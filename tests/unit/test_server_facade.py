"""Tests for the gateway server facade (server_facade.py).

Drives the facade two complementary ways, exactly as the scope doc prescribes:

* **In-process fake transport**: build a facade, then call
  ``facade._on_connection(fake_ws)`` where ``fake_ws`` is a tiny async-iterable
  whose ``send`` records outbound frames. This exercises the real routing
  loop (parse -> branch -> hook call) without binding any port.
* **Fake ``websockets`` lifetime**: monkeypatch ``websockets.serve`` with an
  async context manager, then exercise ``run_lifetime`` and ``stop()`` without
  opening a socket.

All assertions go through the ``EventBus`` + a ``RecordingSink`` /
``RecordingHook`` pair just like ``tests/unit/test_connection_manager.py``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

import mcbe_ws_sdk
from mcbe_ws_sdk.addon.service import AddonBridgeService
from mcbe_ws_sdk.config import AddonBridgeSettings, GatewaySettings, WebsocketTransportConfig
from mcbe_ws_sdk.errors import FacadeLifecycleError
from mcbe_ws_sdk.gateway import WsEventType
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    RouteEnvelope,
)
from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    AddonBridgeChunk,
    UiChatChunk,
    UiChatMessage,
)
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)

# --------------------------------------------------------------------------- #
# Shared test doubles
# --------------------------------------------------------------------------- #


class FakeWebSocket:
    """Minimal async-iterable transport that records ``send``ed frames."""

    def __init__(self, frames: list[str]) -> None:
        self._frames = frames
        self.sent: list[str] = []
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[str]:
        for frame in self._frames:
            yield frame

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


class RecordingSink(DefaultResponseSink):
    """Sink that captures routed envelopes instead of only logging/raising."""

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


class RecordingHook(NoOpHook):
    """Hook that records every call and logs routed player messages."""

    def __init__(self) -> None:
        self.connected: list[UUID] = []
        self.disconnected: list[UUID] = []
        self.player_messages: list[PlayerMessageEvent] = []
        self.parsed: list[object | None] = []
        self.ui_chat_reassembled: list[tuple[str, str]] = []
        self.command_responses: list[MinecraftCommandResponse] = []
        self.errors: list[MinecraftErrorFrame] = []

    async def on_connected(self, state: ConnectionState) -> None:
        self.connected.append(state.id)

    async def on_disconnected(self, state: ConnectionState) -> None:
        self.disconnected.append(state.id)

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: object | None = None,
    ) -> None:
        self.player_messages.append(player_event)
        self.parsed.append(parsed)

    async def on_ui_chat_reassembled(
        self,
        state: ConnectionState,
        player_name: str,
        message: str,
    ) -> None:
        self.ui_chat_reassembled.append((player_name, message))

    async def on_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
        self.command_responses.append(response)

    async def on_error(
        self,
        state: ConnectionState,
        error: MinecraftErrorFrame,
    ) -> None:
        self.errors.append(error)


class ExplodingHook(RecordingHook):
    """Raises on the first player message; records subsequent ones."""

    def __init__(self) -> None:
        super().__init__()
        self._seen = 0

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: object | None = None,
    ) -> None:
        self._seen += 1
        if self._seen == 1:
            raise RuntimeError("hook boom")
        await super().on_player_message(state, player_event, parsed=parsed)


class RecordingAddon(AddonBridgeService):
    """Addon double that records routed AP frame deliveries."""

    def __init__(self, settings: AddonBridgeSettings | None = None) -> None:
        super().__init__(settings if settings is not None else AddonBridgeSettings())
        self.handled: list[tuple[UUID, str, str]] = []

    async def handle_player_message(self, connection_id: UUID, sender: str, message: str) -> Any:
        self.handled.append((connection_id, sender, message))
        return await super().handle_player_message(connection_id, sender, message)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _player_message_frame(sender: str, message: str) -> str:
    """Build a ``PlayerMessage`` event frame the handler recognises."""
    return json.dumps(
        {
            "header": {"eventName": "PlayerMessage", "requestId": "evt-1"},
            "body": {"sender": sender, "message": message},
        }
    )


def _complete_ui_player_frame(player: str, message: str) -> str:
    return _player_message_frame(
        "MCBEWS_BRIDGE",
        f'MCBEWS|UI_CHAT|m1|1/1|{{"player":"{player}","message":"{message}"}}',
    )


def _command_response_frame(
    request_id: str,
    *,
    status_code: int = 0,
    status_message: str = "命令执行成功",
    extra_body: dict[str, Any] | None = None,
    extra_header: dict[str, Any] | None = None,
    extra_envelope: dict[str, Any] | None = None,
) -> str:
    """Build a ``commandResponse`` frame matching the facade's shape heuristic."""
    body = {"statusCode": status_code, "statusMessage": status_message}
    if extra_body is not None:
        body.update(extra_body)
    header = {"requestId": request_id, "messagePurpose": "commandResponse"}
    if extra_header is not None:
        header.update(extra_header)
    envelope: dict[str, Any] = {
        "header": header,
        "body": body,
    }
    if extra_envelope is not None:
        envelope.update(extra_envelope)
    return json.dumps(envelope)


