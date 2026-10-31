"""Addon bridge session management."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

from mcbe_ws_sdk.config import AddonBridgeSettings
from mcbe_ws_sdk.errors import (
    BridgeClosedError,
    BridgeLimitError,
    ProtocolError,
    RequestLimitError,
)
from mcbe_ws_sdk.profiles.mcbews_v1.bridge_codec import (
    decode_bridge_chat_chunk,
    reassemble_bridge_chunks,
)
from mcbe_ws_sdk.profiles.mcbews_v1.bridge_models import AddonBridgeChunk
from mcbe_ws_sdk.reassembly import ChunkBuffer, ReassemblyCore

if TYPE_CHECKING:
    from mcbe_ws_sdk.extensions.mcbews_v1.models import UiChatChunk, UiChatMessage

from dataclasses import replace

from mcbe_ws_sdk.requests import CATEGORY_ADDON, PendingRequest, RequestTable
from mcbe_ws_sdk.tracing import current_trace


class _ChunkWithContent(Protocol):
    content: str


class PendingAddonRequest:
    """Compatibility value for a pending addon bridge request.

    Two forms are supported so the shared request table could be introduced
    without breaking the pre-existing public shape:

    * ``PendingAddonRequest(entry)`` - the session's view over a
      :class:`~mcbe_ws_sdk.requests.PendingRequest` in the shared table.
    * ``PendingAddonRequest(request_id, capability, payload, future)`` - the
      original four-argument constructor, including keyword use. Such an
      instance is standalone (``entry`` is ``None``).

    Attributes are plain and mutable, matching the original dataclass contract;
    assignment does not retarget a shared entry.
    """

    entry: PendingRequest | None

    def __init__(
        self,
        request_id: str | PendingRequest,
        capability: str | None = None,
        payload: dict[str, Any] | None = None,
        future: asyncio.Future[Any] | None = None,
    ) -> None:
        if isinstance(request_id, PendingRequest):
            if capability is not None or payload is not None or future is not None:
                raise TypeError(
                    "PendingAddonRequest(entry) accepts no capability/payload/future arguments"
                )
            metadata = request_id.metadata
            metadata_payload = metadata.get("payload", {})
            self.entry = request_id
            self.request_id = request_id.request_id
            self.capability = str(metadata.get("capability", ""))
            self.payload = metadata_payload if isinstance(metadata_payload, dict) else {}
            self.future = request_id.future
            return

        if capability is None or payload is None or future is None:
            raise TypeError(
                "PendingAddonRequest requires (request_id, capability, payload, future) "
                "or a single PendingRequest entry"
            )
        self.entry = None
        self.request_id = request_id
        self.capability = capability
        self.payload = payload
        self.future = future


class AddonBridgeSession:
    """Maintain bridge requests and fragment buffers for one connection."""

    def __init__(
        self,
        settings: AddonBridgeSettings,
        *,
        clock: Callable[[], float] = time.monotonic,
        extensions: bool = True,
    ) -> None:
        self._settings = settings
        self._profile = settings.profile
        self._clock = clock
        self._extensions = extensions
        self._requests = RequestTable(
            max_pending=settings.max_pending_requests,
            clock=clock,
            name=f"addon:{id(self):x}",
        )
        self._reassembly = ReassemblyCore[_ChunkWithContent](
            settings, content_of=lambda chunk: chunk.content, clock=clock
        )
        self._chunk_buffers = self._reassembly.channel("bridge")
        self._ui_chat_chunk_buffers = self._reassembly.channel("ui_chat")

    @property
    def _pending_requests(self) -> dict[str, PendingRequest]:
        """Live bridge pending requests (compatibility/inspection view)."""
        return self._requests.entries(CATEGORY_ADDON)

    def create_request(
        self,
        capability: str,
        payload: dict[str, Any],
    ) -> PendingAddonRequest:
        """Register a pending request before its outbound frame is sent."""
        try:
            entry = self._requests.reserve(
                CATEGORY_ADDON,
                request_id=f"addon-{uuid4().hex}",
                metadata={"capability": capability, "payload": payload},
            )
        except RequestLimitError as exc:
            raise BridgeLimitError("maximum pending requests exceeded") from exc
        entry.trace = replace(current_trace(), request_id=entry.request_id)
        return PendingAddonRequest(entry)

    def handle_chat_chunk(self, chunk_message: str) -> AddonBridgeChunk:
        """Consume a chat fragment; resolve its request future once complete."""
        try:
            chunk = decode_bridge_chat_chunk(chunk_message, profile=self._profile)
        except ValueError as exc:
            protocol_error = ProtocolError(str(exc))
            request_id = self._pending_bridge_request_id(chunk_message)
            pending = (
                self._requests.get(CATEGORY_ADDON, request_id) if request_id is not None else None
            )
            if request_id is not None and pending is not None:
                self._fail_bridge_request(request_id, protocol_error)
            raise protocol_error from exc
        if self._requests.get(CATEGORY_ADDON, chunk.request_id) is None:
            return chunk

        try:
            buffer = self._accept_chunk(
                "bridge",
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
            response = reassemble_bridge_chunks(
                cast(list[AddonBridgeChunk], list(complete.chunks.values()))
            )
        except ValueError as exc:
            protocol_error = ProtocolError(str(exc))
            self._fail_bridge_request(chunk.request_id, protocol_error)
            raise protocol_error from exc

        # A duplicate or late fragment finds no pending entry and is ignored;
        # it can never revive an already terminal request.
        self._requests.resolve(CATEGORY_ADDON, chunk.request_id, response.payload)
        return chunk

    def _fail_bridge_request(self, request_id: str, error: Exception) -> None:
        self._chunk_buffers.pop(request_id, None)
        self._requests.fail(CATEGORY_ADDON, request_id, error)

    def _pending_bridge_request_id(self, chunk_message: str) -> str | None:
        parts = chunk_message.split("|", 4)
        if len(parts) != 5:
            return None
        namespace, prefix, request_id, _, _ = parts
        bridge_prefix = self._profile.capability_response_chat_prefix.split("|", 1)
        if len(bridge_prefix) != 2:
            return None
        expected_namespace, expected_prefix = bridge_prefix
        if namespace != expected_namespace or prefix != expected_prefix or not request_id:
            return None
        return request_id

    def cancel_request(self, request_id: str) -> None:
        entry = self._requests.get(CATEGORY_ADDON, request_id)
        self._chunk_buffers.pop(request_id, None)
        if entry is not None:
            self._requests.cancel(entry)

    def close(self) -> None:
        """Close the session, failing every pending request exactly once."""
        self._requests.close(lambda request_id: BridgeClosedError(request_id))
        self._chunk_buffers.clear()
        self._ui_chat_chunk_buffers.clear()

    def handle_ui_chat_chunk(self, chunk_message: str) -> tuple[UiChatChunk, UiChatMessage | None]:
        """Consume a UI chat fragment; return the chunk and a completed message if available."""
        if not self._extensions:
            raise ProtocolError("UI chat extension is disabled")
        from mcbe_ws_sdk.extensions.mcbews_v1.codec import (
            decode_ui_chat_chunk,
            reassemble_ui_chat_chunks,
        )

        try:
            chunk = decode_ui_chat_chunk(chunk_message, profile=self._profile)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc

        buffer = self._accept_chunk(
            "ui_chat",
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
            ui_message = reassemble_ui_chat_chunks(
                cast("list[UiChatChunk]", list(complete.chunks.values()))
            )
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        return chunk, ui_message

    def _accept_chunk(
        self,
        channel: str,
        *,
        buffer_id: str,
        index: int,
        total: int,
        content: str,
        item: _ChunkWithContent,
    ) -> ChunkBuffer[_ChunkWithContent]:
        self._prune_expired()
        return self._reassembly.accept(
            channel, buffer_id=buffer_id, index=index, total=total, content=content, item=item
        )

    def _prune_expired(self) -> None:
        for channel, buffer_id in self._reassembly.prune_expired():
            if channel == "bridge":
                self._fail_bridge_request(buffer_id, BridgeLimitError("chunk buffer expired"))

    def _buffer_count(self) -> int:
        return self._reassembly._buffer_count()

    def _total_buffer_bytes(self) -> int:
        return self._reassembly._total_buffer_bytes()
