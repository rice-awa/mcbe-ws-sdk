"""Runnable MCBE WebSocket server that exercises built-in addon capabilities.

This example builds on ``examples/basic-server`` and adds chat commands that
call the TypeScript bridge addon through :class:`AddonBridgeService`, plus the
SDK-owned awaitable ``commandRequest`` / ``commandResponse`` client
(:attr:`ConnectionState.command_client`):

* ``!player [target]`` → ``get_player_snapshot``
* ``!inv [target]`` → ``get_inventory_snapshot``
* ``!cmd <command>`` → ``run_world_command`` (must be enabled in the addon)
* ``!wscmd <command>`` → WS-side ``commandRequest`` (no addon required)
* ``!help`` → list available commands

Usage::

    python examples/addon-server/server.py
    python examples/addon-server/server.py --host 0.0.0.0 --port 8080
    python examples/addon-server/server.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from contextlib import suppress
from typing import Any

import structlog

from mcbe_ws_sdk import (
    AddonBridgeService,
    BridgeError,
    BridgeTimeoutError,
    DefaultResponseSink,
    DispatchSettings,
    FlowControlSettings,
    GatewaySettings,
    McbeOutboundDelivery,
    McbeServerFacade,
    NoOpHook,
    OutboundText,
    SystemNotification,
    WebsocketTransportConfig,
    configure_logging,
)
from mcbe_ws_sdk.command.registry import ParsedCommand
from mcbe_ws_sdk.gateway.connection import ConnectionState, enqueue_response
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)

# Bedrock echoes successful tellraw/say output back as PlayerMessage events
# with sender "外部". Treat those as transport noise, not real player chat.
_EXTERNAL_ECHO_SENDERS = frozenset({"外部", "External"})

_HELP_TEXT = (
    "Addon 示例命令：\n"
    "  !player [target]  获取玩家快照（默认自己）\n"
    "  !inv [target]     获取背包快照（默认自己）\n"
    "  !cmd <command>    通过 addon 执行世界命令（需在 addon 侧启用）\n"
    "  !wscmd <command>  通过 WS commandRequest 执行命令（不依赖 addon）\n"
    "  !help             显示本帮助\n"
    "也支持：!玩家 / !背包 / !命令 / !ws命令 / 帮助"
)

logger = structlog.get_logger("example")


class MinecraftSink(DefaultResponseSink):
    """Deliver SDK response messages as Minecraft WebSocket commands."""

    def __init__(self, flow: FlowControlSettings, *, log_raw_payloads: bool = False) -> None:
        self._flow = flow
        self._log_raw_payloads = log_raw_payloads

    def _delivery(self, state: ConnectionState) -> McbeOutboundDelivery | None:
        if state.send_payload is None:
            return None
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            send_bulk=state.send_bulk,
            settings=self._flow,
            log_raw_payloads=self._log_raw_payloads,
        )

    async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
        delivery = self._delivery(state)
        if delivery is not None:
            await delivery.send_outbound_text(message)

    async def on_system_notification(
        self,
        state: ConnectionState,
        message: SystemNotification,
    ) -> None:
        delivery = self._delivery(state)
        if delivery is not None:
            await delivery.send_system_notification(message)


class AddonDemoHook(NoOpHook):
    """Chat-command demo for addon capabilities and WS-side commandRequest."""

    def __init__(
        self,
        addon: AddonBridgeService,
        flow: FlowControlSettings,
        *,
        log_raw_payloads: bool = False,
    ) -> None:
        self._addon = addon
        self._flow = flow
        self._log_raw_payloads = log_raw_payloads

    @staticmethod
    def _put(state: ConnectionState, message: object) -> None:
        enqueue_response(state, message)

    def _reply(self, state: ConnectionState, player_name: str, content: str) -> None:
        self._put(
            state,
            OutboundText(
                content=content,
                channel="addon_demo",
                player_name=player_name,
                target=player_name,
            ),
        )

    def _bridge_client(self, state: ConnectionState):
        if state.send_payload is None:
            raise BridgeError("connection has no send_payload")
        delivery = McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            send_bulk=state.send_bulk,
            settings=self._flow,
            log_raw_payloads=self._log_raw_payloads,
        )

        async def send_command(command: str) -> None:
            await delivery.send_raw_command(command, source="addon_bridge")

        return self._addon.create_client(state.id, send_command)

    async def on_connected(self, state: ConnectionState) -> None:
        logger.info(
            "connected",
            connection_id=str(state.id),
            subscribed_events=["PlayerMessage"],
        )
        self._put(
            state,
            SystemNotification(
                level="info",
                message="Addon 示例服务器已连接。发送 !help 查看可用命令。",
            ),
        )

    async def on_disconnected(self, state: ConnectionState) -> None:
        logger.info("disconnected", connection_id=str(state.id))

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: ParsedCommand | None = None,
    ) -> None:
        if player_event.sender in _EXTERNAL_ECHO_SENDERS:
            logger.debug(
                "external_echo_ignored",
                connection_id=str(state.id),
                sender=player_event.sender,
                length=len(player_event.message),
            )
            return

        # Surface every non-echo PlayerMessage at INFO while diagnosing bridge
        # timeouts — includes MCBEWS_BRIDGE BRIDGE/UI_CHAT chunks if they arrive.
        logger.info(
            "player_message_raw",
            connection_id=str(state.id),
            sender=player_event.sender,
            message=player_event.message,
            message_type=player_event.type,
            receiver=player_event.receiver,
            parsed_type=parsed.type if parsed is not None else None,
        )

        message = player_event.message.strip()
        if not message:
            return

        logger.info(
            "chat",
            event_name="PlayerMessage",
            sender=player_event.sender,
            message=message,
            message_type=player_event.type,
        )

        # Use event.sender as the authoritative player identity. Do not use
        # ConnectionState.player_name: one WS connection can carry many players.
        #
        # Reliable dispatch already runs this callback in an SDK-owned business
        # task, so it can await a bridge reply directly. The legacy path still
        # awaits the callback inside the reader; there we must not block on a
        # reply that only the reader can deliver, so schedule a background task.
        if state.runtime is not None:
            await self._dispatch_command(state, player_event.sender, message)
            return

        task = asyncio.create_task(
            self._dispatch_command(state, player_event.sender, message),
            name=f"addon-demo:{player_event.sender}",
        )
        task.add_done_callback(self._log_background_failure)

    @staticmethod
    def _log_background_failure(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "background_command_failed",
                task=task.get_name(),
                error=str(exc),
                exc_info=exc,
            )

    async def _dispatch_command(
        self,
        state: ConnectionState,
        sender: str,
        message: str,
    ) -> bool:
        lower = message.lower()
        if lower in {"!help", "帮助", "help"}:
            self._reply(state, sender, _HELP_TEXT)
            return True

        command, _, rest = message.partition(" ")
        command_key = command.lower()
        arg = rest.strip()

        if command_key in {"!player", "!玩家"}:
            target = arg or sender
            await self._run_capability(
                state,
                sender,
                capability="get_player_snapshot",
                payload={"target": target},
                label=f"玩家快照 ({target})",
            )
            return True

        if command_key in {"!inv", "!背包"}:
            target = arg or sender
            await self._run_capability(
                state,
                sender,
                capability="get_inventory_snapshot",
                payload={"target": target},
                label=f"背包快照 ({target})",
            )
            return True

        if command_key in {"!cmd", "!命令"}:
            if not arg:
                self._reply(
                    state,
                    sender,
                    "用法：!cmd <command>  例如：!cmd time query daytime",
                )
                return True
            await self._run_capability(
                state,
                sender,
                capability="run_world_command",
                payload={"command": arg},
                label=f"addon 世界命令 ({arg})",
            )
            return True

        if command_key in {"!wscmd", "!ws命令"}:
            if not arg:
                self._reply(
                    state,
                    sender,
                    "用法：!wscmd <command>  例如：!wscmd time query daytime",
                )
                return True
            await self._run_ws_command(state, sender, arg)
            return True

        self._reply(
            state,
            sender,
            f"未知命令：{message}\n发送 !help 查看可用命令。",
        )
        return True

    async def _run_capability(
        self,
        state: ConnectionState,
        sender: str,
        *,
        capability: str,
        payload: dict[str, Any],
        label: str,
    ) -> None:
        logger.info(
            "addon_request",
            connection_id=str(state.id),
            sender=sender,
            capability=capability,
            payload=payload,
        )
        self._reply(state, sender, f"正在请求 {label}…")

        try:
            client = self._bridge_client(state)
            result = await client.request(capability, payload)
        except BridgeTimeoutError as exc:
            logger.warning(
                "addon_timeout",
                connection_id=str(state.id),
                capability=capability,
                request_id=exc.request_id,
            )
            self._reply(
                state,
                sender,
                f"{label} 超时。请确认世界已加载 bridge addon，且 /wsserver 仍在线。",
            )
            return
        except BridgeError as exc:
            logger.warning(
                "addon_bridge_error",
                connection_id=str(state.id),
                capability=capability,
                error=str(exc),
            )
            self._reply(state, sender, f"{label} 失败：{exc}")
            return
        except Exception as exc:
            logger.exception(
                "addon_request_failed",
                connection_id=str(state.id),
                capability=capability,
            )
            self._reply(state, sender, f"{label} 异常：{exc}")
            return

        logger.info(
            "addon_response",
            connection_id=str(state.id),
            capability=capability,
            ok=result.get("ok") if isinstance(result, dict) else None,
            result=result,
        )
        self._reply(state, sender, format_capability_result(capability, result, label))

    async def _run_ws_command(
        self,
        state: ConnectionState,
        sender: str,
        command: str,
    ) -> None:
        label = f"WS 命令 ({command})"
        logger.info(
            "ws_command_request",
            connection_id=str(state.id),
            sender=sender,
            command=command,
        )
        self._reply(state, sender, f"正在执行 {label}…")

        if state.command_client is None:
            self._reply(state, sender, f"{label} 失败：连接没有命令客户端。")
            return
        try:
            # The SDK registers the request before sending and pairs the
            # commandResponse; no host-side wait table is needed.
            response = await state.command_client.run(command)
        except TimeoutError as exc:
            logger.warning(
                "ws_command_timeout",
                connection_id=str(state.id),
                command=command,
                error=str(exc),
            )
            self._reply(state, sender, f"{label} 超时：未收到 commandResponse。")
            return
        except Exception as exc:
            logger.exception(
                "ws_command_failed",
                connection_id=str(state.id),
                command=command,
            )
            self._reply(state, sender, f"{label} 异常：{exc}")
            return

        self._reply(state, sender, format_ws_command_result(label, response))

    async def on_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
        # Always log the raw commandResponse while diagnosing bridge timeouts.
        # Successful tellraw/scriptevent acks are normally noise, but right now we
        # need to see whether scriptevent mcbews:bridge_req was accepted.
        logger.info(
            "command_response_raw",
            connection_id=str(state.id),
            request_id=response.request_id,
            status_code=response.body.get("statusCode"),
            status_message=response.body.get("statusMessage"),
            body=response.body,
        )

        # The SDK already resolved its tracked native command request before
        # invoking this hook. Remaining frames are tellraw/scriptevent acks.
        status = response.body.get("statusCode")
        if status not in (None, 0):
            logger.warning(
                "command_failed",
                connection_id=str(state.id),
                request_id=response.request_id,
                status_code=status,
                status_message=response.body.get("statusMessage"),
            )

    async def on_error(self, state: ConnectionState, error: MinecraftErrorFrame) -> None:
        logger.error(
            "minecraft_error",
            connection_id=str(state.id),
            request_id=error.request_id,
            body=error.body,
        )


def format_capability_result(capability: str, result: Any, label: str) -> str:
    """Turn a bridge response into a short, readable tellraw body."""
    if not isinstance(result, dict):
        return f"{label}\n{json.dumps(result, ensure_ascii=False)}"

    if result.get("ok") is False:
        error = result.get("error")
        if isinstance(error, dict):
            code = error.get("code", "ERROR")
            message = error.get("message", "")
            hint = ""
            if code == "UNSUPPORTED_CAPABILITY" and capability == "run_world_command":
                hint = (
                    "\n提示：run_world_command 默认未注册。"
                    "请在 addon 侧把 handleRunWorldCommand 挂进 registry，"
                    "或通过 setCapabilityHandler 启用。"
                )
            return f"{label} 失败 [{code}] {message}{hint}".rstrip()
        if "payload" in result and isinstance(result["payload"], dict):
            output = result["payload"].get("output")
            if output:
                return f"{label} 失败：{output}"
        return f"{label} 失败：{json.dumps(result, ensure_ascii=False)}"

    payload = result.get("payload", result)

    if capability == "get_player_snapshot":
        return _format_player_snapshot(label, payload)
    if capability == "get_inventory_snapshot":
        return _format_inventory_snapshot(label, payload)
    if capability == "run_world_command":
        return _format_world_command(label, payload)

    return f"{label}\n{json.dumps(payload, ensure_ascii=False)}"


def _format_player_snapshot(label: str, payload: Any) -> str:
    players = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(players, list):
        return f"{label}\n{json.dumps(payload, ensure_ascii=False)}"
    if not players:
        return f"{label}\n未找到玩家。"

    lines = [label]
    for player in players:
        if not isinstance(player, dict):
            lines.append(f"- {player}")
            continue
        loc = player.get("location") or {}
        coords = f"{loc.get('x', '?')}, {loc.get('y', '?')}, {loc.get('z', '?')}"
        tags = player.get("tags") or []
        tag_text = ", ".join(str(t) for t in tags) if tags else "(无)"
        lines.append(
            f"- {player.get('name', '?')}  "
            f"HP={player.get('health')}  "
            f"mode={player.get('gameMode')}  "
            f"dim={player.get('dimension')}  "
            f"pos=({coords})  "
            f"tags={tag_text}"
        )
    return "\n".join(lines)


def _format_inventory_snapshot(label: str, payload: Any) -> str:
    inventories = payload.get("inventories") if isinstance(payload, dict) else None
    if not isinstance(inventories, list):
        return f"{label}\n{json.dumps(payload, ensure_ascii=False)}"
    if not inventories:
        return f"{label}\n未找到玩家背包。"

    lines = [label]
    for inv in inventories:
        if not isinstance(inv, dict):
            lines.append(f"- {inv}")
            continue
        items = inv.get("items") or []
        lines.append(f"- {inv.get('player', '?')}  size={inv.get('size', 0)}  items={len(items)}")
        # Cap item lines so tellraw stays readable; full JSON is still logged.
        for item in items[:20]:
            if not isinstance(item, dict):
                lines.append(f"    {item}")
                continue
            name = item.get("nameTag") or item.get("typeId")
            lines.append(f"    slot {item.get('slot')}: {name} x{item.get('amount')}")
        if len(items) > 20:
            lines.append(f"    …另有 {len(items) - 20} 个物品未展开")
    return "\n".join(lines)


def _format_world_command(label: str, payload: Any) -> str:
    if not isinstance(payload, dict):
        return f"{label}\n{json.dumps(payload, ensure_ascii=False)}"
    output = payload.get("output", "")
    success = payload.get("successCount", 0)
    return f"{label}\noutput={output}\nsuccessCount={success}"


def format_ws_command_result(label: str, response: MinecraftCommandResponse) -> str:
    """Summarise a Minecraft commandResponse body for tellraw."""
    body = response.body if isinstance(response.body, dict) else {}
    status = body.get("statusCode")
    message = body.get("statusMessage") or ""
    if status in (None, 0):
        detail = message or "命令执行成功"
        return f"{label}\nstatusCode=0\n{detail}"
    detail = message or "(no statusMessage)"
    return f"{label} 失败\nstatusCode={status}\n{detail}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an MCBE SDK server with addon capability demos",
    )
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    parser.add_argument("--port", default=8080, type=int, help="listen port (default: 8080)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        help="console log level (default: INFO)",
    )
    parser.add_argument(
        "--log-raw",
        action="store_true",
        default=True,
        help=(
            "log raw outbound WS payloads and inbound "
            "commandResponse/PlayerMessage bodies (default: on)"
        ),
    )
    parser.add_argument(
        "--no-log-raw",
        action="store_false",
        dest="log_raw",
        help="disable raw payload logging",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging(args.log_level)

    # The reliable path is explicit. It keeps the reader free while a handler
    # awaits a capability reply, gives each player its own FIFO lane, and routes
    # long text through the scheduler's bulk category. ``DispatchSettings()``
    # alone still selects the legacy serial path.
    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(host=args.host, port=args.port),
        dispatch=DispatchSettings(mode="reliable"),
    )
    # Share one AddonBridgeService between the hook (outbound requests) and the
    # facade (inbound response reassembly). Passing the same instance keeps the
    # pending-request futures on a single session map.
    addon = AddonBridgeService(settings.addon)
    flow = settings.flow
    facade = McbeServerFacade(
        settings=settings,
        hook=AddonDemoHook(addon, flow, log_raw_payloads=args.log_raw),
        sink=MinecraftSink(flow, log_raw_payloads=args.log_raw),
        addon=addon,
    )

    logger.info("listening", host=args.host, port=args.port, url=f"ws://{args.host}:{args.port}")
    logger.info(
        "connect_hint",
        command=f"/wsserver <this-machine-ip>:{args.port}",
    )
    logger.info(
        "addon_hint",
        note="world must load the bridge addon for !player/!inv/!cmd; !wscmd needs only /wsserver",
    )
    logger.info("ready", stop_with="Ctrl+C")
    await facade.run_lifetime()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
