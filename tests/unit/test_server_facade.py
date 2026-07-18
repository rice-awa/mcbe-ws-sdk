"""Tests for the gateway server facade (server_facade.py).

Drives the facade two complementary ways, exactly as the scope doc prescribes:

* **In-process fake transport** (tests 2–6): build a facade, then call
  ``facade._on_connection(fake_ws)`` where ``fake_ws`` is a tiny async-iterable
  whose ``send`` records outbound frames. This exercises the real routing
  loop (parse → branch → hook call) without binding any port or importing the
  ``websockets`` runtime.
* **Real ``websockets`` lifetime** (tests 1, 7, 8): ``run_lifetime`` on an
  OS-assigned ephemeral port (``port=0``), then ``stop()`` + await to assert the
  server closes and ``shutdown_all`` runs cleanly with no lingering senders.

All assertions go through the ``EventBus`` + a ``RecordingSink`` /
``RecordingHook`` pair just like ``tests/unit/test_connection_manager.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest

from mcbe_ws_sdk.addon.service import AddonBridgeService
from mcbe_ws_sdk.command.registry import DEFAULT_COMMANDS
from mcbe_ws_sdk.config import AddonBridgeSettings
from mcbe_ws_sdk.gateway import WsEventType
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.messages import StreamChunk, SystemNotification
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.gateway.sink import (
    ResponseKind,
    RouteEnvelope,
    SilentResponseSink,
)
from mcbe_ws_sdk.protocol.minecraft import PlayerMessageEvent

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


class RecordingSink(SilentResponseSink):
    """Sink that captures routed envelopes instead of only logging/raising."""

    def __init__(self) -> None:
        self.envelopes: list[tuple[UUID, RouteEnvelope]] = []

    async def on_stream_chunk(self, state: ConnectionState, chunk: StreamChunk) -> None:
        self.envelopes.append((state.id, RouteEnvelope(ResponseKind.STREAM_CHUNK, chunk)))

    async def on_system_notification(
        self, state: ConnectionState, note: SystemNotification,
    ) -> None:
        self.envelopes.append((state.id, RouteEnvelope(ResponseKind.SYSTEM_NOTIFICATION, note)))


class RecordingHook(NoOpHook):
    """Hook that records every call; consumes bridge requests; logs commands."""

    def __init__(self) -> None:
        self.connected: list[UUID] = []
        self.disconnected: list[UUID] = []
        self.player_messages: list[PlayerMessageEvent] = []
        self.bridge_requests: list[str] = []
        self.ui_chat_reassembled: list[tuple[str, str]] = []
        self.command_responses: list[tuple[str, dict[str, Any]]] = []

    async def on_connected(self, state: ConnectionState) -> None:
        self.connected.append(state.id)

    async def on_disconnected(self, state: ConnectionState) -> None:
        self.disconnected.append(state.id)

    async def on_player_message(
        self, state: ConnectionState, player_event: PlayerMessageEvent,
    ) -> bool:
        self.player_messages.append(player_event)
        return False

    async def on_bridge_message(self, state: ConnectionState, request: Any) -> bool:
        self.bridge_requests.append(request.capability)
        return True  # consumed — mirrors a host CapabilityRegistry seam

    async def on_ui_chat_reassembled(
        self, state: ConnectionState, player_name: str, message: str,
    ) -> None:
        self.ui_chat_reassembled.append((player_name, message))

    async def on_command_response(
        self, state: ConnectionState, request_id: str, response: dict[str, Any],
    ) -> None:
        self.command_responses.append((request_id, response))


class RecordingAddon(AddonBridgeService):
    """Addon double that records routed AP frame deliveries."""

    def __init__(self, settings: AddonBridgeSettings | None = None) -> None:
        super().__init__(settings if settings is not None else AddonBridgeSettings())
        self.handled: list[tuple[UUID, str, str]] = []

    def handle_player_message(self, connection_id: UUID, sender: str, message: str) -> bool:
        self.handled.append((connection_id, sender, message))
        return super().handle_player_message(connection_id, sender, message)


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


def _command_response_frame(
    request_id: str,
    *,
    status_code: int = 0,
    status_message: str = "命令执行成功",
) -> str:
    """Build a ``commandResponse`` frame matching the facade's shape heuristic."""
    return json.dumps(
        {
            "header": {"requestId": request_id, "messagePurpose": "commandResponse"},
            "body": {"statusCode": status_code, "statusMessage": status_message},
        }
    )


def _send_noop(payload: str) -> Any:  # pragma: no cover - transport stub
    f: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    f.set_result(None)
    return f


# --------------------------------------------------------------------------- #
# 1. run_lifetime binds and stops cleanly
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_lifetime_binds_and_stops_cleanly() -> None:
    sink = RecordingSink()
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=sink)

    task = asyncio.create_task(facade.run_lifetime(host="127.0.0.1", port=0))
    # Let it bind.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if facade._server is not None and getattr(facade._server, "sockets", None):
            break

    assert facade._server is not None
    sockets = getattr(facade._server, "sockets", None)
    assert sockets, "server never bound to an ephemeral port"

    await facade.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert facade._server is None
    assert facade.manager.connection_count == 0


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
        WsEventType.CONNECTED, lambda s: connected.append(s.id), weak=False,
    )
    facade.manager.event_bus.subscribe(
        WsEventType.DISCONNECTED, lambda s: disconnected.append(s.id), weak=False,
    )

    await facade._on_connection(FakeWebSocket(frames=[]))

    assert len(connected) == 1
    assert connected == disconnected == [hook.connected[0]]
    assert len(hook.connected) == 1
    assert len(hook.disconnected) == 1
    assert facade.manager.connection_count == 0


