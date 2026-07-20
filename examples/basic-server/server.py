"""Minimal runnable Minecraft Bedrock WebSocket server.

This example uses the SDK as a transport gateway. It accepts a Bedrock
``/wsserver`` connection, subscribes to ``PlayerMessage`` events, and replies
to chat messages with ``tellraw``. The application-specific behaviour lives in
``ExampleHook`` and ``MinecraftSink``; the SDK owns the WebSocket lifetime and
packet protocol.

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
        logger.info(
            "connected",
            connection_id=str(state.id),
            subscribed_events=["PlayerMessage"],
        )
        self._put(
            state,
            SystemNotification(
                level="info",
                message="SDK 示例服务器已连接。发送任意聊天消息即可收到回复。",
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
        # Ignore Bedrock's tellraw echo so we don't treat our own outbound text
        # as an inbound player message (and don't reply to ourselves).
        if player_event.sender in _EXTERNAL_ECHO_SENDERS:
            logger.debug(
                "external_echo_ignored",
                connection_id=str(state.id),
                sender=player_event.sender,
                length=len(player_event.message),
            )
            return

        message = player_event.message.strip()
        if not message:
            return

        logger.info(
            "chat",
            event_name="PlayerMessage",
            sender=player_event.sender,
            message=message,
            message_type=player_event.type,
            parsed_type=parsed.type if parsed is not None else None,
        )
        if message.lower() in {"!help", "帮助"}:
            reply = "可用示例：直接发送聊天消息，服务器会把消息回复给你。"
        else:
            reply = f"收到 {player_event.sender} 的消息：{message}"

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
    facade = McbeServerFacade(
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
