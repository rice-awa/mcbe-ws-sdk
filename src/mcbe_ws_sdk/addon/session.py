"""Addon bridge session management."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar
from uuid import uuid4

from mcbe_ws_sdk.config import AddonBridgeSettings
from mcbe_ws_sdk.errors import BridgeClosedError, BridgeLimitError, ProtocolError
from mcbe_ws_sdk.profiles.mcbews_v1.codec import (
    decode_bridge_chat_chunk,
    decode_ui_chat_chunk,
    reassemble_bridge_chunks,
    reassemble_ui_chat_chunks,
)
from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    AddonBridgeChunk,
    UiChatChunk,
    UiChatMessage,
)


class _ChunkWithContent(Protocol):
    content: str


T = TypeVar("T", bound=_ChunkWithContent)


@dataclass
class PendingAddonRequest:
    """Pending state for a single bridge request."""

    request_id: str
    capability: str
    payload: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]


@dataclass
class ChunkBuffer(Generic[T]):
    total_chunks: int
    chunks: dict[int, T]
    byte_size: int
    updated_at: float


class AddonBridgeSession:
    """Maintain bridge requests and fragment buffers for one connection."""

    def __init__(
        self,
        settings: AddonBridgeSettings,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._profile = settings.profile
        self._clock = clock
        self._pending_requests: dict[str, PendingAddonRequest] = {}
        self._chunk_buffers: dict[str, ChunkBuffer[AddonBridgeChunk]] = {}
        self._ui_chat_chunk_buffers: dict[str, ChunkBuffer[UiChatChunk]] = {}

    def create_request(
        self,
        capability: str,
        payload: dict[str, Any],
    ) -> PendingAddonRequest:
        """Create and register a pending request."""
        if len(self._pending_requests) >= self._settings.max_pending_requests:
            raise BridgeLimitError("maximum pending requests exceeded")
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
            chunk = decode_bridge_chat_chunk(chunk_message, profile=self._profile)
        except ValueError as exc:
            protocol_error = ProtocolError(str(exc))
            request_id = self._pending_bridge_request_id(chunk_message)
            if request_id is not None and request_id in self._pending_requests:
                self._fail_bridge_request(request_id, protocol_error)
            raise protocol_error from exc
        if chunk.request_id not in self._pending_requests:
            return chunk

        try:
            buffer = self._accept_chunk(
                self._chunk_buffers,
                buffer_id=chunk.request_id,
                index=chunk.chunk_index,
                total=chunk.total_chunks,
                content=chunk.content,
                item=chunk,
            )
        except (BridgeLimitError, ProtocolError) as error:
            self._fail_bridge_request(chunk.request_id, error)
            raise
        if set(buffer.chunks) != set(range(1, buffer.total_chunks + 1)):
            return chunk

        complete = self._chunk_buffers.pop(chunk.request_id)
        try:
            response = reassemble_bridge_chunks(list(complete.chunks.values()))
        except ValueError as exc:
            protocol_error = ProtocolError(str(exc))
            self._fail_bridge_request(chunk.request_id, protocol_error)
            raise protocol_error from exc

        request = self._pending_requests.pop(chunk.request_id, None)
        if request is not None and not request.future.done():
            request.future.set_result(response.payload)
        return chunk

    def _fail_bridge_request(self, request_id: str, error: Exception) -> None:
        request = self._pending_requests.pop(request_id, None)
        self._chunk_buffers.pop(request_id, None)
        if request is not None and not request.future.done():
            request.future.set_exception(error)

    def _pending_bridge_request_id(self, chunk_message: str) -> str | None:
        parts = chunk_message.split("|", 4)
        if len(parts) != 5:
            return None
        namespace, prefix, request_id, _, _ = parts
        bridge_prefix = self._profile.bridge_response_prefix.split("|", 1)
        if len(bridge_prefix) != 2:
            return None
        expected_namespace, expected_prefix = bridge_prefix
        if namespace != expected_namespace or prefix != expected_prefix or not request_id:
            return None
        return request_id

    def cancel_request(self, request_id: str) -> None:
        request = self._pending_requests.pop(request_id, None)
        self._chunk_buffers.pop(request_id, None)
        if request is not None and not request.future.done():
            request.future.cancel()

    def close(self) -> None:
        """Close the session, failing every pending request."""
        pending_ids = list(self._pending_requests)
        for request_id in pending_ids:
            request = self._pending_requests.pop(request_id, None)
            self._chunk_buffers.pop(request_id, None)
            if request is not None and not request.future.done():
                request.future.set_exception(BridgeClosedError(request_id))
        self._ui_chat_chunk_buffers.clear()

    def handle_ui_chat_chunk(self, chunk_message: str) -> tuple[UiChatChunk, UiChatMessage | None]:
        """Consume a UI chat fragment; return the chunk and a completed message if available."""
        try:
            chunk = decode_ui_chat_chunk(chunk_message, profile=self._profile)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc

        buffer = self._accept_chunk(
            self._ui_chat_chunk_buffers,
            buffer_id=chunk.msg_id,
            index=chunk.chunk_index,
            total=chunk.total_chunks,
            content=chunk.content,
            item=chunk,
        )
        if set(buffer.chunks) != set(range(1, buffer.total_chunks + 1)):
            return chunk, None

        complete = self._ui_chat_chunk_buffers.pop(chunk.msg_id)
        try:
            ui_message = reassemble_ui_chat_chunks(list(complete.chunks.values()))
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        return chunk, ui_message

    def _accept_chunk(
        self,
        buffers: dict[str, ChunkBuffer[T]],
        *,
        buffer_id: str,
        index: int,
        total: int,
        content: str,
        item: T,
    ) -> ChunkBuffer[T]:
        self._prune_if_needed()
        if not 1 <= index <= total <= self._settings.max_chunks_per_message:
            raise BridgeLimitError("invalid chunk index or total")
        encoded_size = len(content.encode("utf-8"))
        current = buffers.get(buffer_id)
        if current is None:
            if self._buffer_count() >= self._settings.max_buffer_ids:
                raise BridgeLimitError("maximum buffer ids exceeded")
            current = ChunkBuffer(total, {}, 0, self._clock())
            buffers[buffer_id] = current
        elif current.total_chunks != total:
            self._drop_buffer(buffers, buffer_id)
            raise ProtocolError("chunk total changed")
        existing = current.chunks.get(index)
        if existing is not None:
            if self._chunk_content(existing) != content:
                self._drop_buffer(buffers, buffer_id)
                raise ProtocolError("duplicate chunk content changed")
            return current
        if current.byte_size + encoded_size > self._settings.max_message_bytes:
            self._drop_buffer(buffers, buffer_id)
            raise BridgeLimitError("message byte limit exceeded")
        if self._total_buffer_bytes() + encoded_size > self._settings.max_total_buffer_bytes:
            self._drop_buffer(buffers, buffer_id)
            raise BridgeLimitError("total buffer byte limit exceeded")
        current.chunks[index] = item
        current.byte_size += encoded_size
        current.updated_at = self._clock()
        return current

    def _prune_expired(self) -> None:
        now = self._clock()
        ttl = self._settings.buffer_ttl_seconds
        for buffer_id, bridge_buffer in list(self._chunk_buffers.items()):
            if now - bridge_buffer.updated_at >= ttl:
                self._drop_buffer(self._chunk_buffers, buffer_id)
                self._fail_bridge_request(buffer_id, BridgeLimitError("chunk buffer expired"))
        for buffer_id, ui_buffer in list(self._ui_chat_chunk_buffers.items()):
            if now - ui_buffer.updated_at >= ttl:
                self._drop_buffer(self._ui_chat_chunk_buffers, buffer_id)

    def _prune_if_needed(self) -> None:
        """Prune expired buffers when approaching resource limits."""
        total = len(self._chunk_buffers) + len(self._ui_chat_chunk_buffers)
        if total >= self._settings.max_buffer_ids * 0.75:
            self._prune_expired()

    @staticmethod
    def _drop_buffer(
        buffers: dict[str, ChunkBuffer[T]],
        buffer_id: str,
    ) -> None:
        buffers.pop(buffer_id, None)

    def _buffer_count(self) -> int:
        return len(self._chunk_buffers) + len(self._ui_chat_chunk_buffers)

    def _total_buffer_bytes(self) -> int:
        return sum(buffer.byte_size for buffer in self._chunk_buffers.values()) + sum(
            buffer.byte_size for buffer in self._ui_chat_chunk_buffers.values()
        )

    @staticmethod
    def _chunk_content(chunk: _ChunkWithContent) -> str:
        return chunk.content