def _error_frame(
    request_id: str,
    *,
    body: dict[str, Any] | None = None,
    extra_envelope: dict[str, Any] | None = None,
) -> str:
    envelope: dict[str, Any] = {
        "header": {"requestId": request_id, "messagePurpose": "error", "code": 500},
        "body": body if body is not None else {"statusMessage": "boom"},
    }
    if extra_envelope is not None:
        envelope.update(extra_envelope)
    return json.dumps(envelope)


def _send_noop(payload: str) -> Any:  # pragma: no cover - transport stub
    f: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    f.set_result(None)
    return f


def test_facade_has_no_inbound_capability_registry() -> None:
    params = inspect.signature(McbeServerFacade.__init__).parameters
    inbound_param = "capabil" + "ities"
    bridge_hook_name = "on_bridge" + "_message"
    registry_export = "Capability" + "Registry"
    deleted_package_export = "capab" + "ility"

    assert inbound_param not in params
    assert not hasattr(McbeServerFacade(), "_capabilities")
    assert not hasattr(NoOpHook, bridge_hook_name)
    assert not hasattr(mcbe_ws_sdk, registry_export)
    assert deleted_package_export not in mcbe_ws_sdk.__all__


# --------------------------------------------------------------------------- #
# 1. run_lifetime stops and clears state
# --------------------------------------------------------------------------- #


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

    monkeypatch.setattr("mcbe_ws_sdk.gateway.server_facade.websockets.serve", fake_serve)
    facade = McbeServerFacade(hook=RecordingHook(), sink=RecordingSink())
    task = asyncio.create_task(facade.run_lifetime(host="127.0.0.1", port=0))
    await asyncio.wait_for(entered.wait(), timeout=2.0)

    assert facade._server is not None

    await facade.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert facade._server is None
    assert facade.manager.connection_count == 0


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

    def fake_serve(handler: object, host: str, port: int, **kwargs: object) -> FailingServerContext:
        return FailingServerContext()

    monkeypatch.setattr("mcbe_ws_sdk.gateway.server_facade.websockets.serve", fake_serve)
    facade = McbeServerFacade(hook=RecordingHook(), sink=RecordingSink())
    connection_id = UUID(int=10)
    await facade.manager.create_connection(
        connection_id=connection_id,
        send_payload=_send_noop,
    )
    assert facade.manager._sender_tasks[connection_id].done() is False

    try:
        with pytest.raises(LookupError, match="serve enter failed") as exc_info:
            await facade.run_lifetime(host="127.0.0.1", port=0)
        assert exc_info.value is enter_error
        assert facade.manager.connection_count == 0
        assert facade.manager._sender_tasks == {}
        assert facade._server is None
    finally:
        await facade.manager.shutdown_all()


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
    facade = McbeServerFacade(settings=settings, hook=RecordingHook(), sink=RecordingSink())
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

    monkeypatch.setattr("mcbe_ws_sdk.gateway.server_facade.websockets.serve", fake_serve)
    task = asyncio.create_task(facade.run_lifetime())
    await asyncio.sleep(0)
    await facade.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert captured["handler"] == facade._on_connection
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["kwargs"] == {
        "ping_interval": 11.0,
        "ping_timeout": 7.0,
        "close_timeout": 5.0,
        "max_size": 4096,
        "max_queue": 9,
    }


