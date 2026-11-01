"""Per-connection outbound send scheduler.

The scheduler is the single owner of the underlying transport callable on the
reliable path. Every outbound frame - handshake, subscribe, command request,
tellraw/scriptevent chunk or addon message - is admitted through :meth:`send`,
which applies:

* bounded capacity (frame count and total UTF-8 bytes) with a bounded
  reservation that keeps room for control frames even while bulk content fills
  the queue;
* priority ordering (control frames before requests before bulk content) with a
  bounded fairness quota so sustained higher-priority traffic cannot starve
  lower priorities forever;
* a per-connection aggregate rate limit measured with an injectable clock;
* a single total admission deadline (later notifications do not restart it);
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
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from contextvars import Context, copy_context
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

import structlog

from mcbe_ws_sdk.errors import SendClosedError, SendQueueFullError
from mcbe_ws_sdk.tracing import observe

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
    context: Context


class OutboundScheduler:
    """Single-worker, bounded, priority-aware outbound sender for one connection.

    Args:
        connection_id: Owning connection, used only for structured logging.
        transport: The only callable allowed to touch the underlying transport.
        max_queue: Maximum admitted frames (queued plus in-flight).
        max_bytes: Maximum admitted UTF-8 bytes (queued plus in-flight).
        min_interval: Minimum spacing between transport calls. ``0`` disables
            rate limiting.
        capacity_timeout: Total time an admitted-send may wait for capacity.
        control_reserve_slots: Frames that non-control priorities may not occupy,
            clamped so at least one non-control frame still fits.
        control_reserve_bytes: Bytes non-control priorities may not occupy,
            clamped so at least one byte still fits.
        fairness_quota: Consecutive higher-priority selections after which a
            waiting lower-priority frame is sent once. Must be positive.
        clock: Monotonic clock used for rate limiting and the admission deadline.
        sleep: Sleep used for rate limiting (injectable for simulated clocks).
    """

    def __init__(
        self,
        *,
        connection_id: UUID,
        transport: TransportSend,
        max_queue: int = 256,
        max_bytes: int = 4 * 1024 * 1024,
        min_interval: float = 0.0,
        capacity_timeout: float = 5.0,
        control_reserve_slots: int = 1,
        control_reserve_bytes: int = 4096,
        fairness_quota: int = 8,
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
        if type(control_reserve_slots) is not int or control_reserve_slots < 0:
            raise ValueError("control_reserve_slots must be a non-negative integer")
        if type(control_reserve_bytes) is not int or control_reserve_bytes < 0:
            raise ValueError("control_reserve_bytes must be a non-negative integer")
        if type(fairness_quota) is not int or fairness_quota <= 0:
            raise ValueError("fairness_quota must be a positive integer")
        self._connection_id = connection_id
        self._transport = transport
        self._max_queue = max_queue
        self._max_bytes = max_bytes
        self._min_interval = min_interval
        self._capacity_timeout = capacity_timeout
        self._clock = clock
        self._sleep = sleep
        self._fairness_quota = fairness_quota
        # A reservation that would leave no room for non-control frames is
        # clamped to leave one frame/byte, so small test configurations and
        # deployments that intentionally run with a tiny queue still work.
        self._control_reserve_slots = min(control_reserve_slots, max_queue - 1)
        self._control_reserve_bytes = min(control_reserve_bytes, max_bytes - 1)
        self._non_control_queue_limit = max_queue - self._control_reserve_slots
        self._non_control_byte_limit = max_bytes - self._control_reserve_bytes

        self._pending: dict[SendPriority, deque[_Frame]] = {
            priority: deque() for priority in SendPriority
        }
        # Consecutive higher-priority selections since the last frame of each
        # priority was served. Used by the bounded fairness rule.
        self._starvation: dict[SendPriority, int] = dict.fromkeys(SendPriority, 0)
        self._capacity = asyncio.Condition()
        self._count = 0
        self._bytes = 0
        self._closed = False
        self._closed_error: BaseException | None = None
        self._worker: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._last_sent: float | None = None
        self._sent = 0
        self._rejected = 0
        self._sent_by_priority: dict[SendPriority, int] = dict.fromkeys(SendPriority, 0)

    # -- introspection ------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Admitted frames, including the frame currently in flight."""
        return self._count

    @property
    def pending_bytes(self) -> int:
        return self._bytes

    @property
    def pending_by_priority(self) -> dict[SendPriority, int]:
        """Queued frames per priority, excluding the in-flight frame."""
        return {priority: len(frames) for priority, frames in self._pending.items()}

    @property
    def sent_count(self) -> int:
        return self._sent

    @property
    def sent_by_priority(self) -> dict[SendPriority, int]:
        return dict(self._sent_by_priority)

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

    @property
    def control_reserve_slots(self) -> int:
        return self._control_reserve_slots

    @property
    def control_reserve_bytes(self) -> int:
        return self._control_reserve_bytes

    @property
    def fairness_quota(self) -> int:
        return self._fairness_quota

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
            SendQueueFullError: Capacity was not available before the total
                admission deadline.
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
            context=copy_context(),
        )
        await self._admit(frame)
        observe("enqueued", source=source, byte_size=frame.byte_size)
        try:
            await frame.future
        except asyncio.CancelledError:
            if not frame.future.done():
                frame.future.cancel()
            raise

    async def send_from_host(self, payload: str) -> None:
        """``ConnectionState.send_payload`` adapter for host-generated frames.

        Host code that only knows the legacy ``Callable[[str], Awaitable[None]]``
        contract keeps working; those frames are treated as requests. Callers
        that know the content is bulk (long tellraw/scriptevent text) should use
        :meth:`ConnectionRuntime.send_bulk_from_host` or pass an explicit
        priority.
        """
        await self.send(payload, priority=SendPriority.REQUEST, source="host")

    async def _admit(self, frame: _Frame) -> None:
        self._raise_if_closed()
        self.start()
        # One deadline for the whole admission attempt. Repeated notifications
        # from other senders must not restart the timeout.
        deadline = self._clock() + self._capacity_timeout
        async with self._capacity:
            while True:
                self._raise_if_closed()
                if self._has_room(frame):
                    self._count += 1
                    self._bytes += frame.byte_size
                    self._pending[frame.priority].append(frame)
                    self._capacity.notify_all()
                    return
                if frame.priority is SendPriority.BULK:
                    self._rejected += 1
                    raise SendQueueFullError(
                        "send queue full for bulk frame "
                        f"(count={self._count}/{self._max_queue}, "
                        f"bytes={self._bytes}/{self._max_bytes})"
                    )
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._rejected += 1
                    raise SendQueueFullError(
                        "timed out waiting for send capacity "
                        f"(count={self._count}/{self._max_queue}, "
                        f"bytes={self._bytes}/{self._max_bytes})"
                    )
                try:
                    await asyncio.wait_for(self._capacity.wait(), remaining)
                except TimeoutError as exc:
                    self._rejected += 1
                    raise SendQueueFullError(
                        "timed out waiting for send capacity "
                        f"(count={self._count}/{self._max_queue}, "
                        f"bytes={self._bytes}/{self._max_bytes})"
                    ) from exc

    def _has_room(self, frame: _Frame) -> bool:
        if self._count >= self._max_queue or self._bytes + frame.byte_size > self._max_bytes:
            return False
        if frame.priority is SendPriority.CONTROL:
            return True
        # Non-control frames may not consume the bounded control reservation.
        return (
            self._count < self._non_control_queue_limit
            and self._bytes + frame.byte_size <= self._non_control_byte_limit
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
            frame = await self._next_frame()
            if frame is None:
                return
            try:
                if frame.future.cancelled():
                    continue
                await self._apply_rate_limit()
                # Cancellation can arrive while a selected frame is rate limited.
                if frame.future.cancelled():
                    continue
                # The sender worker may predate this operation.
                sent = await asyncio.create_task(self._transmit(frame), context=frame.context)
                if not sent:
                    continue
            except asyncio.CancelledError:
                self._fail_frame(frame, self._closed_error or SendClosedError())
                raise
            except BaseException as exc:
                self._fail_frame(frame, exc)
                await self._abort(exc)
                return
            else:
                self._sent += 1
                self._sent_by_priority[frame.priority] += 1
                if not frame.future.done():
                    frame.future.set_result(None)
            finally:
                await self._release(frame)

    async def _transmit(self, frame: _Frame) -> bool:
        # The task may start after its original sender has been cancelled.
        if frame.future.cancelled():
            return False
        self._last_sent = self._clock()
        await self._transport(frame.payload)
        return True

    async def _next_frame(self) -> _Frame | None:
        async with self._capacity:
            while not self._closed and not self._has_pending():
                await self._capacity.wait()
            if self._closed and not self._has_pending():
                return None
            if self._closed:
                # close()/``_abort`` fail the remaining frames itself.
                return None
            return self._select_locked()

    def _has_pending(self) -> bool:
        return any(self._pending.values())

    def _select_locked(self) -> _Frame | None:
        """Pick the next frame under priority plus the bounded fairness rule.

        Strict priority would let sustained CONTROL traffic starve everything
        else. After ``fairness_quota`` consecutive selections from higher
        priorities, the highest-priority *starved* level is served once. Within
        a level frames stay FIFO.
        """
        waiting = [priority for priority in SendPriority if self._pending[priority]]
        if not waiting:
            return None
        starved = [
            priority for priority in waiting if self._starvation[priority] >= self._fairness_quota
        ]
        priority = min(starved) if starved else min(waiting)
        frame = self._pending[priority].popleft()
        for lower in SendPriority:
            if lower > priority and self._pending[lower]:
                self._starvation[lower] += 1
        self._starvation[priority] = 0
        return frame

    async def _apply_rate_limit(self) -> None:
        if self._min_interval <= 0:
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

    async def _release(self, frame: _Frame) -> None:
        async with self._capacity:
            self._count = max(0, self._count - 1)
            self._bytes = max(0, self._bytes - frame.byte_size)
            self._capacity.notify_all()

    def _fail_frame(self, frame: _Frame, error: BaseException) -> None:
        if not frame.future.done():
            frame.future.set_exception(error)

    def _drain_locked(self) -> list[_Frame]:
        frames: list[_Frame] = []
        for priority in SendPriority:
            frames.extend(self._pending[priority])
            self._pending[priority].clear()
        self._count = 0
        self._bytes = 0
        return frames

    async def _abort(self, error: BaseException) -> None:
        """Fail every queued frame after a transport failure and stop accepting."""
        self._closed_error = error
        async with self._capacity:
            self._closed = True
            frames = self._drain_locked()
            self._capacity.notify_all()
        for frame in frames:
            self._fail_frame(frame, error)
        logger.error(
            "outbound_transport_failed",
            connection_id=str(self._connection_id),
            error_type=type(error).__name__,
        )

    # -- shutdown -----------------------------------------------------------

    async def close(self, error: BaseException | None = None) -> None:
        """Close the scheduler, fail queued frames and release waiting senders.

        The first call starts cleanup; every later call awaits the same task, so
        a concurrent close cannot return before queued frames are failed and the
        worker has exited. Cleanup is shielded from waiter cancellation.
        """
        task = self._close_task
        if task is None:
            self._closed = True
            self._closed_error = error or self._closed_error or SendClosedError()
            task = asyncio.create_task(
                self._close_impl(),
                name=f"outbound-close:{self._connection_id}",
            )
            self._close_task = task
        await asyncio.shield(task)

    async def _close_impl(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        async with self._capacity:
            frames = self._drain_locked()
            self._capacity.notify_all()
        failure = self._closed_error or SendClosedError()
        for frame in frames:
            self._fail_frame(frame, failure)


__all__ = ["OutboundScheduler", "SendPriority", "TransportSend"]
