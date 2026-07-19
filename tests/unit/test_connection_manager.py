"""Tests for the gateway connection manager (connection.py).

Verifies the batch-C rewrite invariants:
  * create_connection emits CONNECTED and installs a response queue.
  * The response-sender loop classifies queued messages with RouteEnvelope and
    dispatches them to the shared sink (replacing the old if/elif dispatch).
  * Unroutable messages are dropped with a warning, not crashed.
  * drop_connection cancels the sender and emits DISCONNECTED.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from mcbe_ws_sdk.gateway import EventBus, WsEventType
from mcbe_ws_sdk.gateway.connection import ConnectionManager, ConnectionState
from mcbe_ws_sdk.gateway.messages import StreamChunk, SystemNotification
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    RouteEnvelope,
)


class RecordingSink(DefaultResponseSink):
    def __init__(self) -> None:
        self.envelopes: list[tuple[UUID, RouteEnvelope]] = []

    async def on_stream_chunk(self, state: ConnectionState, chunk: StreamChunk) -> None:
        self.envelopes.append((state.id, RouteEnvelope(ResponseKind.STREAM_CHUNK, chunk)))

    async def on_system_notification(
        self, state: ConnectionState, note: SystemNotification
    ) -> None:
        self.envelopes.append((state.id, RouteEnvelope(ResponseKind.SYSTEM_NOTIFICATION, note)))


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
async def test_create_connection_emits_connected_and_installs_queue(
    manager: ConnectionManager, bus: EventBus
) -> None:
    connected: list[ConnectionState] = []
    bus.subscribe(WsEventType.CONNECTED, lambda s: connected.append(s), weak=False)

    state = await manager.create_connection(connection_id=UUID(int=1), send_payload=_send_noop)

    assert state.id == UUID(int=1)
    assert state.response_queue is not None
    assert manager.connection_count == 1
    assert connected == [state]


@pytest.mark.asyncio
async def test_response_sender_dispatches_stream_and_system_to_sink(
    manager: ConnectionManager, sink: RecordingSink
) -> None:
    state = await manager.create_connection(connection_id=UUID(int=2), send_payload=_send_noop)
    queue = state.response_queue
    assert queue is not None

    chunk = StreamChunk(chunk_type="content", content="hello", sequence=1)
    note = SystemNotification(level="info", message="ready")
    queue.put_nowait(chunk)
    queue.put_nowait(note)

    # Yield to let the sender coroutine run.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if len(sink.envelopes) == 2:
            break

    assert len(sink.envelopes) == 2
    assert sink.envelopes[0][1].kind is ResponseKind.STREAM_CHUNK
    assert sink.envelopes[1][1].kind is ResponseKind.SYSTEM_NOTIFICATION


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
