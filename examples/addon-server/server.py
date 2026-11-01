"""Runnable MCBE WebSocket server that exercises built-in addon capabilities.

This example builds on ``examples/basic-server`` and adds chat commands that
call the TypeScript bridge addon through :class:`V2Bridge`, plus the
SDK-owned awaitable ``commandRequest`` / ``commandResponse`` client
(:attr:`ConnectionState.command_client`):

* ``!player [target]`` → ``get_player_snapshot``
* ``!inv [target]`` → ``get_inventory_snapshot``
* ``!cmd <command>`` → ``run_world_command`` (included in the default addon)
* ``!wscmd <command>`` → WS-side ``commandRequest`` (no addon capability call)
* ``!echo <text>`` → custom capability and metadata
* ``!stream [count]`` → continuously produced items and normal completion
* ``!stream-cancel [count]`` → early consumer exit cancels an unlimited producer
* ``!help`` → list available commands

Only messages that start with ASCII ``!`` after trimming are dispatched;
ordinary chat is silent. The addon's default build ships every capability this
server calls; ``--base`` builds register only the two read-only snapshots.
Enable the world's Beta APIs experiment before loading the addon.

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
    ConnectionState,
    DefaultResponseSink,
    FlowControlSettings,
    GatewaySettings,
    McbeOutboundDelivery,
    McbeServer,
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    NativeEvent,
    NegotiationError,
    NoOpHook,
    OutboundText,
    ParsedCommand,
    PlayerMessageEvent,
    RemoteCallError,
    StreamError,
    SystemNotification,
    V2Bridge,
    WebsocketTransportConfig,
    configure_logging,
    enqueue_response,
    is_external_echo,
)

_HELP_TEXT = (
    "Addon 示例命令：\n"
    "  !player [target]  获取玩家快照（默认自己）\n"
    "  !inv [target]     获取背包快照（默认自己）\n"
    "  !cmd <command>    通过 addon 以自己为上下文执行（命令禁止列表生效）\n"
    "  !wscmd <command>  通过 WS commandRequest 执行命令（不调用 addon 能力）\n"
    "  !echo <text>      自定义能力与元数据\n"
    "  !stream [count]   持续流直到正常结束（默认5项）\n"
    "  !stream-cancel [count]  消费后取消无限流（默认3项）\n"
    "  !help             显示本帮助\n"
    "也支持：!玩家 / !背包 / !命令 / !ws命令 / !帮助"
)
_USAGE_TEXT = "请在消息开头使用 ! 调用命令，例如 !help 或 !player。"
_MAX_ERROR_BYTES = 256

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

    def __init__(self, addon: V2Bridge) -> None:
        self._addon = addon

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

    async def on_connected(self, state: ConnectionState) -> None:
        await state.native_events.subscribe("PlayerMessage", self._observe_chat)
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

    @staticmethod
    def _observe_chat(event: NativeEvent) -> None:
        logger.debug("native_event", event_name=event.name)

    async def on_disconnected(self, state: ConnectionState) -> None:
        logger.info("disconnected", connection_id=str(state.id))

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: ParsedCommand | None = None,
    ) -> None:
        if is_external_echo(player_event.sender, player_event.type):
            logger.debug(
                "external_echo_ignored",
                connection_id=str(state.id),
                sender=player_event.sender,
                length=len(player_event.message),
            )
            return

        message = player_event.message.strip()
        # Chat without the ASCII command prefix is never dispatched: ordinary
        # conversation, bare ``help`` and full-width ``！`` stay silent.
        if not message.startswith("!"):
            return

        logger.info(
            "chat",
            event_name="PlayerMessage",
            sender=player_event.sender,
            message=message,
            message_type=player_event.type,
        )

        await self._dispatch_command(state, player_event.sender, message)

    async def _dispatch_command(
        self,
        state: ConnectionState,
        sender: str,
        message: str,
    ) -> bool:
        lower = message.lower()
        if lower in {"!help", "!帮助"}:
            self._reply(state, sender, _HELP_TEXT)
            return True
        if message == "!":
            self._reply(state, sender, _USAGE_TEXT)
            return True

        command, _, rest = message.partition(" ")
        command_key = command.lower()
        arg = rest.strip()

        if command_key == "!echo":
            await self._run_capability(
                state,
                sender,
                capability="demo.echo",
                payload={"text": arg},
                label="自定义回声",
                metadata={"example.label": "chat-demo", "example.player": sender},
            )
            return True

        if command_key in {"!stream", "!stream-cancel"}:
            try:
                count = int(arg or ("3" if command_key == "!stream-cancel" else "5"))
                if not 1 <= count <= 100:
                    raise ValueError
            except ValueError:
                self._reply(state, sender, "用法：!stream [1..100] / !stream-cancel [1..100]")
                return True
            await self._run_stream(state, sender, count, cancel=command_key == "!stream-cancel")
            return True

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
                payload={"command": arg, "player": sender},
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
            f"未知命令：{_bounded_text(message, 96)}\n发送 !help 查看可用命令。",
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
        metadata: dict[str, Any] | None = None,
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
            response = await self._addon.request(state.id, capability, payload, metadata=metadata)
            result = response["result"]
            if metadata is not None:
                self._reply(state, sender, f"返回元数据：{response.get('meta', {})}")
        except RemoteCallError as exc:
            logger.warning(
                "addon_remote_error",
                connection_id=str(state.id),
                capability=capability,
                request_id=exc.request_id,
                code=exc.code,
                message=exc.message,
            )
            self._reply(state, sender, _format_remote_error(label, capability, exc))
            return
        except NegotiationError as exc:
            logger.warning(
                "addon_negotiation_error",
                connection_id=str(state.id),
                capability=capability,
                reason=exc.reason,
                detail=str(exc),
            )
            self._reply(
                state,
                sender,
                f"{label} 失败 [{exc.reason}] {_bounded_text(str(exc))}{_action_hint(capability)}",
            )
            return
        except Exception:
            logger.exception(
                "addon_request_failed",
                connection_id=str(state.id),
                capability=capability,
            )
            self._reply(
                state,
                sender,
                f"{label} 失败 [INTERNAL_ERROR] 服务器内部错误，请查看服务器日志。"
                f"{_action_hint(capability)}",
            )
            return

        logger.info(
            "addon_response",
            connection_id=str(state.id),
            capability=capability,
            ok=result.get("ok") if isinstance(result, dict) else None,
            result=result,
        )
        self._reply(state, sender, format_capability_result(capability, result, label))

    async def _run_stream(
        self, state: ConnectionState, sender: str, count: int, *, cancel: bool
    ) -> None:
        try:
            params = {} if cancel else {"count": count}
            stream = await self._addon.stream(state.id, "demo.ticks", params)
            received = 0
            async with stream:
                async for item in stream:
                    self._reply(state, sender, f"流数据：{item}")
                    received += 1
                    if cancel and received == count:
                        break
            self._reply(state, sender, "流已取消。" if cancel else "流已结束。")
        except RemoteCallError as exc:
            logger.warning(
                "addon_stream_remote_error",
                connection_id=str(state.id),
                method="demo.ticks",
                request_id=exc.request_id,
                code=exc.code,
                message=exc.message,
            )
            self._reply(state, sender, _format_remote_error("流 demo.ticks", "demo.ticks", exc))
        except NegotiationError as exc:
            logger.warning(
                "addon_stream_negotiation_error",
                connection_id=str(state.id),
                method="demo.ticks",
                reason=exc.reason,
                detail=str(exc),
            )
            self._reply(
                state,
                sender,
                f"流失败 [{exc.reason}] {_bounded_text(str(exc))}{_action_hint('demo.ticks')}",
            )
        except StreamError as exc:
            logger.warning(
                "addon_stream_remote_error",
                connection_id=str(state.id),
                method="demo.ticks",
                code=exc.code,
                message=exc.message,
            )
            self._reply(
                state,
                sender,
                f"流失败 [{exc.code}] {_bounded_text(exc.message)}{_action_hint('demo.ticks')}",
            )
        except Exception:
            logger.exception(
                "addon_stream_failed",
                connection_id=str(state.id),
                method="demo.ticks",
            )
            self._reply(
                state,
                sender,
                "流失败 [INTERNAL_ERROR] 服务器内部错误，请查看服务器日志。"
                f"{_action_hint('demo.ticks')}",
            )

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
            logger.info(
                "ws_command_response",
                connection_id=str(state.id),
                command=command,
                request_id=response.request_id,
                status_code=response.body.get("statusCode"),
            )
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
            self._reply(
                state,
                sender,
                f"{label} 失败 [{type(exc).__name__}] {_bounded_text(str(exc))}",
            )
            return

        self._reply(state, sender, format_ws_command_result(label, response))

    async def on_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
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


def _bounded_text(text: object, limit: int = _MAX_ERROR_BYTES) -> str:
    """Truncate to ``limit`` UTF-8 bytes without splitting a scalar value."""
    value = str(text)
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def _action_hint(capability: str) -> str:
    if capability == "run_world_command":
        return (
            "\n提示：该命令在 addon 的命令禁止列表内，"
            "或当前附加包是 --base 精简构建（不含世界命令能力）。"
        )
    if capability == "demo.ticks":
        return "\n提示：确认附加包为默认演示构建，并启用 stream/1 能力。"
    return "\n提示：确认 addon 已注册该能力，并检查命令参数。"


def _format_remote_error(label: str, capability: str, exc: RemoteCallError) -> str:
    """Render a public remote failure; the request id stays in server logs."""
    code = exc.code if isinstance(exc.code, str) and exc.code else "ERROR"
    return f"{label} 失败 [{code}] {_bounded_text(exc.message)}{_action_hint(capability)}"


def format_capability_result(capability: str, result: Any, label: str) -> str:
    """Turn a bridge response into a short, readable tellraw body."""
    if not isinstance(result, dict):
        return f"{label}\n{_bounded_text(json.dumps(result, ensure_ascii=False))}"

    if result.get("ok") is False:
        error = result.get("error")
        if isinstance(error, dict):
            code = error.get("code", "ERROR")
            message = _bounded_text(error.get("message", ""))
            hint = _action_hint(capability) if code == "UNSUPPORTED_CAPABILITY" else ""
            return f"{label} 失败 [{code}] {message}{hint}".rstrip()
        if "payload" in result and isinstance(result["payload"], dict):
            output = result["payload"].get("output")
            if output:
                return f"{label} 失败：{_bounded_text(output)}"
        return f"{label} 失败：{_bounded_text(json.dumps(result, ensure_ascii=False))}"

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
        default=False,
        help="log raw outbound WS payloads (default: off; payloads may contain player data)",
    )
    parser.add_argument(
        "--no-log-raw",
        action="store_false",
        dest="log_raw",
        help="disable raw payload logging (the default)",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging(args.log_level)

    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(host=args.host, port=args.port),
    )
    bridge = V2Bridge()
    facade = McbeServer(
        settings=settings,
        hook=AddonDemoHook(bridge),
        sink=MinecraftSink(settings.flow, log_raw_payloads=args.log_raw),
        v2=bridge,
    )

    logger.info("listening", host=args.host, port=args.port, url=f"ws://{args.host}:{args.port}")
    logger.info(
        "connect_hint",
        command=f"/wsserver <this-machine-ip>:{args.port}",
    )
    logger.info(
        "addon_hint",
        note="Enable Beta APIs and load the addon; the default demo build includes "
        "!cmd/!echo/!stream. Both examples need the addon for protocol negotiation; "
        "!wscmd uses native WebSocket commands.",
    )
    logger.info("ready", stop_with="Ctrl+C")
    await facade.run_lifetime()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
