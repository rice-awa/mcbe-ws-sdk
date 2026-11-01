"""Per-connection runtime for the reliable path.

A :class:`ConnectionRuntime` groups everything the SDK owns for one reliable
connection: the business and observation dispatchers, the request table, the
outbound scheduler and the native command client. Grouping them makes the close
path explicit and lets connection teardown release every waiter and task exactly
once.

Lanes are the ordering mechanism: work submitted to the same lane runs in FIFO
order, while different lanes run concurrently. The facade uses one lane per
player (``player:<name>``) and dedicated ``connection`` / ``control`` lanes for
messages without a player identity. Hosts that need to serialise world-mutating
commands can use :meth:`ConnectionRuntime.lane` with a shared name such as
``world``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

import structlog

from mcbe_ws_sdk.config import DispatchSettings, FlowControlSettings, SendSchedulerSettings
from mcbe_ws_sdk.diagnostics import Diagnostics
from mcbe_ws_sdk.errors import RequestClosedError, SendClosedError
from mcbe_ws_sdk.gateway.commands import NativeCommandClient
from mcbe_ws_sdk.gateway.connection import SendPayload
from mcbe_ws_sdk.gateway.dispatch import BusinessDispatcher
from mcbe_ws_sdk.gateway.native_events import NativeEventSubscriptions
from mcbe_ws_sdk.gateway.sender import OutboundScheduler, SendPriority
from mcbe_ws_sdk.requests import RequestTable
from mcbe_ws_sdk.tracing import trace_scope

logger = structlog.get_logger(__name__)


class ConnectionRuntime:
    """SDK-owned schedulers, tasks and request state for one connection."""

    def __init__(
        self,
        *,
        connection_id: UUID,
        dispatch: DispatchSettings,
        send: SendSchedulerSettings,
        flow: FlowControlSettings,
        transport: SendPayload,
        diagnostics: Diagnostics | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.connection_id = connection_id
        self.command_line_byte_budget = flow.command_line_byte_budget
        self.diagnostics = diagnostics
        self.business = BusinessDispatcher(
            connection_id=connection_id,
            max_queue=dispatch.business_queue_maxsize,
            max_concurrent=dispatch.max_concurrent_business_tasks,
            name="business",
        )
        # Observation handlers are best-effort and run on one lane so their
        # relative order is preserved without blocking the reader.
        self.events = BusinessDispatcher(
            connection_id=connection_id,
            max_queue=dispatch.event_queue_maxsize,
            max_concurrent=1,
            name="events",
        )
        self.requests = RequestTable(
            max_pending=dispatch.max_pending_requests,
            name=f"commands:{connection_id}",
        )
        self.scheduler = OutboundScheduler(
            connection_id=connection_id,
            transport=transport,
            max_queue=send.queue_maxsize,
            max_bytes=send.queue_max_bytes,
            min_interval=send.min_interval_seconds,
            capacity_timeout=send.capacity_timeout_seconds,
            control_reserve_slots=send.control_reserve_slots,
            control_reserve_bytes=send.control_reserve_bytes,
            fairness_quota=send.fairness_quota,
            clock=clock,
            sleep=sleep,
        )
        self.commands = NativeCommandClient(
            connection_id=connection_id,
            requests=self.requests,
            flow=flow,
            send_payload=self.send_from_host,
            timeout_seconds=dispatch.command_timeout_seconds,
            diagnostics=diagnostics,
        )
        self.native_events = NativeEventSubscriptions(
            self._send_subscription,
            max_listeners=dispatch.event_queue_maxsize,
            timeout_seconds=dispatch.command_timeout_seconds,
        )
        self._lanes: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._close_guard = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._close_reason = "connection closed"

    # -- state --------------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def close_reason(self) -> str:
        return self._close_reason

    def lane(self, name: str) -> asyncio.Lock:
        """Return the FIFO lock for ``name`` (shared ordering group)."""
        lock = self._lanes.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._lanes[name] = lock
        return lock

    # -- sending ------------------------------------------------------------

    async def send_from_host(self, payload: str) -> None:
        """``ConnectionState.send_payload`` adapter used by host/delivery code.

        Keeps the legacy single-argument callable contract; host frames that do
        not declare a category are treated as requests.
        """
        self._raise_if_closed()
        with trace_scope(connection_id=str(self.connection_id), diagnostics=self.diagnostics):
            await self.scheduler.send_from_host(payload)

    async def send_bulk_from_host(self, payload: str) -> None:
        """``ConnectionState.send_bulk`` adapter for long tellraw/scriptevent text.

        The signature is identical to :meth:`send_from_host` so delivery code can
        accept either callable without knowing about the scheduler.
        """
        self._raise_if_closed()
        with trace_scope(connection_id=str(self.connection_id), diagnostics=self.diagnostics):
            await self.scheduler.send(payload, priority=SendPriority.BULK, source="host.bulk")

    async def send_raw(
        self,
        payload: str,
        *,
        priority: SendPriority = SendPriority.REQUEST,
        source: str = "runtime",
    ) -> None:
        """Send one frame through the scheduler with an explicit priority."""
        self._raise_if_closed()
        with trace_scope(connection_id=str(self.connection_id), diagnostics=self.diagnostics):
            await self.scheduler.send(payload, priority=priority, source=source)

    async def _send_subscription(self, payload: str) -> None:
        await self.send_raw(payload, priority=SendPriority.CONTROL, source="native_subscription")

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise SendClosedError(self._close_reason)

    # -- shutdown -----------------------------------------------------------

    async def close(self, *, reason: str = "connection closed") -> None:
        """Release every waiter and task exactly once.

        The first call starts the cleanup task; every later call awaits that
        same task, so a second ``close()`` can never return while the first is
        still reaping tasks or closing the scheduler. The cleanup is shielded
        from waiter cancellation: cancelling one caller does not stop it
        halfway, and a later call still observes the completed state.
        """
        async with self._close_guard:
            task = self._close_task
            if task is None:
                self._closed = True
                self.native_events.close()
                self._close_reason = reason
                task = asyncio.create_task(
                    self._close_impl(reason),
                    name=f"runtime-close:{self.connection_id}",
                )
                self._close_task = task
        await asyncio.shield(task)

    async def _close_impl(self, reason: str) -> None:
        failed = self.requests.close(lambda request_id: RequestClosedError(request_id, reason))
        await self.business.close()
        await self.events.close()
        await self.scheduler.close(SendClosedError(reason))
        if self.diagnostics is not None:
            await self.diagnostics.close()
        self._lanes.clear()
        logger.debug(
            "connection_runtime_closed",
            connection_id=str(self.connection_id),
            failed_requests=failed,
        )


__all__ = ["ConnectionRuntime"]
