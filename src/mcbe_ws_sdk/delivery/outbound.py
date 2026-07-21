"""MCBE outbound delivery adapter."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.flow import FlowControlMiddleware
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification

logger = structlog.get_logger(__name__)
ws_raw_logger = structlog.get_logger("websocket.raw")
PayloadSender = Callable[[str], Awaitable[None]]


class McbeOutboundDelivery:
    """Chunk, throttle, send, and optionally log MCBE ``commandRequest`` payloads."""

    def __init__(
        self,
        *,
        connection_id: Any,
        send_payload: PayloadSender,
        settings: FlowControlSettings,
        log_raw_payloads: bool = False,
    ) -> None:
        self.connection_id = connection_id
        self._send_payload = send_payload
        self._flow = FlowControlMiddleware(settings)
        self._log_raw_payloads = log_raw_payloads

    @property
    def flow(self) -> FlowControlMiddleware:
        return self._flow

    async def send_payload(self, payload: str, source: str) -> None:
        """Send an already-built payload (subscribe, handshake, non-chunked paths)."""
        await self._send_one(payload, source)

    async def send_tellraw(
        self,
        message: str,
        color: str,
        source: str,
        target: str = "@a",
    ) -> int:
        """Send tellraw text and return the number of payloads actually transmitted."""
        payloads = self.flow.chunk_tellraw(message, color=color, target=target)
        await self.send_chunked(payloads, "tellraw", source)
        return len(payloads)

    async def send_scriptevent(
        self,
        content: str,
        message_id: str = "server:data",
        source: str = "scriptevent",
    ) -> int:
        """Send scriptevent text and return the number of payloads actually transmitted."""
        payloads = self.flow.chunk_scriptevent(content, message_id)
        await self.send_chunked(payloads, "scriptevent", source)
        return len(payloads)

    async def send_raw_command(
        self,
        command: str,
        source: str = "raw_command",
        before_send: Callable[[str], None] | None = None,
    ) -> str:
        """Send a raw command that must not be semantically split.

        Raises ``FrameTooLargeError`` when the command exceeds the byte budget.
        """
        payload = self.flow.chunk_raw_command(command)[0]
        request_id = _request_id_from_payload(payload)
        if before_send is not None:
            before_send(request_id)
        await self._send_one(payload, source)
        return request_id

    async def send_outbound_text(self, message: OutboundText) -> int:
        """Route an OutboundText message through the delivery adapter."""
        source = f"outbound_text:{message.channel}"
        if message.delivery == "scriptevent":
            return await self.send_scriptevent(
                message.content, message_id=message.message_id, source=source
            )
        target = message.target or message.player_name or "@a"
        return await self.send_tellraw(message.content, color="", source=source, target=target)

    async def send_system_notification(self, message: SystemNotification) -> int:
        """Route a SystemNotification through the delivery adapter."""
        colors = {"info": "§b", "warning": "§e", "error": "§c"}
        return await self.send_tellraw(
            message.message,
            color=colors[message.level],
            source="system_notification",
            target=message.player_name or "@a",
        )

    async def send_chunked(
        self,
        payloads: list[str],
        delay_kind: str,
        source: str,
        *,
        delay: float | None = None,
    ) -> None:
        """Send ``payloads`` with inter-chunk delays.

        When ``delay`` is provided it overrides ``flow.chunk_delay_for(delay_kind)``
        so protocol profiles can inject their own cadence (e.g.
        ``profile.response_chunk_delay``).
        """
        chunk_delay = self.flow.chunk_delay_for(delay_kind) if delay is None else delay
        for idx, payload in enumerate(payloads):
            await self._send_one(
                payload,
                source=f"{source}_chunked" if len(payloads) > 1 else source,
            )
            if len(payloads) > 1 and idx < len(payloads) - 1:
                await asyncio.sleep(chunk_delay)

    async def _send_one(self, payload: str, source: str) -> None:
        await self._send_payload(payload)
        self._log_ws_send(payload, source)

    def _log_ws_send(self, payload: str, source: str) -> None:
        request_id = ""
        message_purpose = ""
        command_line = ""
        try:
            data = json.loads(payload)
            header = data.get("header", {})
            request_id = str(header.get("requestId", ""))
            message_purpose = str(header.get("messagePurpose", ""))
            command_line = str(data.get("body", {}).get("commandLine", ""))
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        # Per-frame send metadata is high-volume at DEBUG. When the host opts into
        # log_raw_payloads, promote both the summary and the full payload to INFO so
        # a normal console run (examples default to INFO) can diagnose bridge timeouts.
        log_method = ws_raw_logger.info if self._log_raw_payloads else ws_raw_logger.debug
        log_method(
            "websocket_response_sent",
            connection_id=str(self.connection_id),
            source=source,
            request_id=request_id,
            message_purpose=message_purpose,
            command_type=command_line.partition(" ")[0] if command_line else "",
            command_line=command_line if self._log_raw_payloads else None,
            command_line_length=len(command_line),
            command_line_bytes=len(command_line.encode("utf-8")),
        )

        if self._log_raw_payloads:
            log_method("websocket_response_payload", payload=payload)


def _request_id_from_payload(payload: str) -> str:
    data: Any = json.loads(payload)
    return str(data["header"]["requestId"])