# --------------------------------------------------------------------------- #
# 2. on_connection emits CONNECTED then DISCONNECTS
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_on_connection_emits_connected_and_disconnects() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())

    connected: list[UUID] = []
    disconnected: list[UUID] = []
    facade.manager.event_bus.subscribe(
        WsEventType.CONNECTED,
        lambda s: connected.append(s.id),
        weak=False,
    )
    facade.manager.event_bus.subscribe(
        WsEventType.DISCONNECTED,
        lambda s: disconnected.append(s.id),
        weak=False,
    )

    await facade._on_connection(FakeWebSocket(frames=[]))

    assert len(connected) == 1
    assert connected == disconnected == [hook.connected[0]]
    assert len(hook.connected) == 1
    assert len(hook.disconnected) == 1
    assert facade.manager.connection_count == 0


@pytest.mark.asyncio
async def test_connection_sends_init_and_subscribe_before_hook() -> None:
    order: list[str] = []

    class Hook(RecordingHook):
        async def on_connected(self, state: ConnectionState) -> None:
            order.append("hook")

    websocket = FakeWebSocket(frames=[])
    facade = McbeServerFacade(hook=Hook(), sink=RecordingSink())
    original_send = websocket.send

    async def record_send(payload: str) -> None:
        order.append(payload)
        await original_send(payload)

    async def on_connected_event(state: ConnectionState) -> None:
        order.append("connected_event")

    facade.manager.event_bus.subscribe(WsEventType.CONNECTED, on_connected_event, weak=False)
    websocket.send = record_send  # type: ignore[method-assign]
    await facade._on_connection(websocket)

    assert order[0] == '{"Result":"true"}'
    assert json.loads(order[1])["header"]["messagePurpose"] == "subscribe"
    assert json.loads(order[1])["body"]["eventName"] == "PlayerMessage"
    # D8: CONNECTED emit, then hook.on_connected — both after handshake+subscribe.
    assert order[2] == "connected_event"
    assert order[3] == "hook"


# --------------------------------------------------------------------------- #
# 3. bridge-response frames route through the addon, not the player hook
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_routes_bridge_response_through_addon() -> None:
    hook = RecordingHook()
    addon = RecordingAddon()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink(), addon=addon)

    resp = 'MCBEWS|BRIDGE|req-7|1/1|{"ok": true}'
    ws = FakeWebSocket(frames=[_player_message_frame("MCBEWS_BRIDGE", resp)])

    await facade._on_connection(ws)

    assert addon.handled and addon.handled[0][1] == "MCBEWS_BRIDGE"
    assert addon.handled[0][2] == resp
    assert hook.player_messages == []  # NOT handed to the player hook


@pytest.mark.asyncio
async def test_malformed_ui_frame_does_not_block_following_player_frame() -> None:
    hook = RecordingHook()
    frames = [
        _player_message_frame("MCBEWS_BRIDGE", "MCBEWS|UI_CHAT|bad"),
        _player_message_frame("Alice", "hello"),
    ]

    await McbeServerFacade(hook=hook, sink=RecordingSink())._on_connection(FakeWebSocket(frames))

    assert [event.message for event in hook.player_messages] == ["hello"]


class FailingUiHook(RecordingHook):
    async def on_ui_chat_reassembled(
        self,
        state: ConnectionState,
        player_name: str,
        message: str,
    ) -> None:
        raise LookupError("callback failed")