# --------------------------------------------------------------------------- #
# 3. bridge-response frames route through the addon, not the player hook
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_routes_bridge_response_through_addon() -> None:
    hook = RecordingHook()
    addon = RecordingAddon()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink(), addon=addon)

    resp = 'MCBEAI|RESP|req-7|1/1|{"ok": true}'
    ws = FakeWebSocket(frames=[_player_message_frame("MCBEAI_TOOL", resp)])

    await facade._on_connection(ws)

    assert addon.handled and addon.handled[0][1] == "MCBEAI_TOOL"
    assert addon.handled[0][2] == resp
    assert hook.player_messages == []  # NOT handed to the player hook


# --------------------------------------------------------------------------- #
# 4. inbound bridge capability request routes to the hook
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_routes_inbound_bridge_request_to_hook() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())

    body = _player_message_frame(
        "Alice",
        'scriptevent mcbeai:bridge_request {"request_id":"r1",'
        '"capability":"greet","payload":{"name":"world"}}',
    )
    await facade._on_connection(FakeWebSocket(frames=[body]))

    assert hook.bridge_requests == ["greet"]
    assert hook.player_messages == []  # did NOT fall through to the player hook

    # CapabilityRegistry default is wired on the facade even with NoOpHook-unused.
    assert facade._capabilities is not None


# --------------------------------------------------------------------------- #
# 5. player command routes through handler + player hook
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_routes_command_through_handler_and_hook() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())

    await facade._on_connection(
        FakeWebSocket(frames=[_player_message_frame("Alice", "帮助 状态")]),
    )

    assert len(hook.player_messages) == 1
    assert hook.player_messages[0].message == "帮助 状态"
    assert hook.player_messages[0].sender == "Alice"
    assert hook.bridge_requests == []  # not mistaken for a capability request
    # The command is registered in the default registry.
    parsed = facade.handler.parse_typed_command("帮助 状态")
    assert parsed is not None and parsed.type == "help"


# --------------------------------------------------------------------------- #
# 6. commandResponse frame drives the hook with the extracted request id
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_command_response_calls_hook() -> None:
    hook = RecordingHook()
    facade = McbeServerFacade(hook=hook, sink=RecordingSink())

    frame = _command_response_frame("req-42", status_code=0, status_message="done")
    await facade._on_connection(FakeWebSocket(frames=[frame]))

    assert hook.command_responses == [("req-42", {"statusCode": 0, "statusMessage": "done"})]


# --------------------------------------------------------------------------- #
# 7. outbound stream chunk reaches the provided sink over the real loop
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sink_receives_stream_chunk() -> None:
    sink = RecordingSink()
    facade = McbeServerFacade(hook=RecordingHook(), sink=sink)

    state = await facade.manager.create_connection(
        connection_id=UUID(int=5), send_payload=_send_noop,
    )
    assert state.response_queue is not None

    chunk = StreamChunk(chunk_type="content", content="hello world", sequence=1)
    await state.response_queue.put(chunk)

    # Yield to let the response-sender coroutine drain the queue.
    for _ in range(30):
        await asyncio.sleep(0.01)
        if len(sink.envelopes) == 1:
            break

    assert len(sink.envelopes) == 1
    env = sink.envelopes[0][1]
    assert env.kind is ResponseKind.STREAM_CHUNK
    assert isinstance(env.payload, StreamChunk)
    assert env.payload.content == "hello world"

    await facade.manager.drop_connection(state.id)
    await asyncio.sleep(0.05)


# --------------------------------------------------------------------------- #
# 8 (bonus). stop()/shutdown_all leaves no lingering sender tasks
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_shutdown_all_on_stop_cancels_senders() -> None:
    facade = McbeServerFacade(hook=RecordingHook(), sink=RecordingSink())

    await facade.manager.create_connection(
        connection_id=UUID(int=9), send_payload=_send_noop,
    )
    assert UUID(int=9) in facade.manager._sender_tasks
    # The sender task should be alive (waiting on the queue).
    assert facade.manager._sender_tasks[UUID(int=9)].done() is False

    await facade.manager.shutdown_all()

    assert facade.manager.connection_count == 0
    assert facade.manager._sender_tasks == {}


# --------------------------------------------------------------------------- #
# Sanity: the default facade wiring matches the documented contract
# --------------------------------------------------------------------------- #


def test_default_facade_wiring_and_default_commands() -> None:
    facade = McbeServerFacade()
    # Constructor omits ``broker`` and includes ``capabilities``.
    import inspect

    params = inspect.signature(McbeServerFacade.__init__).parameters
    assert "broker" not in params
    assert "capabilities" in params

    # Default sink is the non-crashing SilentResponseSink.
    assert isinstance(facade.manager.sink, SilentResponseSink)
    # Default registry loads the canonical command table.
    assert facade.handler.parse_typed_command("帮助") is not None
    assert "#登录" in DEFAULT_COMMANDS
