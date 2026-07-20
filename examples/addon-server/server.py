"""Runnable MCBE WebSocket server that exercises built-in addon capabilities.

This example builds on ``examples/basic-server`` and adds chat commands that
call the TypeScript bridge addon through :class:`AddonBridgeService`, plus a
host-owned WebSocket ``commandRequest`` / ``commandResponse`` runner:

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
from uuid import UUID

import structlog

from mcbe_ws_sdk import (
    AddonBridgeService,
    BridgeError,
    BridgeTimeoutError,
    DefaultResponseSink,
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
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)

# Bedrock echoes successful tellraw/say output back as PlayerMessage events
# with sender "外部". Treat those as transport noise, not real player chat.
_EXTERNAL_ECHO_SENDERS = frozenset({"外部", "External"})

# How long to wait for a commandResponse after a WS-side commandRequest.
_WS_COMMAND_TIMEOUT_SECONDS = 5.0

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


class WsCommandRunner:
    """Host-owned request/response tracker for WS ``commandRequest`` frames.

    The SDK deliberately does not keep a ``pending_command_futures`` map on
    :class:`ConnectionState` — that plumbing is host-owned. This helper is the
    minimal pattern:

    1. ``run()`` registers a future under the outbound ``requestId``;
    2. ``resolve()`` is called from ``on_command_response``;
    3. ``close_connection()`` fails any leftovers on disconnect.
    """

    def __init__(
        self,
        flow: FlowControlSettings,
        *,
        timeout: float = _WS_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self._flow = flow
        self._timeout = timeout
        self._pending: dict[UUID, dict[str, asyncio.Future[MinecraftCommandResponse]]] = {}

    def _bucket(
        self,
        connection_id: UUID,
    ) -> dict[str, asyncio.Future[MinecraftCommandResponse]]:
        bucket = self._pending.get(connection_id)
        if bucket is None:
            bucket = {}
            self._pending[connection_id] = bucket
        return bucket

    async def run(
        self,
        state: ConnectionState,
        command: str,
    ) -> MinecraftCommandResponse:
        if state.send_payload is None:
            raise RuntimeError("connection has no send_payload")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[MinecraftCommandResponse] = loop.create_future()
        delivery = McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            settings=self._flow,
        )
        bucket = self._bucket(state.id)

        def _register(request_id: str) -> None:
            bucket[request_id] = future

            def _cleanup(done: asyncio.Future[MinecraftCommandResponse]) -> None:
                if bucket.get(request_id) is done:
                    bucket.pop(request_id, None)

            future.add_done_callback(_cleanup)

        request_id = await delivery.send_raw_command(
            command,
            source="ws_run_command",
            before_send=_register,
        )
        logger.info(
            "ws_command_sent",
            connection_id=str(state.id),
            request_id=request_id,
            command=command,
        )
        try:
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError as exc:
            bucket.pop(request_id, None)
            raise TimeoutError(
                f"WS command timed out after {self._timeout:.0f}s (request_id={request_id})"
            ) from exc

    def resolve(self, state: ConnectionState, response: MinecraftCommandResponse) -> bool:
        """Complete a pending future if this response is tracked. Returns True if handled."""
        bucket = self._pending.get(state.id)
        if not bucket:
            return False
        future = bucket.pop(response.request_id, None)
        if future is None or future.done():
            return False
        future.set_result(response)
        return True

    def close_connection(self, connection_id: UUID) -> None:
        bucket = self._pending.pop(connection_id, None)
        if bucket is None:
            return
        for future in bucket.values():
            if not future.done():
                future.set_exception(ConnectionError("connection closed before commandResponse"))
        bucket.clear()


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
        ws_commands: WsCommandRunner,
        *,
        log_raw_payloads: bool = False,
    ) -> None:
        self._addon = addon
        self._flow = flow
        self._ws_commands = ws_commands
        self._log_raw_payloads = log_raw_payloads

    @staticmethod
    async def _put(state: ConnectionState, message: object) -> None:
        if state.response_queue is not None:
            await state.response_queue.put(message)

    async def _reply(self, state: ConnectionState, player_name: str, content: str) -> None:
        await self._put(
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
        await self._put(
            state,
            SystemNotification(
                level="info",
                message="Addon 示例服务器已连接。发送 !help 查看可用命令。",
            ),
        )

    async def on_disconnected(self, state: ConnectionState) -> None:
        self._ws_commands.close_connection(state.id)
        logger.info("disconnected", connection_id=str(state.id))

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
    ) -> bool:
        if player_event.sender in _EXTERNAL_ECHO_SENDERS:
            logger.debug(
                "external_echo_ignored",
                connection_id=str(state.id),
                sender=player_event.sender,
                length=len(player_event.message),
            )
            return False

        # Surface every non-echo PlayerMessage at INFO while diagnosing bridge
        # timeouts — includes MCBEWS_BRIDGE RESP/UI_CHAT chunks if they arrive.
        logger.info(
            "player_message_raw",
            connection_id=str(state.id),
            sender=player_event.sender,
            message=player_event.message,
            message_type=player_event.type,
            receiver=player_event.receiver,
        )

        message = player_event.message.strip()
        if not message:
            return False

        logger.info(
            "chat",
            event_name="PlayerMessage",
            sender=player_event.sender,
            message=message,
            message_type=player_event.type,
        )

        # CRITICAL: do not await run()/addon request on this stack.
        # McbeServerFacade processes inbound frames with:
        #   async for raw in websocket: await self._handle_raw(...)
        # which awaits on_player_message. If we block here waiting for a
        # commandResponse (or bridge chat chunk), the receive loop cannot
        # read that response frame → permanent timeout even though the game
        # executed the command. Schedule work on a background task instead.
        # Use event.sender as the authoritative player identity. Do not use
        # ConnectionState.player_name: one WS connection can carry many players.
        task = asyncio.create_task(
            self._dispatch_command(state, player_event.sender, message),
            name=f"addon-demo:{player_event.sender}",
        )
        task.add_done_callback(self._log_background_failure)
        return True

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
            await self._reply(state, sender, _HELP_TEXT)
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
                await self._reply(
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
                await self._reply(
                    state,
                    sender,
                    "用法：!wscmd <command>  例如：!wscmd time query daytime",
                )
                return True
            await self._run_ws_command(state, sender, arg)
            return True

        await self._reply(
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
        await self._reply(state, sender, f"正在请求 {label}…")

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
            await self._reply(
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
            await self._reply(state, sender, f"{label} 失败：{exc}")
            return
        except Exception as exc:
            logger.exception(
                "addon_request_failed",
                connection_id=str(state.id),
                capability=capability,
            )
            await self._reply(state, sender, f"{label} 异常：{exc}")
            return

        logger.info(
            "addon_response",
            connection_id=str(state.id),
            capability=capability,
            ok=result.get("ok") if isinstance(result, dict) else None,
            result=result,
        )
        await self._reply(state, sender, format_capability_result(capability, result, label))

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
        await self._reply(state, sender, f"正在执行 {label}…")

        try:
            response = await self._ws_commands.run(state, command)
        except TimeoutError as exc:
            logger.warning(
                "ws_command_timeout",
                connection_id=str(state.id),
                command=command,
                error=str(exc),
            )
            await self._reply(state, sender, f"{label} 超时：未收到 commandResponse。")
            return
        except Exception as exc:
            logger.exception(
                "ws_command_failed",
                connection_id=str(state.id),
                command=command,
            )
            await self._reply(state, sender, f"{label} 异常：{exc}")
            return

        await self._reply(state, sender, format_ws_command_result(label, response))

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

        # Resolve host-tracked WS run_command futures first. Untracked frames
        # (tellraw/scriptevent acks from our own replies) fall through after the
        # raw log above.
        if self._ws_commands.resolve(state, response):
            logger.info(
                "ws_command_response",
                connection_id=str(state.id),
                request_id=response.request_id,
                status_code=response.body.get("statusCode"),
            )
            return

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
        help="log raw outbound WS payloads and inbound commandResponse/PlayerMessage bodies (default: on)",
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

    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(host=args.host, port=args.port),
    )
    # Share one AddonBridgeService between the hook (outbound requests) and the
    # facade (inbound response reassembly). Passing the same instance keeps the
    # pending-request futures on a single session map.
    addon = AddonBridgeService(settings.addon)
    flow = settings.flow
    # WS commandRequest/response correlation is host-owned (not in the SDK).
    ws_commands = WsCommandRunner(flow)
    facade = McbeServerFacade(
        settings=settings,
        hook=AddonDemoHook(addon, flow, ws_commands, log_raw_payloads=args.log_raw),
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