@pytest.mark.asyncio
async def test_ui_callback_failure_does_not_block_following_frame() -> None:
    """Facade isolates on_ui_chat_reassembled like other hooks; connection stays up."""
    hook = FailingUiHook()
    frames = [
        _complete_ui_player_frame("Alice", "first"),
        _player_message_frame("Alice", "second"),
    ]
    await McbeServerFacade(hook=hook)._on_connection(FakeWebSocket(frames))
    assert [event.message for event in hook.player_messages] == ["second"]
    # Natural disconnect still runs — hook failure must not tear the connection early.
    assert len(hook.disconnected) == 1


# --------------------------------------------------------------------------- #
# 4. player command routes through handler + player hook
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_routes_player_message_through_handler_and_hook() -> None:
    from mcbe_ws_sdk.command import CommandRegistry

    hook = RecordingHook()
    registry = CommandRegistry({"#x": "demo"})
    facade = McbeServerFacade(hook=hook, sink=RecordingSink(), registry=registry)

    await facade._on_connection(
        FakeWebSocket(
            frames=[
                _player_message_frame("Alice", "hello world"),
                _player_message_frame("Alice", "#x payload"),
            ]
        ),
    )

    assert len(hook.player_messages) == 2
    assert hook.player_messages[0].message == "hello world"
    assert hook.player_messages[0].sender == "Alice"
    assert hook.parsed[0] is None
    assert hook.parsed[1] is not None


@pytest.mark.asyncio
async def test_hook_exception_does_not_end_connection() -> None:
    hook = ExplodingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())

    await facade._on_connection(
        FakeWebSocket(
            frames=[
                _player_message_frame("Alice", "first"),
                _player_message_frame("Alice", "second"),
            ]
        ),
    )

    # First raises inside the hook; second must still be delivered.
    assert [event.message for event in hook.player_messages] == ["second"]
    assert len(hook.disconnected) == 1


@pytest.mark.asyncio
async def test_facade_emits_typed_player_event() -> None:
    facade = McbeServerFacade(hook=RecordingHook(), sink=RecordingSink())
    seen: list[tuple[ConnectionState, PlayerMessageEvent]] = []

    async def on_player(state: ConnectionState, event: PlayerMessageEvent) -> None:
        seen.append((state, event))

    facade.manager.event_bus.subscribe(WsEventType.PLAYER_MESSAGE, on_player, weak=False)
    await facade._on_connection(FakeWebSocket([_player_message_frame("Alice", "hello")]))

    assert seen[0][1].sender == "Alice"


@pytest.mark.asyncio
async def test_facade_emits_typed_error_and_command_response_events() -> None:
    facade = McbeServerFacade(hook=RecordingHook(), sink=RecordingSink())
    seen_errors: list[tuple[ConnectionState, MinecraftErrorFrame]] = []
    seen_responses: list[tuple[ConnectionState, MinecraftCommandResponse]] = []

    async def on_error(state: ConnectionState, event: MinecraftErrorFrame) -> None:
        seen_errors.append((state, event))

    async def on_response(state: ConnectionState, event: MinecraftCommandResponse) -> None:
        seen_responses.append((state, event))

    facade.manager.event_bus.subscribe(WsEventType.ERROR, on_error, weak=False)
    facade.manager.event_bus.subscribe(WsEventType.COMMAND_RESPONSE, on_response, weak=False)

    await facade._on_connection(
        FakeWebSocket(
            [
                _error_frame("err-1"),
                _command_response_frame("req-1"),
            ]
        )
    )

    assert seen_errors[0][1].request_id == "err-1"
    assert seen_responses[0][1].request_id == "req-1"


