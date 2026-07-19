"""Addon bridge session management.

Relocated from the main repo ``services/addon/session.py``.

The session now receives its :class:`AddonProtocolConfig` at construction and
threads it into every codec decode call. This removes the old implicit
``_protocol()`` global read (which depended on a running settings singleton) and
keeps configuration explicit and per-session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import structlog

from mcbe_ws_sdk.addon.protocol import (
    decode_bridge_chat_chunk,
    decode_ui_chat_chunk,
    reassemble_bridge_chunks,
    reassemble_ui_chat_chunks,
)
from mcbe_ws_sdk.config import AddonProtocolConfig
from mcbe_ws_sdk.errors import ProtocolError
from mcbe_ws_sdk.protocol.addon import AddonBridgeChunk, UiChatChunk, UiChatMessage

logger = structlog.get_logger(__name__)


@dataclass
class PendingAddonRequest:
    """Pending state for a single bridge request."""

    request_id: str
    capability: str
    payload: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]


class AddonBridgeSession:
    """Maintain bridge requests and fragment buffers for one connection."""

    def __init__(self, protocol: AddonProtocolConfig | None = None) -> None:
        self._protocol = protocol if protocol is not None else AddonProtocolConfig()
        self._pending_requests: dict[str, PendingAddonRequest] = {}
        self._chunk_buffers: dict[str, dict[int, AddonBridgeChunk]] = {}
        self._ui_chat_chunk_buffers: dict[str, dict[int, UiChatChunk]] = {}

    def create_request(
        self,
        capability: str,
        payload: dict[str, Any],
    ) -> PendingAddonRequest:
        """Create and register a pending request."""
        loop = asyncio.get_running_loop()
        request = PendingAddonRequest(
            request_id=f"addon-{uuid4().hex}",
            capability=capability,
            payload=payload,
            future=loop.create_future(),
        )
        self._pending_requests[request.request_id] = request
        return request

    def handle_chat_chunk(self, chunk_message: str) -> AddonBridgeChunk:
        """Consume a chat fragment; resolve its request future once complete."""
        try:
            chunk = decode_bridge_chat_chunk(chunk_message, protocol=self._protocol)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        if chunk.request_id not in self._pending_requests:
            return chunk

        buffer = self._chunk_buffers.setdefault(chunk.request_id, {})
        buffer[chunk.chunk_index] = chunk

        if len(buffer) < chunk.total_chunks:
            return chunk

        try:
            response = reassemble_bridge_chunks(list(buffer.values()))
        except ValueError as exc:
            self._chunk_buffers.pop(chunk.request_id, None)
            raise ProtocolError(str(exc)) from exc

        request = self._pending_requests.pop(chunk.request_id)
        self._chunk_buffers.pop(chunk.request_id, None)
        if not request.future.done():
            request.future.set_result(response.payload)
        return chunk

    def fail_request(self, request_id: str, reason: str) -> None:
        """Fail and drop a pending request."""
        request = self._pending_requests.pop(request_id, None)
        self._chunk_buffers.pop(request_id, None)
        if request and not request.future.done():
            request.future.set_exception(RuntimeError(reason))

    def close(self, reason: str) -> None:
        """Close the session, failing every pending request."""
        pending_ids = list(self._pending_requests)
        for request_id in pending_ids:
            self.fail_request(request_id, reason)
        self._ui_chat_chunk_buffers.clear()

    def handle_ui_chat_chunk(self, chunk_message: str) -> tuple[UiChatChunk, UiChatMessage | None]:
        """Consume a UI chat fragment; return the chunk and a completed message if available."""
        try:
            chunk = decode_ui_chat_chunk(chunk_message, protocol=self._protocol)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc

        buffer = self._ui_chat_chunk_buffers.setdefault(chunk.msg_id, {})
        buffer[chunk.chunk_index] = chunk

        logger.debug(
            "ui_chat_chunk_buffered",
            msg_id=chunk.msg_id,
            chunk_index=chunk.chunk_index,
            total_chunks=chunk.total_chunks,
            buffered=len(buffer),
        )

        if len(buffer) < chunk.total_chunks:
            return chunk, None

        try:
            ui_msg = reassemble_ui_chat_chunks(list(buffer.values()))
        except ValueError as e:
            self._ui_chat_chunk_buffers.pop(chunk.msg_id, None)
            raise ProtocolError(str(e)) from e
        finally:
            self._ui_chat_chunk_buffers.pop(chunk.msg_id, None)

        logger.info(
            "ui_chat_reassemble_success",
            msg_id=ui_msg.msg_id,
            player=ui_msg.player_name,
            message_length=len(ui_msg.message),
        )

        return chunk, ui_msg

