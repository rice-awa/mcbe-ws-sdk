"""Minimal runnable Minecraft Bedrock WebSocket server.

This example uses the SDK as a transport gateway. It accepts a Bedrock
``/wsserver`` connection, subscribes to ``PlayerMessage`` events, and replies
to ``!`` commands with ``tellraw``. The application-specific behaviour lives in
``ExampleHook`` and ``MinecraftSink``; the SDK owns the WebSocket lifetime and
packet protocol.

Admission is mandatory: the companion MCBEWS/2 addon must be installed in the
world. Without it the server logs ``NEGOTIATION_TIMEOUT`` and closes the
connection; the example itself never calls addon capabilities.

Usage::

    python examples/basic-server/server.py
    python examples/basic-server/server.py --host 0.0.0.0 --port 8080
    python examples/basic-server/server.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from contextlib import suppress

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
    NoOpHook,
    OutboundText,
    ParsedCommand,
    PlayerMessageEvent,
    SystemNotification,
    WebsocketTransportConfig,
    configure_logging,
    enqueue_response,
    is_external_echo,
)

_HELP_TEXT = (
    "基础示例命令：\n"
    "  !help             显示本帮助\n"
    "  !wscmd <command>  通过 WS commandRequest 执行命令并等待响应\n"
    "也支持：!帮助"
)
_USAGE_TEXT = "请在消息开头使用 ! 调用命令，例如 !help 或 !wscmd time query daytime。"
_MAX_REPLY_BYTES = 256

logger = structlog.get_logger("example")


class MinecraftSink(DefaultResponseSink):
    """Deliver SDK response messages as Minecraft WebSocket commands."""

    def __init__(self, flow: FlowControlSettings) -> None:
        self._flow = flow

    def _delivery(self, state: ConnectionState) -> McbeOutboundDelivery | None:
        if state.send_payload is None:
            return None
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            send_bulk=state.send_bulk,
            settings=self._flow,
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


class ExampleHook(NoOpHook):
    """Log lifecycle events and echo each player's chat message."""

    @staticmethod
    def _put(state: ConnectionState, message: object) -> None:
        enqueue_response(state, message)

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
                message="SDK 示例服务器已连接。发送 !help 查看可用命令。",
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
        # Ignore Bedrock's tellraw echo so we don't treat our own outbound text
        # as an inbound player message (and don't reply to ourselves).
        if is_external_echo(player_event.sender, player_event.type):
            logger.debug(
                "external_echo_ignored",
                connection_id=str(state.id),
                sender=player_event.sender,
                length=len(player_event.message),
            )
            return

        message = player_event.message.strip()
        # Only the ASCII ``!`` prefix dispatches. Ordinary chat, bare ``help``
        # and full-width ``！`` stay silent, matching the addon example.
        if not message.startswith("!"):
            return

        logger.info(
            "chat",
            event_name="PlayerMessage",
            sender=player_event.sender,
            message=message,
            message_type=player_event.type,
            parsed_type=parsed.type if parsed is not None else None,
        )
        command, _, argument = message.partition(" ")
        command_key = command.lower()
        argument = argument.strip()
        if message == "!":
            reply = _USAGE_TEXT
        elif command_key in {"!help", "!帮助"}:
            reply = _HELP_TEXT
        elif command_key == "!wscmd":
            if not argument:
                reply = "用法：!wscmd <command>  例如：!wscmd time query daytime"
            elif state.command_client is None:
                reply = "命令失败 [NO_COMMAND_CLIENT] 连接没有命令客户端。"
            else:
                try:
                    response = await state.command_client.run(argument)
                    reply = str(response.body)
                except Exception as exc:
                    reply = f"命令失败 [{type(exc).__name__}] {_bounded_text(str(exc))}"
        else:
            reply = f"未知命令：{_bounded_text(message, 96)}\n发送 !help 查看可用命令。"

        # Use event.sender as the authoritative player identity. Do not use
        # ConnectionState.player_name: one WS connection can carry many players.
        # Prefer @a for the welcome-style broadcast path; for replies, target
        # the real sender so only that player sees the echo in multiplayer.
        self._put(
            state,
            OutboundText(
                content=reply,
                channel="example_reply",
                player_name=player_event.sender,
                target=player_event.sender,
            ),
        )

    async def on_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
        # Successful tellraw/scriptevent acks are noise at INFO; only surface
        # failures (non-zero statusCode) so the console stays readable.
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


def _bounded_text(text: object, limit: int = _MAX_REPLY_BYTES) -> str:
    """Truncate to ``limit`` UTF-8 bytes without splitting a scalar value."""
    value = str(text)
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a minimal MCBE SDK WebSocket server")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    parser.add_argument("--port", default=8080, type=int, help="listen port (default: 8080)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        help="console log level (default: INFO)",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging(args.log_level)

    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(host=args.host, port=args.port),
    )
    facade = McbeServer(
        settings=settings,
        hook=ExampleHook(),
        sink=MinecraftSink(settings.flow),
    )

    logger.info("listening", host=args.host, port=args.port, url=f"ws://{args.host}:{args.port}")
    logger.info(
        "connect_hint",
        command=f"/wsserver <this-machine-ip>:{args.port}",
    )
    logger.info("ready", stop_with="Ctrl+C")
    await facade.run_lifetime()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
