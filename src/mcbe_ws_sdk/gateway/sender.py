"""Per-connection outbound send scheduler.

The scheduler is the single owner of the underlying transport callable on the
reliable path. Every outbound frame - handshake, subscribe, command request,
tellraw/scriptevent chunk or addon message - is admitted through :meth:`send`,
which applies:

* bounded capacity (frame count and total UTF-8 bytes);
* priority ordering (control frames before requests before bulk content);
* a per-connection aggregate rate limit measured with an injectable clock;
* explicit failure instead of silent loss, and close semantics that release
  every waiting sender.

Because exactly one worker calls the transport, spawning extra send tasks cannot
bypass the rate limit. A transport failure is propagated to the frame that owned
it, fails the remaining queued frames for that connection, and is isolated from
other connections (each connection has its own scheduler).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

import structlog

from mcbe_ws_sdk.errors import SendClosedError, SendQueueFullError

logger = structlog.get_logger(__name__)

TransportSend = Callable[[str], Awaitable[None]]


class SendPriority(IntEnum):
    """Lower values are sent first."""

    CONTROL = 0
    REQUEST = 1
    BULK = 2


@dataclass(slots=True)
class _Frame:
    payload: str
    source: str
    priority: SendPriority
    byte_size: int
    future: asyncio.Future[None]


class OutboundScheduler:
    """Single-worker, bounded, priority-aware outbound sender for one connection."""

    def __init__(
        self,
        *,
        connection_id: UUID,
        transport: TransportSend,
        max_queue: int = 256,
        max_bytes: int = 4 * 1024 * 1024,
        min_interval: float = 0.0,
        capacity_timeout: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if type(max_queue) is not int or max_queue <= 0:
            raise ValueError("max_queue must be a positive integer")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if min_interval < 0:
            raise ValueError("min_interval must be non-negative")
        if capacity_timeout <= 0:
            raise ValueError("capacity_timeout must be positive")
        self._connection_id = connection_id
        self._transport = transport
        self._max_queue = max_queue
        self._max_bytes = max_bytes
        self._min_interval = min_interval
        self._capacity_timeout = capacity_timeout
        self._clock = clock
        self._sleep = sleep
        self._queue: asyncio.PriorityQueue[tuple[int, int, _Frame]] = asyncio.PriorityQueue()
        self._capacity = asyncio.Condition()
        self._sequence = 0
        self._count = 0
        self._bytes = 0
        self._closed = False
        self._closed_error: BaseException | None = None
        self._worker: asyncio.Task[None] | None = None
        self._last_sent: float | None = None
        self._sent = 0
        self._rejected = 0

    # -- introspection ------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return self._count

    @property
    def pending_bytes(self) -> int:
        return self._bytes

    @property
    def sent_count(self) -> int:
        return self._sent

    @property
    def rejected_count(self) -> int:
        return self._rejected

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def max_queue(self) -> int:
        return self._max_queue

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    # -- sending ------------------------------------------------------------

    async def send(
        self,
        payload: str,
        *,
        priority: SendPriority = SendPriority.REQUEST,
        source: str = "",
    ) -> None:
        """Admit and send one frame, awaiting transport completion.

        Raises:
            SendClosedError: The scheduler is or became closed.
            SendQueueFullError: Capacity was not available in time.
            BaseException: The original transport failure.
        """
        if self._closed:
            raise self._closed_error or SendClosedError()
        frame = _Frame(
            payload=payload,
            source=source,
            priority=priority,
            byte_size=len(payload.encode("utf-8")),
            future=asyncio.get_running_loop().create_future(),
        )
        await self._admit(frame)
        try:
            await frame.future
        except asyncio.CancelledError:
            if not frame.future.done():
                frame.future.cancel()
            raise

    async def send_from_host(self, payload: str) -> None:
        """``ConnectionState.send_payload`` adapter for host-generated frames."""
        await self.send(payload, priority=SendPriority.REQUEST, source="host")

    async def _admit(self, frame: _Frame) -> None:
        self._raise_if_closed()
        self.start()
        async with self._capacity:
            while True:
                self._raise_if_closed()
                if self._has_room(frame):
                    self._count += 1
                    self._bytes += frame.byte_size
                    self._sequence += 1
                    self._queue.put_nowait((int(frame.priority), self._sequence, frame))
                    return
                if frame.priority is SendPriority.BULK:
                    self._rejected += 1
                    raise SendQueueFullError(
                        "send queue full for bulk frame "
                        f"(count={self._count}/{self._max_queue}, "
                        f"bytes={self._bytes}/{self._max_bytes})"
                    )
                try:
                    await asyncio.wait_for(self._capacity.wait(), self._capacity_timeout)
                except TimeoutError as exc:
                    self._rejected += 1
                    raise SendQueueFullError(
                        "timed out waiting for send capacity "
                        f"(count={self._count}/{self._max_queue}, "
                        f"bytes={self._bytes}/{self._max_bytes})"
                    ) from exc

    def _has_room(self, frame: _Frame) -> bool:
        return (
            self._count < self._max_queue
            and self._bytes + frame.byte_size <= self._max_bytes
        )

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise self._closed_error or SendClosedError()

    # -- worker -------------------------------------------------------------

    def start(self) -> None:
        if self._closed or self._worker is not None:
            return
        self._worker = asyncio.create_task(
            self._run(),
            name=f"outbound:{self._connection_id}",
        )

    async def _run(self) -> None:
        while True:
            _priority, _sequence, frame = await self._queue.get()
            try:
                if frame.future.cancelled():
                    continue
                await self._apply_rate_limit()
                await self._transport(frame.payload)
            except asyncio.CancelledError:
                self._fail_frame(frame, self._closed_error or SendClosedError())
                raise
            except BaseException as exc:
                self._fail_frame(frame, exc)
                await self._abort(exc)
                return
            else:
                self._sent += 1
                if not frame.future.done():
                    frame.future.set_result(None)
            finally:
                await self._release(frame)

    async def _apply_rate_limit(self) -> None:
        if self._min_interval <= 0:
            self._last_sent = self._clock()
            return
        now = self._clock()
        if self._last_sent is not None:
            delay = self._min_interval - (now - self._last_sent)
            if delay > 0:
                logger.debug(
                    "outbound_rate_limited",
                    connection_id=str(self._connection_id),
                    delay_seconds=delay,
                )
                await self._sleep(delay)
        self._last_sent = self._clock()

    async def _release(self, frame: _Frame) -> None:
        async with self._capacity:
            self._count = max(0, self._count - 1)
            self._bytes = max(0, self._bytes - frame.byte_size)
            self._capacity.notify_all()

    def _fail_frame(self, frame: _Frame, error: BaseException) -> None:
        if not frame.future.done():
            frame.future.set_exception(error)

    async def _abort(self, error: BaseException) -> None:
        """Fail every queued frame after a transport failure and stop accepting."""
        self._closed_error = error
        async with self._capacity:
            self._closed = True
            self._capacity.notify_all()
        while True:
            try:
                _priority, _sequence, frame = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._fail_frame(frame, error)
            await self._release(frame)
        logger.error(
            "outbound_transport_failed",
            connection_id=str(self._connection_id),
            error_type=type(error).__name__,
        )

    # -- shutdown -----------------------------------------------------------

    async def close(self, error: BaseException | None = None) -> None:
        """Close the scheduler, fail queued frames and release waiting senders."""
        async with self._capacity:
            if self._closed and self._worker is None:
                return
            self._closed = True
            self._closed_error = error or self._closed_error or SendClosedError()
            self._capacity.notify_all()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        while True:
            try:
                _priority, _sequence, frame = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._fail_frame(frame, self._closed_error)
            await self._release(frame)


__all__ = ["OutboundScheduler", "SendPriority", "TransportSend"]
