"""Bounded business-task dispatch for the reliable connection path.

The reliable path keeps the WebSocket reader free: inbound frames are parsed and
cheap SDK work (request pairing, chunk reassembly) happens inline, while every
host callback is submitted to a :class:`BusinessDispatcher`. The dispatcher owns
the resulting tasks, bounds both the pending queue and the number of concurrent
tasks, serialises work per lane (for example one lane per player), and reaps
everything on close.

Rejection is explicit: :meth:`BusinessDispatcher.submit` returns ``False`` and
counts the rejection when the queue is full or the dispatcher is closed. It never
waits for room, so the caller (the reader) cannot be blocked by a slow handler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

BusinessCallable = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class _BusinessItem:
    lane: str
    label: str
    call: BusinessCallable


class BusinessDispatcher:
    """Own a bounded business queue plus its in-flight tasks for one connection.

    Args:
        connection_id: Owning connection, used only for structured logging.
        max_queue: Maximum pending callbacks. Submissions beyond it are rejected.
        max_concurrent: Maximum callbacks running at once. Callbacks in different
            lanes may run concurrently; callbacks in the same lane run in FIFO
            order.
        name: Dispatcher name for logs and task names.
    """

    def __init__(
        self,
        *,
        connection_id: UUID,
        max_queue: int = 256,
        max_concurrent: int = 64,
        name: str = "business",
    ) -> None:
        if type(max_queue) is not int or max_queue <= 0:
            raise ValueError("max_queue must be a positive integer")
        if type(max_concurrent) is not int or max_concurrent <= 0:
            raise ValueError("max_concurrent must be a positive integer")
        self._connection_id = connection_id
        self._name = name
        self._max_queue = max_queue
        self._queue: asyncio.Queue[_BusinessItem] = asyncio.Queue(maxsize=max_queue)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task[None]] = set()
        self._lane_locks: dict[str, asyncio.Lock] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._closed = False
        self._rejected = 0
        self._completed = 0
        self._failed = 0
        self._active = 0

    # -- introspection ------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

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
        return len(self._lane_locks)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def max_queue(self) -> int:
        return self._max_queue

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
            logger.warning(
                "business_submit_after_close",
                connection_id=str(self._connection_id),
                dispatcher=self._name,
                lane=lane,
                label=label,
            )
            return False
        if self._queue.full():
            self._rejected += 1
            logger.warning(
                "business_queue_overflow",
                connection_id=str(self._connection_id),
                dispatcher=self._name,
                lane=lane,
                label=label,
                maxsize=self._max_queue,
            )
            return False
        self.start()
        self._queue.put_nowait(_BusinessItem(lane=lane, label=label, call=call))
        return True

    # -- supervisor ---------------------------------------------------------

    async def _supervise(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._semaphore.acquire()
            except asyncio.CancelledError:
                # The dequeued item never starts; account for it as overload.
                self._rejected += 1
                raise
            self._active += 1
            task = asyncio.create_task(
                self._run(item),
                name=f"{self._name}:{item.label or item.lane}:{self._connection_id}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._on_done)

    async def _run(self, item: _BusinessItem) -> None:
        lock = self._lane_lock(item.lane)
        try:
            async with lock:
                await item.call()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed += 1
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
        self._active = max(0, self._active - 1)
        self._semaphore.release()
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:  # defensive: ``_run`` already isolates exceptions
            logger.error(
                "business_task_unhandled",
                connection_id=str(self._connection_id),
                dispatcher=self._name,
                error_type=type(exc).__name__,
            )

    def _lane_lock(self, lane: str) -> asyncio.Lock:
        lock = self._lane_locks.get(lane)
        if lock is None:
            lock = asyncio.Lock()
            self._lane_locks[lane] = lock
        return lock

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
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            drained += 1
        if drained:
            self._rejected += drained
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
        # Let done callbacks release the semaphore and update counters.
        await asyncio.sleep(0)
        self._lane_locks.clear()


__all__ = ["BusinessCallable", "BusinessDispatcher"]
