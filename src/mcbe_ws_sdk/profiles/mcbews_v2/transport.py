"""Opt-in bounded bidirectional carrier; no capability dispatch or negotiation."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcbe_ws_sdk.errors import BridgeLimitError, ProtocolError
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommand
from mcbe_ws_sdk.reassembly import ReassemblyCore

from .codec import LIMITS, MANIFEST, decode, dumps, encode, fail

if TYPE_CHECKING:
    from mcbe_ws_sdk.gateway.sender import OutboundScheduler

PREFIX = MANIFEST["carrier"]["prefix"]
COMMAND_PREFIX = f"scriptevent {MANIFEST['carrier']['event']} "


def limits(overrides: dict[str, int] | None = None) -> dict[str, int]:
    """Return validated deployment limits, never exceeding the contract maxima."""
    result = dict(LIMITS)
    for key, value in (overrides or {}).items():
        if key not in result or type(value) is not int or not 1 <= value <= LIMITS[key]:
            fail("LIMIT_EXCEEDED")
        result[key] = value
    return result


def _identity(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        fail("INVALID_FRAGMENT")


def _transport_id(tid: str, direction: str) -> None:
    if (
        direction not in ("down", "up")
        or not isinstance(tid, str)
        or re.fullmatch(MANIFEST["id_pattern"], tid, re.ASCII) is None
        or tid[0] != ("p" if direction == "down" else "a")
    ):
        fail("INVALID_FRAGMENT")


def _fits(frame: str, direction: str, cfg: dict[str, int]) -> bool:
    if direction == "down":
        return len((COMMAND_PREFIX + frame).encode()) <= cfg["command_bytes"]
    return (
        len(frame) <= cfg["upstream_code_points"] and len(frame.encode()) <= cfg["upstream_bytes"]
    )


def fragment(
    message: object, transport_id: str, direction: str, *, overrides: dict[str, int] | None = None
) -> list[str]:
    """Plan bounded frames with worst-case index overhead before any send."""
    cfg = limits(overrides)
    _transport_id(transport_id, direction)
    wire = encode(message, resource_limits=cfg)
    if len(wire.encode()) > cfg["message_bytes"]:
        fail("LIMIT_EXCEEDED")
    header = f"{PREFIX}|{transport_id}|{cfg['chunks']}/{cfg['chunks']}|"
    parts: list[str] = []
    current = ""
    for ch in wire:
        if _fits(header + current + ch, direction, cfg):
            current += ch
        else:
            if not current or not _fits(header + ch, direction, cfg):
                fail("LIMIT_EXCEEDED")
            parts.append(current)
            current = ch
    parts.append(current)
    if len(parts) > cfg["chunks"]:
        fail("LIMIT_EXCEEDED")
    frames = [f"{PREFIX}|{transport_id}|{i}/{len(parts)}|{part}" for i, part in enumerate(parts, 1)]
    if not all(_fits(frame, direction, cfg) for frame in frames):
        fail("LIMIT_EXCEEDED")
    return frames


def downstream_payloads(
    message: object, transport_id: str, *, overrides: dict[str, int] | None = None
) -> list[str]:
    """Build final commandRequests for the existing per-connection send scheduler."""
    return [
        MinecraftCommand.create_scriptevent(frame, MANIFEST["carrier"]["event"]).model_dump_json(
            exclude_none=True
        )
        for frame in fragment(message, transport_id, "down", overrides=overrides)
    ]


@dataclass(frozen=True)
class _CoreLimits:
    max_chunks_per_message: int
    max_buffer_ids: int
    max_message_bytes: int
    max_total_buffer_bytes: int
    buffer_ttl_seconds: float


class ReassemblyPool:
    """One shared runtime pool; owners supply trusted connection/source attribution.

    Call prune on the lifecycle tick, close on disconnect, and use fresh epochs.
    Receive returns a complete logical value at most once within the tombstone TTL.
    """

    def __init__(
        self,
        *,
        overrides: dict[str, int] | None = None,
        clock_ms: Callable[[], float] = lambda: time.monotonic() * 1000,
    ) -> None:
        self.cfg = limits(overrides)
        self._clock = clock_ms
        self._core = ReassemblyCore[str](
            _CoreLimits(
                self.cfg["chunks"],
                self.cfg["global_buffers"],
                self.cfg["message_bytes"],
                self.cfg["global_bytes"],
                self.cfg["ttl_ms"] / 1000,
            ),
            content_of=lambda item: item,
            clock=self._clock,
            ttl_ticks=self.cfg["ttl_ms"],
        )
        self._buffers = self._core.channel("v2")
        self._owners: dict[str, tuple[str, str, str]] = {}
        self._done: dict[str, tuple[str, float]] = {}

    @property
    def stats(self) -> dict[str, int]:
        """Current buffer, byte and tombstone counts (without mutating the pool)."""
        return {
            "buffers": len(self._buffers),
            "bytes": sum(b.byte_size for b in self._buffers.values()),
            "tombstones": len(self._done),
        }

    def _drop(self, key: str, now: float | None = None) -> None:
        owner = self._owners.pop(key, None)
        self._buffers.pop(key, None)
        if owner:
            self._done[key] = (
                owner[0],
                (self._clock() if now is None else now) + self.cfg["ttl_ms"],
            )

    def prune(self) -> None:
        """Release idle messages and elapsed tombstones using the supplied clock."""
        self._prune(self._clock())

    def _prune(self, now: float) -> None:
        for key, (_, expiry) in list(self._done.items()):
            if now >= expiry:
                del self._done[key]
        for _, key in self._core.prune_expired(now=now):
            self._drop(key, now)

    def cancel(self, connection: str, source: str, transport_id: str) -> None:
        """Cancel one active transfer without affecting another source or connection."""
        self._drop(dumps([connection, source, transport_id]))

    def close(self, connection: str) -> None:
        """Release a disconnected connection, including its duplicate suppression state."""
        for key, owner in list(self._owners.items()):
            if owner[0] == connection:
                self._drop(key)
        for key, (conn, _) in list(self._done.items()):
            if conn == connection:
                del self._done[key]

    def receive(
        self, connection: str, source: str, frame: str, direction: str
    ) -> dict[str, Any] | None:
        """Accept a carrier frame without invoking any application behavior."""
        now = self._clock()
        self._prune(now)
        _identity(connection)
        _identity(source)
        if direction not in ("up", "down") or not isinstance(frame, str):
            fail("INVALID_FRAGMENT")
        pieces = frame.split("|", 3)
        if len(pieces) != 4 or pieces[0] != PREFIX:
            fail("INVALID_FRAGMENT")
        _, tid, indexes, content = pieces
        _transport_id(tid, direction)
        key = dumps([connection, source, tid])
        try:
            valid_frame = _fits(frame, direction, self.cfg)
        except UnicodeError:
            valid_frame = False
        if not valid_frame:
            self._drop(key, now)
            fail("INVALID_FRAGMENT")
        if key in self._done:
            return None
        if re.fullmatch(r"[1-9][0-9]{0,3}/[1-9][0-9]{0,3}", indexes) is None:
            self._drop(key, now)
            fail("INVALID_FRAGMENT")
        index, total = map(int, indexes.split("/"))
        if not content or not 1 <= index <= total <= self.cfg["chunks"]:
            self._drop(key, now)
            fail("INVALID_FRAGMENT")
        if key not in self._owners:
            if len(self._owners) + len(self._done) >= self.cfg["tombstones"]:
                fail("LIMIT_EXCEEDED")
            self._owners[key] = (connection, source, tid)
        relevant = [(k, b) for k, b in self._buffers.items() if self._owners[k][0] == connection]
        same_source = [(k, b) for k, b in relevant if self._owners[k][1] == source]
        current = self._buffers.get(key)
        extra = 0 if current and index in current.chunks else len(content.encode())
        for entries, count_limit, byte_limit in (
            (relevant, "connection_buffers", "connection_bytes"),
            (same_source, "source_buffers", "source_bytes"),
        ):
            count = len(entries) + (0 if current else 1)
            if (
                count > self.cfg[count_limit]
                or sum(b.byte_size for _, b in entries) + extra > self.cfg[byte_limit]
            ):
                self._drop(key, now)
                fail("LIMIT_EXCEEDED")
        try:
            buffer = self._core.accept(
                "v2",
                buffer_id=key,
                index=index,
                total=total,
                content=content,
                item=content,
                now=now,
            )
        except BridgeLimitError:
            self._drop(key, now)
            fail("LIMIT_EXCEEDED")
        except ProtocolError:
            self._drop(key, now)
            fail("FRAGMENT_CONFLICT")
        if len(buffer.chunks) != total:
            return None
        wire = "".join(buffer.chunks[i] for i in range(1, total + 1))
        self._drop(key, now)
        return decode(wire, resource_limits=self.cfg)


async def send_downstream(
    scheduler: OutboundScheduler,
    message: object,
    transport_id: str,
    *,
    overrides: dict[str, int] | None = None,
) -> None:
    """Send one frame at a time through the existing fair connection scheduler.

    Planning fails before the first send; transport failure or cancellation stops
    remaining fragments. Already sent fragments expire at the receiver.
    """
    from mcbe_ws_sdk.gateway.sender import SendPriority

    for payload in downstream_payloads(message, transport_id, overrides=overrides):
        await scheduler.send(payload, source=transport_id, priority=SendPriority.BULK)
