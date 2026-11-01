"""Server lifetime, protocol routing and sink delivery."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from mcbe_ws_sdk.config import GatewaySettings, WebsocketTransportConfig
from mcbe_ws_sdk.errors import ServerLifecycleError
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.events import WsEventType
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink, ResponseKind, RouteEnvelope
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)
from mcbe_ws_sdk.server import McbeServer

from .test_reliable_facade import (
    QueueWebSocket,
    RecordingHook,
    _command_response_frame,
    _error_frame,
    _player_frame,
    _stop,
)


class RecordingSink(DefaultResponseSink):
    def __init__(self) -> None:
        self.envelopes: list[tuple[UUID, RouteEnvelope]] = []

    async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
        self.envelopes.append((state.id, RouteEnvelope(ResponseKind.OUTBOUND_TEXT, message)))

    async def on_system_notification(
        self,
        state: ConnectionState,
        note: SystemNotification,
    ) -> None:
        self.envelopes.append((state.id, RouteEnvelope(ResponseKind.SYSTEM_NOTIFICATION, note)))


async def _send_noop(payload: str) -> None:
    return None


@pytest.mark.asyncio
async def test_run_lifetime_stop_unwinds_and_clears_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    class FakeServerContext:
        async def __aenter__(self) -> FakeServerContext:
            entered.set()
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    def fake_serve(handler: object, host: str, port: int, **kwargs: object) -> FakeServerContext:
        return FakeServerContext()

    monkeypatch.setattr("mcbe_ws_sdk.server.websockets.serve", fake_serve)
    server = McbeServer(hook=RecordingHook(), sink=RecordingSink())
    task = asyncio.create_task(server.run_lifetime(host="127.0.0.1", port=0))
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    assert server._server is not None

    await server.stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert server._server is None
    assert server.manager.connection_count == 0


@pytest.mark.asyncio
async def test_run_lifetime_enter_failure_cleans_manager_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enter_error = LookupError("serve enter failed")

    class FailingServerContext:
        async def __aenter__(self) -> object:
            raise enter_error

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    monkeypatch.setattr(
        "mcbe_ws_sdk.server.websockets.serve",
        lambda handler, host, port, **kwargs: FailingServerContext(),
    )
    server = McbeServer(hook=RecordingHook(), sink=RecordingSink())
    connection_id = UUID(int=10)
    await server.manager.create_connection(connection_id=connection_id, send_payload=_send_noop)
    assert server.manager._sender_tasks[connection_id].done() is False

    try:
        with pytest.raises(LookupError, match="serve enter failed") as exc_info:
            await server.run_lifetime(host="127.0.0.1", port=0)
        assert exc_info.value is enter_error
        assert server.manager.connection_count == 0
        assert server.manager._sender_tasks == {}
        assert server._server is None
    finally:
        await server.manager.shutdown_all()


@pytest.mark.asyncio
async def test_run_lifetime_passes_transport_options_to_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(
            host="127.0.0.1",
            port=8765,
            ping_interval=11.0,
            ping_timeout=7.0,
            close_timeout=5.0,
            max_size=4096,
            max_queue=9,
        )
    )
    server = McbeServer(settings=settings, hook=RecordingHook(), sink=RecordingSink())
    captured: dict[str, Any] = {}

    class FakeServerContext:
        sockets = [object()]

        async def __aenter__(self) -> FakeServerContext:
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    def fake_serve(handler: Any, host: str, port: int, **kwargs: Any) -> FakeServerContext:
        captured["handler"] = handler
        captured["host"] = host
        captured["port"] = port
        captured["kwargs"] = kwargs
        return FakeServerContext()

    monkeypatch.setattr("mcbe_ws_sdk.server.websockets.serve", fake_serve)
    task = asyncio.create_task(server.run_lifetime())
    await asyncio.sleep(0)
    await server.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert captured["handler"] == server._on_connection
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["kwargs"] == {
        "ping_interval": 11.0,
        "ping_timeout": 7.0,
        "close_timeout": 5.0,
        "max_size": 4096,
        "max_queue": 9,
    }


@pytest.mark.asyncio
async def test_lifetime_is_single_use(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeServerContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    monkeypatch.setattr(
        "mcbe_ws_sdk.server.websockets.serve",
        lambda handler, host, port, **kwargs: FakeServerContext(),
    )
    server = McbeServer()
    await server.stop()
    await server.run_lifetime(host="127.0.0.1", port=0)
    with pytest.raises(ServerLifecycleError, match="single-use"):
        await server.run_lifetime(host="127.0.0.1", port=0)


@pytest.mark.asyncio
async def test_connection_emits_connected_after_handshake_and_disconnects() -> None:
    hook = RecordingHook()
    server = McbeServer(hook=hook, sink=RecordingSink())
    connected: list[UUID] = []
    disconnected: list[UUID] = []
    server.manager.event_bus.subscribe(
        WsEventType.CONNECTED, lambda state: connected.append(state.id), weak=False
    )
    server.manager.event_bus.subscribe(
        WsEventType.DISCONNECTED, lambda state: disconnected.append(state.id), weak=False
    )

    websocket = QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    await _stop(websocket, task)

    assert hook.state is not None
    assert connected == [hook.state.id]
    assert disconnected == [hook.state.id]
    assert hook.connected.is_set()
    assert hook.disconnected.is_set()
    assert server.manager.connection_count == 0
    assert websocket.sent[0] == '{"Result":"true"}'
    assert json.loads(websocket.sent[1])["header"]["messagePurpose"] == "subscribe"


@pytest.mark.asyncio
async def test_command_response_and_error_call_hooks() -> None:
    hook = RecordingHook()
    seen_errors: list[tuple[ConnectionState, MinecraftErrorFrame]] = []
    seen_responses: list[tuple[ConnectionState, MinecraftCommandResponse]] = []

    async def on_error(state: ConnectionState, event: MinecraftErrorFrame) -> None:
        seen_errors.append((state, event))

    async def on_response(state: ConnectionState, event: MinecraftCommandResponse) -> None:
        seen_responses.append((state, event))

    server = McbeServer(hook=hook, sink=RecordingSink())
    server.manager.event_bus.subscribe(WsEventType.ERROR, on_error, weak=False)
    server.manager.event_bus.subscribe(WsEventType.COMMAND_RESPONSE, on_response, weak=False)

    websocket = QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    try:
        await websocket.push(_error_frame("err-1"))
        await websocket.push(_command_response_frame("req-1"))
        await asyncio.wait_for(hook.error_seen.wait(), 2)
        for _ in range(100):
            if seen_responses:
                break
            await asyncio.sleep(0.01)
        assert seen_errors[0][1].request_id == "err-1"
        assert seen_responses[0][1].request_id == "req-1"
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_player_message_reaches_hook_with_parsed_command() -> None:
    from mcbe_ws_sdk.command import CommandRegistry

    hook = RecordingHook()
    server = McbeServer(
        hook=hook,
        sink=RecordingSink(),
        registry=CommandRegistry({"#x": "demo"}),
    )
    parsed: list[object | None] = []
    original = hook.on_player

    async def record(state: ConnectionState, event: PlayerMessageEvent) -> None:
        parsed.append(server.handler.parse_typed_command(event.message))
        if original is not None:
            await original(state, event)

    hook.on_player = record
    websocket = QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    try:
        await websocket.push(_player_frame("Alice", "hello world"))
        await websocket.push(_player_frame("Alice", "#x payload"))
        for _ in range(100):
            if len(hook.messages) == 2:
                break
            await asyncio.sleep(0.01)
        assert [event.message for event in hook.messages] == ["hello world", "#x payload"]
        assert parsed[0] is None
        assert parsed[1] is not None
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_raw_outbound_event_includes_state() -> None:
    seen: list[tuple[ConnectionState, str]] = []

    async def on_outbound(state: ConnectionState, payload: str) -> None:
        seen.append((state, payload))

    hook = RecordingHook()
    server = McbeServer(hook=hook, sink=RecordingSink())
    server.manager.event_bus.subscribe(WsEventType.RAW_OUTBOUND, on_outbound, weak=False)
    websocket = QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    await _stop(websocket, task)

    assert seen
    assert any(payload == '{"Result":"true"}' for _, payload in seen)
    assert all(state is not None for state, _ in seen)


@pytest.mark.asyncio
async def test_sink_receives_outbound_text() -> None:
    sink = RecordingSink()
    server = McbeServer(hook=RecordingHook(), sink=sink)
    state = await server.manager.create_connection(
        connection_id=uuid4(),
        send_payload=_send_noop,
    )
    assert state.response_queue is not None

    message = OutboundText(content="hello world", sequence=1)
    await state.response_queue.put(message)
    for _ in range(30):
        await asyncio.sleep(0.01)
        if len(sink.envelopes) == 1:
            break

    assert len(sink.envelopes) == 1
    envelope = sink.envelopes[0][1]
    assert envelope.kind is ResponseKind.OUTBOUND_TEXT
    assert isinstance(envelope.payload, OutboundText)
    assert envelope.payload.content == "hello world"

    await server.manager.drop_connection(state.id)
    assert state.id not in server.manager._sender_tasks


def test_default_wiring() -> None:
    server = McbeServer()
    assert isinstance(server.manager.sink, DefaultResponseSink)
    assert server.handler.command_registry.list_all_commands() == []
    assert server.v2 is not None