@pytest.mark.asyncio
async def test_facade_emits_bridge_and_ui_semantic_events() -> None:
    facade = McbeServerFacade(hook=RecordingHook(), sink=RecordingSink())
    bridge_chunks: list[tuple[ConnectionState, AddonBridgeChunk]] = []
    ui_chunks: list[tuple[ConnectionState, UiChatChunk]] = []
    ui_messages: list[tuple[ConnectionState, UiChatMessage]] = []

    async def on_connected(state: ConnectionState) -> None:
        session = facade.addon._session_for(state.id)
        session.create_request(capability="demo", payload={"x": 1})

    async def on_bridge(state: ConnectionState, chunk: AddonBridgeChunk) -> None:
        bridge_chunks.append((state, chunk))

    async def on_ui_chunk(state: ConnectionState, chunk: UiChatChunk) -> None:
        ui_chunks.append((state, chunk))

    async def on_ui_message(state: ConnectionState, message: UiChatMessage) -> None:
        ui_messages.append((state, message))

    facade.manager.event_bus.subscribe(WsEventType.CONNECTED, on_connected, weak=False)
    facade.manager.event_bus.subscribe(WsEventType.BRIDGE_CHUNK, on_bridge, weak=False)
    facade.manager.event_bus.subscribe(WsEventType.UI_CHAT_CHUNK, on_ui_chunk, weak=False)
    facade.manager.event_bus.subscribe(WsEventType.UI_CHAT_REASSEMBLED, on_ui_message, weak=False)

    with patch(
        "mcbe_ws_sdk.addon.session.uuid4",
        return_value=UUID("00000000-0000-0000-0000-000000000001"),
    ):
        await facade._on_connection(
            FakeWebSocket(
                [
                    _player_message_frame(
                        "MCBEWS_BRIDGE",
                        'MCBEWS|BRIDGE|addon-00000000000000000000000000000001|1/1|{"ok":true}',
                    ),
                    _player_message_frame(
                        "MCBEWS_BRIDGE",
                        'MCBEWS|UI_CHAT|m1|1/1|{"player":"Steve","message":"hello"}',
                    ),
                ]
            )
        )

    assert bridge_chunks[0][1].request_id == "addon-00000000000000000000000000000001"
    assert ui_chunks[0][1].msg_id == "m1"
    assert ui_messages[0][1].message == "hello"


@pytest.mark.asyncio
async def test_raw_outbound_event_includes_state() -> None:
    facade = McbeServerFacade(hook=RecordingHook(), sink=RecordingSink())
    seen: list[tuple[ConnectionState, str]] = []

    async def on_outbound(state: ConnectionState, payload: str) -> None:
        seen.append((state, payload))

    facade.manager.event_bus.subscribe(WsEventType.RAW_OUTBOUND, on_outbound, weak=False)
    await facade._on_connection(FakeWebSocket([]))

    assert len(seen) == 2
    assert seen[0][1] == '{"Result":"true"}'


# --------------------------------------------------------------------------- #
# 6. commandResponse frame drives the hook with the extracted request id
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_command_response_calls_hook() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())

    frame = _command_response_frame("req-42", status_code=0, status_message="done")
    await facade._on_connection(FakeWebSocket(frames=[frame]))

    assert len(hook.command_responses) == 1
    assert hook.command_responses[0].request_id == "req-42"
    assert hook.command_responses[0].body == {"statusCode": 0, "statusMessage": "done"}


@pytest.mark.asyncio
async def test_command_response_preserves_complete_body() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())
    frame = _command_response_frame(
        "r-1",
        status_code=0,
        status_message="ok",
        extra_body={"details": {"count": 2}},
    )

    await facade._on_connection(FakeWebSocket([frame]))

    assert hook.command_responses[0].body["details"] == {"count": 2}


@pytest.mark.asyncio
async def test_command_response_preserves_full_envelope_extensions() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())
    frame = _command_response_frame(
        "r-2",
        status_code=0,
        status_message="ok",
        extra_body={"details": {"count": 2}},
        extra_header={"futureHeader": {"x": 1}},
        extra_envelope={"futureEnvelope": True},
    )

    await facade._on_connection(FakeWebSocket([frame]))

    response = hook.command_responses[0]
    assert response.header["futureHeader"] == {"x": 1}
    assert response.model_dump()["futureEnvelope"] is True
    assert response.body["details"] == {"count": 2}


