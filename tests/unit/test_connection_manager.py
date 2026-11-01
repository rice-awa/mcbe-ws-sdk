"""Tests for the gateway connection manager (connection.py).

Verifies the batch-C rewrite invariants:
  * create_connection installs a response queue but does NOT emit CONNECTED
    (CONNECTED is facade-owned after handshake + subscribe).
  * The response-sender loop classifies queued messages with RouteEnvelope and
    routes them inline to the sink's on_* methods (no Protocol.dispatch).
  * Unroutable messages are dropped with a warning, not crashed.
  * drop_connection cancels the sender and emits DISCONNECTED.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from mcbe_ws_sdk.config import DispatchSettings, FlowControlSettings, SendSchedulerSettings
from mcbe_ws_sdk.errors import RequestClosedError
from mcbe_ws_sdk.gateway import EventBus, WsEventType
from mcbe_ws_sdk.gateway.connection import (
    ConnectionManager,
    ConnectionState,
    enqueue_response,
)
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification
from mcbe_ws_sdk.gateway.runtime import ConnectionRuntime
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    RouteEnvelope,
)
from mcbe_ws_sdk.requests import CATEGORY_COMMAND, RequestTable


class RecordingSink(DefaultResponseSink):
    def __init__(self) -> None:
        self.envelopes: list[tuple[UUID, RouteEnvelope]] = []

    async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
        self.envelopes.append((state.id, RouteEnvelope(ResponseKind.OUTBOUND_TEXT, message)))

    async def on_system_notification(
        self, state: ConnectionState, note: SystemNotification
    ) -> None:
        self.envelopes.append((state.id, RouteEnvelope(ResponseKind.SYSTEM_NOTIFICATION, note)))


class DuckTypedSink:
    """Minimal host sink: only the two Protocol methods, no dispatch."""

    def __init__(self) -> None:
        self.outbound: list[OutboundText] = []
        self.notifications: list[SystemNotification] = []

    async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
        self.outbound.append(message)

    async def on_system_notification(
        self, state: ConnectionState, note: SystemNotification
    ) -> None:
        self.notifications.append(note)


def _send_noop(payload: str) -> asyncio.Future[None]:  # pragma: no cover - transport stub
    f: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    f.set_result(None)
    return f


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def manager(bus: EventBus, sink: RecordingSink) -> ConnectionManager:
    return ConnectionManager(sink=sink, event_bus=bus)


@pytest.mark.asyncio
async def test_create_connection_installs_queue_without_emitting_connected(
    manager: ConnectionManager, bus: EventBus
) -> None:
    connected: list[ConnectionState] = []
    bus.subscribe(WsEventType.CONNECTED, lambda s: connected.append(s), weak=False)

    state = await manager.create_connection(connection_id=UUID(int=1), send_payload=_send_noop)

    assert state.id == UUID(int=1)
    assert state.response_queue is not None
    assert manager.connection_count == 1
    assert connected == []


@pytest.mark.asyncio
async def test_response_sender_dispatches_outbound_and_system_to_sink(
    manager: ConnectionManager, sink: RecordingSink
) -> None:
    state = await manager.create_connection(connection_id=UUID(int=2), send_payload=_send_noop)
    queue = state.response_queue
    assert queue is not None

    msg = OutboundText(content="hello", sequence=1)
    note = SystemNotification(level="info", message="ready")
    queue.put_nowait(msg)
    queue.put_nowait(note)

    # Yield to let the sender coroutine run.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if len(sink.envelopes) == 2:
            break

    assert len(sink.envelopes) == 2
    assert sink.envelopes[0][1].kind is ResponseKind.OUTBOUND_TEXT
    assert sink.envelopes[1][1].kind is ResponseKind.SYSTEM_NOTIFICATION


@pytest.mark.asyncio
async def test_response_sender_routes_duck_typed_sink_without_dispatch(bus: EventBus) -> None:
    sink = DuckTypedSink()
    manager = ConnectionManager(sink=sink, event_bus=bus)  # type: ignore[arg-type]
    state = await manager.create_connection(connection_id=UUID(int=21), send_payload=_send_noop)
    queue = state.response_queue
    assert queue is not None

    msg = OutboundText(content="duck", sequence=1)
    note = SystemNotification(level="info", message="typed")
    queue.put_nowait(msg)
    queue.put_nowait(note)

    for _ in range(20):
        await asyncio.sleep(0.01)
        if len(sink.outbound) == 1 and len(sink.notifications) == 1:
            break

    assert sink.outbound == [msg]
    assert sink.notifications == [note]
    await manager.drop_connection(state.id)


def _install_runtime(state: ConnectionState) -> ConnectionRuntime:
    runtime = ConnectionRuntime(
        connection_id=state.id,
        dispatch=DispatchSettings(),
        send=SendSchedulerSettings(),
        flow=FlowControlSettings(),
        transport=_send_noop,
    )
    state.runtime = runtime
    state.requests = runtime.requests
    return runtime


@pytest.mark.asyncio
async def test_disconnected_observer_can_repeat_drop_without_changing_order(
    manager: ConnectionManager, bus: EventBus
) -> None:
    state = await manager.create_connection(send_payload=_send_noop)
    state.ready = True
    state.requests = RequestTable()
    order: list[str] = []

    async def on_disconnected(dropped: ConnectionState) -> None:
        if dropped.requests is not None and dropped.requests.is_closed:
            order.append("requests closed")
        await manager.drop_connection(dropped.id)
        await manager.shutdown_all()
        order.append("observer finished")

    bus.subscribe(WsEventType.DISCONNECTED, on_disconnected, weak=False)
    await asyncio.wait_for(manager.drop_connection(state.id), 2)
    assert order == ["requests closed", "observer finished"]
    assert manager._drop_tasks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_first", [False, True])
async def test_concurrent_manager_drops_wait_for_runtime_cleanup(
    manager: ConnectionManager, bus: EventBus, cancel_first: bool
) -> None:
    state = await manager.create_connection(send_payload=_send_noop)
    state.ready = True
    runtime = _install_runtime(state)
    started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    disconnected: list[ConnectionState] = []
    bus.subscribe(WsEventType.DISCONNECTED, lambda s: disconnected.append(s), weak=False)

    async def job() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            await release.wait()

    assert runtime.business.submit(job, lane="player:Alice", label="blocked")
    await asyncio.wait_for(started.wait(), 2)
    first = asyncio.create_task(manager.drop_connection(state.id))
    await asyncio.wait_for(cancelling.wait(), 2)
    second_entered = asyncio.Event()

    async def second_drop() -> None:
        second_entered.set()
        await manager.drop_connection(state.id)

    second = asyncio.create_task(second_drop())
    try:
        await asyncio.wait_for(second_entered.wait(), 2)
        assert not first.done()
        assert not second.done()
        assert runtime.business.task_count == 1
        assert not runtime.scheduler.is_closed
        assert disconnected == []
        if cancel_first:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not second.done()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 2)
    assert second.exception() is None
    if not cancel_first:
        assert first.exception() is None
    assert runtime.business.task_count == 0
    assert runtime.scheduler.is_closed
    assert runtime.requests.pending_count == 0
    assert disconnected == [state]
    assert manager._sender_tasks == {}
    assert manager._drop_tasks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("reliable", [False, True])
async def test_cancelled_drop_during_sender_cleanup_is_joined_by_shutdown(
    bus: EventBus, reliable: bool
) -> None:
    started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    order: list[str] = []

    class SlowSink(DefaultResponseSink):
        async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await release.wait()
                order.append("sender stopped")

    manager = ConnectionManager(sink=SlowSink(), event_bus=bus)
    state = await manager.create_connection(send_payload=_send_noop)
    state.ready = True
    runtime = _install_runtime(state) if reliable else None
    if not reliable:
        state.requests = RequestTable()
    assert state.requests is not None
    pending = state.requests.reserve(CATEGORY_COMMAND, request_id="pending")
    other = await manager.create_connection(send_payload=_send_noop)
    other.ready = True
    other_runtime = _install_runtime(other)
    disconnected: list[ConnectionState] = []
    cleanup_observed: list[tuple[bool, bool, list[str]]] = []

    def on_disconnected(dropped: ConnectionState) -> None:
        disconnected.append(dropped)
        if dropped is state:
            cleanup_observed.append(
                (
                    dropped.requests is not None and dropped.requests.is_closed,
                    dropped.id not in manager._sender_tasks,
                    list(order),
                )
            )

    bus.subscribe(WsEventType.DISCONNECTED, on_disconnected, weak=False)
    enqueue_response(state, OutboundText(content="block", sequence=1))
    await asyncio.wait_for(started.wait(), 2)
    first = asyncio.create_task(manager.drop_connection(state.id))
    await asyncio.wait_for(cancelling.wait(), 2)
    first.cancel()
    shutdown_entered = asyncio.Event()

    async def shutdown() -> None:
        shutdown_entered.set()
        await manager.shutdown_all()

    shutdown_task = asyncio.create_task(shutdown())
    try:
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(shutdown_entered.wait(), 2)
        assert not shutdown_task.done()
        assert disconnected == [] or disconnected == [other]
    finally:
        release.set()
        await asyncio.wait_for(shutdown_task, 2)
        await manager.drop_connection(state.id)
    with pytest.raises(RequestClosedError):
        await pending.future
    assert {s.id for s in disconnected} == {state.id, other.id}
    assert len(disconnected) == 2
    assert cleanup_observed == [(True, True, ["sender stopped"])]
    if runtime is not None:
        assert runtime.scheduler.is_closed
        assert runtime.business.task_count == 0
    assert other_runtime.scheduler.is_closed
    assert manager._sender_tasks == {}
    assert manager._drop_tasks == {}
    assert manager.connection_count == 0


@pytest.mark.asyncio
async def test_response_sender_drops_unroutable_message(manager: ConnectionManager) -> None:
    state = await manager.create_connection(connection_id=UUID(int=3), send_payload=_send_noop)
    queue = state.response_queue
    assert queue is not None

    queue.put_nowait(object())
    await asyncio.sleep(0.15)

    # Sender logs a warning and keeps running; nothing dispatched, state still alive.
    assert manager.get_connection(UUID(int=3)) is state
    sender_done = manager._sender_tasks[UUID(int=3)].done()
    assert sender_done is False


@pytest.mark.asyncio
async def test_drop_connection_awaits_sender_task_cleanup(
    manager: ConnectionManager, bus: EventBus
) -> None:
    state = await manager.create_connection(connection_id=UUID(int=41), send_payload=_send_noop)
    state.ready = True
    disconnected: list[ConnectionState] = []
    bus.subscribe(WsEventType.DISCONNECTED, lambda s: disconnected.append(s), weak=False)
    task = manager._sender_tasks[state.id]

    await manager.drop_connection(state.id)

    assert task.done()
    assert task.cancelled()
    assert state.id not in manager._sender_tasks
    assert manager.get_connection(state.id) is None
    assert manager.connection_count == 0
    assert disconnected == [state]


@pytest.mark.asyncio
async def test_shutdown_all_drops_every_connection(
    manager: ConnectionManager, bus: EventBus
) -> None:
    for i in range(3):
        await manager.create_connection(connection_id=UUID(int=10 + i), send_payload=_send_noop)
    assert manager.connection_count == 3

    await manager.shutdown_all()
    assert manager.connection_count == 0


@pytest.mark.asyncio
async def test_connection_shutdown_is_idempotent(manager: ConnectionManager) -> None:
    state = await manager.create_connection(connection_id=UUID(int=42), send_payload=_send_noop)

    await manager.drop_connection(state.id)
    await manager.drop_connection(state.id)

    assert manager.get_connection(state.id) is None
    assert state.id not in manager._sender_tasks

    await manager.create_connection(connection_id=UUID(int=43), send_payload=_send_noop)
    assert manager.connection_count == 1

    await manager.shutdown_all()
    await manager.shutdown_all()

    assert manager.connection_count == 0
    assert manager._sender_tasks == {}


@pytest.mark.asyncio
async def test_response_queue_overflow_drops_oldest(bus: EventBus, sink: RecordingSink) -> None:
    manager = ConnectionManager(sink=sink, event_bus=bus, response_queue_maxsize=2)
    state = await manager.create_connection(connection_id=UUID(int=50), send_payload=_send_noop)
    # Keep the sender from draining the queue so overflow behaviour is visible.
    await manager._cancel_sender(state.id)
    assert state.response_queue is not None
    assert state.response_queue.maxsize == 2

    enqueue_response(state, "a")
    enqueue_response(state, "b")
    enqueue_response(state, "c")  # drops oldest ("a")

    remaining: list[object] = []
    while not state.response_queue.empty():
        remaining.append(state.response_queue.get_nowait())
    assert remaining == ["b", "c"]

    await manager.drop_connection(state.id)
