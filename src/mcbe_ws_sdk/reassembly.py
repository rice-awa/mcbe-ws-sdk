"""Generic fragment ordering, duplicate detection, size and lifetime limits."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from mcbe_ws_sdk.errors import ProtocolError, ReassemblyLimitError

T = TypeVar("T")


class ReassemblyLimits(Protocol):
    """Structural resource limits supplied by each connection adapter."""

    @property
    def max_chunks_per_message(self) -> int: ...
    @property
    def max_buffer_ids(self) -> int: ...
    @property
    def max_message_bytes(self) -> int: ...
    @property
    def max_total_buffer_bytes(self) -> int: ...
    @property
    def buffer_ttl_seconds(self) -> float: ...


@dataclass
class ChunkBuffer(Generic[T]):
    """Opaque fragments with byte and lifetime accounting."""

    total_chunks: int
    chunks: dict[int, T]
    byte_size: int
    updated_at: float


class ReassemblyCore(Generic[T]):
    """Share all buffer quotas across channels, without inspecting payload fields.

    Adapters call prune_expired before accept and handle returned expired keys.
    """

    def __init__(
        self,
        limits: ReassemblyLimits,
        *,
        content_of: Callable[[T], str],
        clock: Callable[[], float] = time.monotonic,
        ttl_ticks: float | None = None,
    ) -> None:
        self._settings = limits
        self._clock = clock
        # Adapters may keep their native clock units without lossy conversions.
        self._ttl = limits.buffer_ttl_seconds if ttl_ticks is None else ttl_ticks
        self._content_of = content_of
        self._channels: dict[str, dict[str, ChunkBuffer[T]]] = {}

    def channel(self, name: str) -> dict[str, ChunkBuffer[T]]:
        """Return a channel backed by this core's shared quotas."""
        return self._channels.setdefault(name, {})

    def accept(
        self,
        channel: str,
        *,
        buffer_id: str,
        index: int,
        total: int,
        content: str,
        item: T,
        now: float | None = None,
    ) -> ChunkBuffer[T]:
        """Accept a fragment, ignoring exact duplicates and dropping conflicts."""
        buffers = self.channel(channel)
        timestamp = self._clock() if now is None else now
        if not 1 <= index <= total <= self._settings.max_chunks_per_message:
            raise ReassemblyLimitError("invalid chunk index or total")
        encoded_size = len(content.encode("utf-8"))
        current = buffers.get(buffer_id)
        if current is None:
            if self._buffer_count() >= self._settings.max_buffer_ids:
                raise ReassemblyLimitError("maximum buffer ids exceeded")
            current = ChunkBuffer(total, {}, 0, timestamp)
            buffers[buffer_id] = current
        elif current.total_chunks != total:
            self._drop_buffer(buffers, buffer_id)
            raise ProtocolError("chunk total changed")
        existing = current.chunks.get(index)
        if existing is not None:
            if self._content_of(existing) != content:
                self._drop_buffer(buffers, buffer_id)
                raise ProtocolError("duplicate chunk content changed")
            return current
        if current.byte_size + encoded_size > self._settings.max_message_bytes:
            self._drop_buffer(buffers, buffer_id)
            raise ReassemblyLimitError("message byte limit exceeded")
        if self._total_buffer_bytes() + encoded_size > self._settings.max_total_buffer_bytes:
            self._drop_buffer(buffers, buffer_id)
            raise ReassemblyLimitError("total buffer byte limit exceeded")
        current.chunks[index] = item
        current.byte_size += encoded_size
        current.updated_at = timestamp
        return current

    def prune_expired(self, *, now: float | None = None) -> list[tuple[str, str]]:
        """Release expired fragments and return their channel/key pairs."""
        now = self._clock() if now is None else now
        expired = []
        for channel, buffers in self._channels.items():
            for buffer_id, buffer in list(buffers.items()):
                if now - buffer.updated_at >= self._ttl:
                    buffers.pop(buffer_id)
                    expired.append((channel, buffer_id))
        return expired

    @staticmethod
    def _drop_buffer(buffers: dict[str, ChunkBuffer[T]], buffer_id: str) -> None:
        buffers.pop(buffer_id, None)

    def _buffer_count(self) -> int:
        return sum(len(buffers) for buffers in self._channels.values())

    def _total_buffer_bytes(self) -> int:
        return sum(
            buffer.byte_size for buffers in self._channels.values() for buffer in buffers.values()
        )