@pytest.mark.asyncio
async def test_error_frame_calls_typed_error_hook() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())

    await facade._on_connection(
        FakeWebSocket(frames=[_error_frame("err-7", body={"statusMessage": "boom", "extra": 1})])
    )

    assert len(hook.errors) == 1
    assert hook.errors[0].request_id == "err-7"
    assert hook.errors[0].header["code"] == 500
    assert hook.errors[0].body == {"statusMessage": "boom", "extra": 1}


@pytest.mark.asyncio
async def test_error_frame_preserves_full_envelope_extensions() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())

    await facade._on_connection(
        FakeWebSocket(
            frames=[
                _error_frame(
                    "err-8",
                    body={"statusMessage": "boom", "extra": 2},
                    extra_envelope={"futureEnvelope": {"x": 1}},
                )
            ]
        )
    )

    error = hook.errors[0]
    assert error.request_id == "err-8"
    assert error.model_dump()["futureEnvelope"] == {"x": 1}
    assert error.body["extra"] == 2


# --------------------------------------------------------------------------- #
# 7. outbound message reaches the provided sink over the real loop
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sink_receives_outbound_text() -> None:
    sink = RecordingSink()
    facade = McbeServerFacade(hook=RecordingHook(), sink=sink)

    state = await facade.manager.create_connection(
        connection_id=UUID(int=5),
        send_payload=_send_noop,
    )
    assert state.response_queue is not None

    msg = OutboundText(content="hello world", sequence=1)
    await state.response_queue.put(msg)

    # Yield to let the response-sender coroutine drain the queue.
    for _ in range(30):
        await asyncio.sleep(0.01)
        if len(sink.envelopes) == 1:
            break

    assert len(sink.envelopes) == 1
    env = sink.envelopes[0][1]
    assert env.kind is ResponseKind.OUTBOUND_TEXT
    assert isinstance(env.payload, OutboundText)
    assert env.payload.content == "hello world"

    await facade.manager.drop_connection(state.id)
    assert state.id not in facade.manager._sender_tasks


# --------------------------------------------------------------------------- #
# 8 (bonus). stop()/shutdown_all leaves no lingering sender tasks
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_shutdown_all_on_stop_cancels_senders() -> None:
    facade = McbeServerFacade(hook=RecordingHook(), sink=RecordingSink())

    await facade.manager.create_connection(
        connection_id=UUID(int=9),
        send_payload=_send_noop,
    )
    assert UUID(int=9) in facade.manager._sender_tasks
    # The sender task should be alive (waiting on the queue).
    assert facade.manager._sender_tasks[UUID(int=9)].done() is False

    await facade.manager.shutdown_all()

    assert facade.manager.connection_count == 0
    assert facade.manager._sender_tasks == {}


@pytest.mark.asyncio
async def test_facade_lifetime_is_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, dict[str, object]]] = []

    class FakeServerContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    def fake_serve(handler: object, host: str, port: int, **kwargs: object) -> FakeServerContext:
        calls.append((host, port, dict(kwargs)))
        return FakeServerContext()

    monkeypatch.setattr("mcbe_ws_sdk.gateway.server_facade.websockets.serve", fake_serve)
    facade = McbeServerFacade()
    await facade.stop()
    await facade.run_lifetime(host="127.0.0.1", port=0)
    assert calls[0][0:2] == ("127.0.0.1", 0)
    assert facade._server is None
    with pytest.raises(FacadeLifecycleError, match="single-use"):
        await facade.run_lifetime(host="127.0.0.1", port=0)


# --------------------------------------------------------------------------- #
# Sanity: the default facade wiring matches the documented contract
# --------------------------------------------------------------------------- #


def test_default_facade_wiring_and_default_commands() -> None:
    facade = McbeServerFacade()
    # Constructor omits the removed inbound-only seams.
    import inspect

    params = inspect.signature(McbeServerFacade.__init__).parameters
    assert "broker" not in params
    assert "capabilities" not in params

    # Default sink is the non-crashing DefaultResponseSink.
    assert isinstance(facade.manager.sink, DefaultResponseSink)
    # Default registry is empty — no commands registered by default.
    assert facade.handler.command_registry.list_all_commands() == []
