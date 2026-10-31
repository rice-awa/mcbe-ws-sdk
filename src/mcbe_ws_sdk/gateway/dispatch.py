"""Bounded business-task dispatch for the reliable connection path.

The reliable path keeps the WebSocket reader free: inbound frames are parsed and
cheap SDK work (request pairing, chunk reassembly) happens inline, while every
host callback is submitted to a :class:`BusinessDispatcher`. The dispatcher owns
the resulting tasks, bounds both the pending queue and the number of concurrent
tasks, serialises work per lane (for example one lane per player), and reaps
everything on close.

Admission is fair across lanes: a global concurrency slot is only taken by a
task that can actually run. An item whose lane is already executing stays in
its lane queue without holding a slot, so one paused player cannot consume the
whole concurrency budget while another player's work waits. Lanes are served
round-robin, and each lane keeps FIFO order.

Rejection is explicit: :meth:`BusinessDispatcher.submit` returns ``False`` and
counts the rejection when the queue is full or the dispatcher is closed. It never
waits for room, so the caller (the reader) cannot be blocked by a slow handler.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from contextvars import Context, copy_context
from dataclasses import dataclass
from uuid import UUID

import structlog

from mcbe_ws_sdk.tracing import observe

logger = structlog.get_logger(__name__)

BusinessCallable = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class _BusinessItem:
    lane: str
    label: str
    call: BusinessCallable
    context: Context


class BusinessDispatcher:
    """Own a bounded business queue plus its in-flight tasks for one connection.

    Args:
        connection_id: Owning connection, used only for structured logging.
        max_queue: Maximum pending callbacks. Submissions beyond it are rejected.
        max_concurrent: Maximum callbacks running at once. Callbacks in different
            lanes may run concurrently; callbacks in the same lane run in FIFO
            order. A lane that is already running never holds a second slot.
        name: Dispatcher name for logs and task names.
    """

    def __init__(
        self,
        *,
        connection_id: UUID,
        max_queue: int = 256,
        max_concurrent: int = 64,
        name: str = "business",
        log_rejections: bool = True,
    ) -> None:
        if type(max_queue) is not int or max_queue <= 0:
            raise ValueError("max_queue must be a positive integer")
        if type(max_concurrent) is not int or max_concurrent <= 0:
            raise ValueError("max_concurrent must be a positive integer")
        self._connection_id = connection_id
        self._name = name
        self._log_rejections = log_rejections
        self._max_queue = max_queue
        self._max_concurrent = max_concurrent

        # One FIFO bucket per lane; only the head can run.
        self._pending: dict[str, deque[_BusinessItem]] = {}
        # Lanes ready to run, in round-robin order. A lane appears at most once.
        self._ready: deque[str] = deque()
        self._ready_set: set[str] = set()
        self._running_lanes: set[str] = set()
        self._task_lanes: dict[asyncio.Task[None], str] = {}
        self._pending_count = 0

        self._tasks: set[asyncio.Task[None]] = set()
        self._wakeup = asyncio.Event()
        self._supervisor: asyncio.Task[None] | None = None
        self._closed = False
        self._rejected = 0
        self._completed = 0
        self._failed = 0
        self._active = 0

    # -- introspection ------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return self._pending_count

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    @property
    def rejected_count(self) -> int:
        return self._rejected

    @property
    def completed_count(self) -> int:
        return self._completed

    @property
    def failed_count(self) -> int:
        return self._failed

    @property
    def lane_count(self) -> int:
        return len(set(self._pending) | self._running_lanes)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def max_queue(self) -> int:
        return self._max_queue

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    # -- submission ---------------------------------------------------------

    def start(self) -> None:
        """Start the supervisor if it is not running yet."""
        if self._closed or self._supervisor is not None:
            return
        self._supervisor = asyncio.create_task(
            self._supervise(),
            name=f"{self._name}:supervisor:{self._connection_id}",
        )

    def submit(
        self,
        call: BusinessCallable,
        *,
        lane: str = "control",
        label: str = "",
    ) -> bool:
        """Queue ``call`` without blocking.

        Returns ``True`` when accepted and ``False`` when rejected because the
        queue is full or the dispatcher is closed. Rejections are counted and
        logged; callers should treat them as explicit overload.
        """
        if self._closed:
            self._rejected += 1
            self._warn_rejection(
                "business_submit_after_close",
                connection_id=str(self._connection_id),
                dispatcher=self._name,
                lane=lane,
                label=label,
            )
            return False
        if self._pending_count >= self._max_queue:
            self._rejected += 1
            self._warn_rejection(
                "business_queue_overflow",
                connection_id=str(self._connection_id),
                dispatcher=self._name,
                lane=lane,
                label=label,
                maxsize=self._max_queue,
            )
            return False
        self.start()
        self._pending.setdefault(lane, deque()).append(
            _BusinessItem(lane=lane, label=label, call=call, context=copy_context())
        )
        self._pending_count += 1
        # A lane that is already running (or already queued) is scheduled when
        # its turn comes; only newly idle lanes need to enter the ready ring.
        if lane not in self._running_lanes and lane not in self._ready_set:
            self._ready.append(lane)
            self._ready_set.add(lane)
        self._wakeup.set()
        return True

    def _warn_rejection(self, event: str, **fields: object) -> None:
        if self._log_rejections:
            logger.warning(event, **fields)

    # -- supervisor ---------------------------------------------------------

    async def _supervise(self) -> None:
        """Start every runnable lane task while a global slot is available.

        Only a lane that is not already running is eligible, so queued work for
        a paused lane never consumes a concurrency slot. The ready ring is FIFO,
        which gives round-robin fairness between lanes and keeps same-lane FIFO
        through each lane's bucket.
        """
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            if self._closed:
                return
            while self._active < self._max_concurrent and self._ready:
                lane = self._ready.popleft()
                self._ready_set.discard(lane)
                bucket = self._pending.get(lane)
                if not bucket:
                    # Defensive: a stale ready entry cannot consume a slot.
                    self._pending.pop(lane, None)
                    continue
                item = bucket.popleft()
                self._pending_count -= 1
                if not bucket:
                    self._pending.pop(lane, None)
                self._running_lanes.add(lane)
                self._active += 1
                task = asyncio.create_task(
                    self._run(item),
                    context=item.context,
                    name=f"{self._name}:{item.label or item.lane}:{self._connection_id}",
                )
                self._tasks.add(task)
                self._task_lanes[task] = lane
                task.add_done_callback(self._on_done)

    async def _run(self, item: _BusinessItem) -> None:
        # Lane exclusivity is enforced by the supervisor: at most one task per
        # lane exists at any time, so no per-lane lock is needed here.
        try:
            await item.call()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed += 1
            observe("processing_failed", dispatcher=self._name, label=item.label)
            logger.exception(
                "business_task_failed",
                connection_id=str(self._connection_id),
                dispatcher=self._name,
                lane=item.lane,
                label=item.label,
            )
        else:
            self._completed += 1

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        lane = self._task_lanes.pop(task, None)
        self._active = max(0, self._active - 1)
        if lane is not None:
            self._running_lanes.discard(lane)
            if self._pending.get(lane) and lane not in self._ready_set:
                self._ready.append(lane)
                self._ready_set.add(lane)
        if task.cancelled():
            self._wakeup.set()
            return
        exc = task.exception()
        if exc is not None:  # defensive: ``_run`` already isolates exceptions
            logger.error(
                "business_task_unhandled",
                connection_id=str(self._connection_id),
                dispatcher=self._name,
                error_type=type(exc).__name__,
            )
        self._wakeup.set()

    # -- shutdown -----------------------------------------------------------

    async def close(self) -> None:
        """Stop accepting work, cancel in-flight tasks and await their exit."""
        if self._closed:
            return
        self._closed = True
        supervisor = self._supervisor
        self._supervisor = None
        if supervisor is not None:
            supervisor.cancel()
            with suppress(asyncio.CancelledError):
                await supervisor
        drained = 0
        for bucket in self._pending.values():
            drained += len(bucket)
        self._pending.clear()
        self._pending_count = 0
        self._ready.clear()
        self._ready_set.clear()
        if drained:
            self._rejected += drained
        if drained and self._log_rejections:
            logger.info(
                "business_queue_discarded",
                connection_id=str(self._connection_id),
                dispatcher=self._name,
                discarded=drained,
            )
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Let done callbacks release counters and clear the lane table.
        await asyncio.sleep(0)
        self._pending.clear()
        self._ready.clear()
        self._ready_set.clear()
        self._running_lanes.clear()
        self._task_lanes.clear()


__all__ = ["BusinessCallable", "BusinessDispatcher"]
