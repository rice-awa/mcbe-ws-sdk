"""Reliable dispatch and connection lifecycle for the single ``McbeServer``.

The queue-backed transport answers MCBEWS/2 negotiation automatically, so a
test only needs to press ``hook.connected`` to know admission finished. Tests
that exercise rejection set ``auto_negotiate=False``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest

from mcbe_ws_sdk.config import (
    DispatchSettings,
    GatewaySettings,
    SendSchedulerSettings,
)
from mcbe_ws_sdk.errors import SendClosedError
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX, ReassemblyPool, fragment
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)
from mcbe_ws_sdk.server import McbeServer

_NEGOTIATION_PREFIX = "scriptevent mcbews:negotiate_v2 "
_DEFAULT_FEATURES = "metadata/1,notifications/1,reverse/1,stream/1"


class QueueWebSocket:
    """Async-iterable transport fed by explicit ``push`` calls.

    When ``auto_negotiate`` is true, the transport answers the SDK's
    ``MCBEWS2H`` hello and ``MCBEWS2C`` confirmation with a valid challenge.
    """

    def __init__(self, *, auto_negotiate: bool = True) -> None:
        self.sent: list[str] = []
        self.on_send: Callable[[str], Awaitable[None]] | None = None
        self.auto_negotiate = auto_negotiate
        self.close_code: int | None = None
        self.close_reason: str = ""
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, payload: str) -> None:
        self.sent.append(payload)
        if self.auto_negotiate:
            await self._answer_negotiation(payload)
        if self.on_send is not None:
            await self.on_send(payload)

    async def _answer_negotiation(self, payload: str) -> None:
        try:
            command = json.loads(payload).get("body", {}).get("commandLine", "")
        except (json.JSONDecodeError, AttributeError, TypeError):
            return
        if not command.startswith(_NEGOTIATION_PREFIX):
            return
        parts = command[len(_NEGOTIATION_PREFIX) :].split("|")
        if parts[0] == "MCBEWS2H":
            attempt = parts[1]
            epoch = f"e{attempt[:22]}"
            await self.push(
                _player_frame(
                    "MCBEWS_BRIDGE",
                    f"MCBEWS2N|{attempt}|{epoch}|2|{_DEFAULT_FEATURES}",
                )
            )
        elif parts[0] == "MCBEWS2C":
            await self.push(
                _player_frame(
                    "MCBEWS_BRIDGE",
                    f"MCBEWS2N|{parts[1]}|{parts[2]}|ready",
                )
            )

    async def push(self, frame: str) -> None:
        await self._incoming.put(frame)

    async def close(self, code: int | None = None, reason: str = "") -> None:
        if self.close_code is not None:
            return
        self.close_code = code
        self.close_reason = reason
        await self._incoming.put(None)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter_frames()

    async def _iter_frames(self) -> AsyncIterator[str]:
        while True:
            frame = await self._incoming.get()
            if frame is None:
                return
            yield frame


class RecordingHook(NoOpHook):
    def __init__(self) -> None:
        self.connected = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.state: ConnectionState | None = None
        self.messages: list[PlayerMessageEvent] = []
        self.errors: list[MinecraftErrorFrame] = []
        self.error_seen = asyncio.Event()
        self.on_player: Callable[[ConnectionState, PlayerMessageEvent], Awaitable[None]] | None = (
            None
        )

    async def on_connected(self, state: ConnectionState) -> None:
        self.state = state
        self.connected.set()

    async def on_disconnected(self, state: ConnectionState) -> None:
        self.disconnected.set()

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: object | None = None,
    ) -> None:
        self.messages.append(player_event)
        if self.on_player is not None:
            await self.on_player(state, player_event)

    async def on_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
        return None

    async def on_error(self, state: ConnectionState, error: MinecraftErrorFrame) -> None:
        self.errors.append(error)
        self.error_seen.set()


def _player_frame(sender: str, message: str) -> str:
    return json.dumps(
        {
            "header": {"eventName": "PlayerMessage", "requestId": "evt"},
            "body": {"sender": sender, "message": message},
        }
    )


def _command_response_frame(request_id: str) -> str:
    return json.dumps(
        {
            "header": {"requestId": request_id, "messagePurpose": "commandResponse"},
            "body": {"statusCode": 0, "statusMessage": "ok"},
        }
    )


def _error_frame(request_id: str) -> str:
    return json.dumps(
        {
            "header": {"requestId": request_id, "messagePurpose": "error", "code": 7},
            "body": {"statusMessage": "denied"},
        }
    )


async def _start(
    hook: RecordingHook,
    *,
    dispatch: DispatchSettings | None = None,
    send: SendSchedulerSettings | None = None,
    bridge: V2Bridge | None = None,
    websocket: QueueWebSocket | None = None,
) -> tuple[McbeServer, QueueWebSocket, asyncio.Task[None]]:
    settings = GatewaySettings(
        dispatch=dispatch or DispatchSettings(),
        send=send or SendSchedulerSettings(),
    )
    server = McbeServer(
        settings=settings,
        hook=hook,
        sink=DefaultResponseSink(),
        v2=bridge,
    )
    websocket = websocket or QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), timeout=2)
    return server, websocket, task


async def _stop(websocket: QueueWebSocket, task: asyncio.Task[None]) -> None:
    await websocket.close()
    await asyncio.wait_for(task, timeout=2)


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    iterations = int(timeout / 0.005)
    for _ in range(iterations):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached before the timeout")


def _require_state(hook: RecordingHook) -> ConnectionState:
    assert hook.state is not None
    assert hook.state.runtime is not None
    return hook.state


def _outbound_v2_messages(websocket: QueueWebSocket) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    pool = ReassemblyPool()
    for payload in websocket.sent:
        try:
            command = json.loads(payload).get("body", {}).get("commandLine", "")
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        if not command.startswith(COMMAND_PREFIX):
            continue
        message = pool.receive("test", "server", command[len(COMMAND_PREFIX) :], "down")
        if message is not None:
            messages.append(message)
    return messages


async def _send_v2(server: McbeServer, state: ConnectionState, message: dict[str, Any]) -> None:
    endpoint = server.v2.connection(state.id)
    assert endpoint is not None
    for frame in fragment(message, f"a.{endpoint.epoch}.{message.get('seq', 0) + 1}", "up"):
        await endpoint.receive(frame, source="MCBEWS_BRIDGE")


# --------------------------------------------------------------------------- #
# Scheduler integration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_handshake_subscribe_and_host_sends_use_one_scheduler() -> None:
    hook = RecordingHook()
    host_sent = asyncio.Event()

    async def on_connected(state: ConnectionState) -> None:
        hook.state = state
        hook.connected.set()
        assert state.send_payload is not None
        await state.send_payload("host payload")
        host_sent.set()

    hook.on_connected = on_connected  # type: ignore[method-assign]
    server, websocket, task = await _start(hook)

    await asyncio.wait_for(host_sent.wait(), timeout=2)

    assert websocket.sent[0] == '{"Result":"true"}'
    assert json.loads(websocket.sent[1])["header"]["messagePurpose"] == "subscribe"
    assert "host payload" in websocket.sent
    state = _require_state(hook)
    await _wait_until(lambda: state.runtime.scheduler.sent_count == len(websocket.sent))

    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# Per-player lanes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_blocked_player_does_not_block_other_player_or_command_reply() -> None:
    hook = RecordingHook()
    alice_started = asyncio.Event()
    alice_release = asyncio.Event()
    bob_done = asyncio.Event()
    command_sent = asyncio.Event()
    command_ids: list[str] = []

    async def on_player(state: ConnectionState, event: PlayerMessageEvent) -> None:
        if event.sender == "Alice":
            alice_started.set()
            await alice_release.wait()
        else:
            bob_done.set()

    hook.on_player = on_player
    server, websocket, task = await _start(hook)
    state = _require_state(hook)
    assert state.command_client is not None

    async def on_send(payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        if data.get("body", {}).get("commandLine") == "say hi":
            command_ids.append(str(data["header"]["requestId"]))
            command_sent.set()

    websocket.on_send = on_send

    command_task = asyncio.create_task(state.command_client.run("say hi"))
    await asyncio.wait_for(command_sent.wait(), timeout=2)

    await websocket.push(_player_frame("Alice", "slow"))
    await asyncio.wait_for(alice_started.wait(), timeout=2)

    await websocket.push(_player_frame("Bob", "fast"))
    await asyncio.wait_for(bob_done.wait(), timeout=2)

    await websocket.push(_command_response_frame(command_ids[0]))
    response = await asyncio.wait_for(command_task, timeout=2)
    assert response.request_id == command_ids[0]
    assert not alice_release.is_set()

    alice_release.set()
    await _stop(websocket, task)


@pytest.mark.asyncio
async def test_full_business_queue_rejects_and_replies_still_pair() -> None:
    hook = RecordingHook()
    alice_started = asyncio.Event()
    alice_release = asyncio.Event()
    command_sent = asyncio.Event()
    command_ids: list[str] = []

    async def on_player(state: ConnectionState, event: PlayerMessageEvent) -> None:
        if event.sender == "Alice":
            alice_started.set()
            await alice_release.wait()

    hook.on_player = on_player
    server, websocket, task = await _start(
        hook,
        dispatch=DispatchSettings(
            business_queue_maxsize=1,
            max_concurrent_business_tasks=1,
        ),
    )
    state = _require_state(hook)
    assert state.command_client is not None

    async def on_send(payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        if data.get("body", {}).get("commandLine") == "say hi":
            command_ids.append(str(data["header"]["requestId"]))
            command_sent.set()

    websocket.on_send = on_send

    command_task = asyncio.create_task(state.command_client.run("say hi"))
    await asyncio.wait_for(command_sent.wait(), timeout=2)

    await websocket.push(_player_frame("Alice", "block"))
    await asyncio.wait_for(alice_started.wait(), timeout=2)

    for index in range(5):
        await websocket.push(_player_frame("Bob", f"flood-{index}"))

    runtime = state.runtime
    assert runtime is not None
    await _wait_until(lambda: runtime.business.rejected_count > 0)

    await websocket.push(_command_response_frame(command_ids[0]))
    response = await asyncio.wait_for(command_task, timeout=2)
    assert response.request_id == command_ids[0]

    alice_release.set()
    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# Teardown
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_disconnect_reaps_business_tasks_and_rejects_late_sends() -> None:
    hook = RecordingHook()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def on_player(state: ConnectionState, event: PlayerMessageEvent) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    hook.on_player = on_player
    server, websocket, task = await _start(hook)
    state = _require_state(hook)
    runtime = state.runtime
    assert runtime is not None

    await websocket.push(_player_frame("Alice", "hang"))
    await asyncio.wait_for(started.wait(), timeout=2)

    await _stop(websocket, task)

    assert cancelled.is_set()
    assert runtime.is_closed
    assert runtime.business.task_count == 0
    assert runtime.business.active_count == 0
    assert runtime.business.pending_count == 0
    assert runtime.business.lane_count == 0
    assert server.manager.connection_count == 0

    with pytest.raises(SendClosedError):
        await runtime.send_raw("late")
    with pytest.raises(SendClosedError):
        assert state.send_payload is not None
        await state.send_payload("late")

    await server.manager.drop_connection(state.id)
    await runtime.close()
    assert server.manager.connection_count == 0


@pytest.mark.asyncio
async def test_disconnect_joins_cancelled_manager_drop() -> None:
    started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    disconnected = asyncio.Event()
    cleanup_observed: list[bool] = []

    class ClosingHook(RecordingHook):
        async def on_disconnected(self, state: ConnectionState) -> None:
            runtime = state.runtime
            cleanup_observed.append(
                runtime is not None
                and runtime.scheduler.is_closed
                and runtime.business.task_count == 0
                and runtime.requests.pending_count == 0
            )
            disconnected.set()

    async def on_player(state: ConnectionState, event: PlayerMessageEvent) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            await release.wait()

    hook = ClosingHook()
    hook.on_player = on_player
    server, websocket, task = await _start(hook)
    state = _require_state(hook)
    await websocket.push(_player_frame("Alice", "blocked"))
    await asyncio.wait_for(started.wait(), 2)
    first = asyncio.create_task(server.manager.drop_connection(state.id))
    try:
        await asyncio.wait_for(cancelling.wait(), 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        drop_entered = asyncio.Event()
        original_drop = server.manager.drop_connection

        async def observed_drop(connection_id: UUID, *, notify: bool | None = None) -> None:
            drop_entered.set()
            await original_drop(connection_id, notify=notify)

        server.manager.drop_connection = observed_drop  # type: ignore[method-assign]
        await websocket.close()
        await asyncio.wait_for(drop_entered.wait(), 2)
        assert not task.done()
        assert not disconnected.is_set()
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert cleanup_observed == [True]
    assert server.manager._drop_tasks == {}
    assert server.manager._sender_tasks == {}


@pytest.mark.asyncio
async def test_send_during_drop_window_fails_instead_of_reporting_success() -> None:
    hook = RecordingHook()
    server, websocket, task = await _start(hook)
    state = _require_state(hook)
    runtime = state.runtime
    assert runtime is not None
    assert state.send_payload is not None

    sent_before = runtime.scheduler.sent_count
    server.manager._connections.pop(state.id)
    with pytest.raises(SendClosedError):
        await state.send_payload("late")
    assert "late" not in websocket.sent
    assert runtime.scheduler.sent_count == sent_before

    server.manager._connections[state.id] = state
    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# Lanes and ordering
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_same_player_stays_ordered_while_control_lane_runs() -> None:
    hook = RecordingHook()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def on_player(state: ConnectionState, event: PlayerMessageEvent) -> None:
        order.append(event.message)
        if event.message == "first":
            first_started.set()
            await release_first.wait()

    hook.on_player = on_player
    server, websocket, task = await _start(hook)

    await websocket.push(_player_frame("Alice", "first"))
    await asyncio.wait_for(first_started.wait(), timeout=2)
    await websocket.push(_player_frame("Alice", "second"))
    await websocket.push(_error_frame("err-1"))

    await asyncio.wait_for(hook.error_seen.wait(), timeout=2)
    assert order == ["first"]

    release_first.set()
    await _wait_until(lambda: order == ["first", "second"])
    await _stop(websocket, task)


@pytest.mark.asyncio
async def test_shared_world_lane_serialises_operations() -> None:
    hook = RecordingHook()
    server, websocket, task = await _start(hook)
    state = _require_state(hook)
    runtime = state.runtime
    assert runtime is not None

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def operation(name: str, *, block: bool) -> None:
        async with runtime.lane("world"):
            order.append(name)
            if block:
                first_started.set()
                await release_first.wait()

    first = asyncio.create_task(operation("first", block=True))
    await asyncio.wait_for(first_started.wait(), timeout=2)
    second = asyncio.create_task(operation("second", block=False))
    await asyncio.sleep(0.02)
    assert order == ["first"]

    release_first.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
    assert order == ["first", "second"]

    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# Cleanup and request isolation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_repeated_connections_return_to_initial_state() -> None:
    hook = RecordingHook()
    server, websocket, task = await _start(hook)
    first_state = _require_state(hook)

    await _stop(websocket, task)

    assert server.manager.connection_count == 0
    assert server.manager._sender_tasks == {}
    assert server.v2.connection(first_state.id) is None

    for _ in range(2):
        hook = RecordingHook()
        server, websocket, task = await _start(hook)
        new_state = _require_state(hook)
        assert new_state.id != first_state.id
        await _stop(websocket, task)
        assert server.manager.connection_count == 0
        assert server.manager._sender_tasks == {}


@pytest.mark.asyncio
async def test_command_response_does_not_resolve_v2_request_with_same_id() -> None:
    hook = RecordingHook()
    server, websocket, task = await _start(hook)
    state = _require_state(hook)
    endpoint = server.v2.connection(state.id)
    assert endpoint is not None

    request = asyncio.create_task(endpoint.request("echo", {"value": 1}))
    async with asyncio.timeout(2):
        while not _outbound_v2_messages(websocket):
            await asyncio.sleep(0)
    sent = _outbound_v2_messages(websocket)[-1]
    request_id = sent["id"]

    await websocket.push(_command_response_frame(request_id))
    await asyncio.sleep(0.02)
    assert not request.done()
    assert state.requests is not None
    assert state.requests.get("addon.v2", request_id) is not None

    await _send_v2(
        server,
        state,
        {
            "v": "MCBEWS/2",
            "kind": "response",
            "id": request_id,
            "result": {"ok": True},
        },
    )
    assert (await asyncio.wait_for(request, 2))["result"] == {"ok": True}

    await _stop(websocket, task)


@pytest.mark.asyncio
async def test_hook_can_await_v2_reply_without_blocking_the_reader() -> None:
    hook = RecordingHook()
    started = asyncio.Event()
    finished = asyncio.Event()
    result_holder: dict[str, Any] = {}
    server: McbeServer | None = None
    websocket: QueueWebSocket | None = None

    async def on_connected(state: ConnectionState) -> None:
        assert server is not None and websocket is not None
        started.set()
        endpoint = server.v2.connection(state.id)
        assert endpoint is not None
        request = asyncio.create_task(endpoint.request("py.echo", {"value": 1}))
        async with asyncio.timeout(2):
            while not _outbound_v2_messages(websocket):
                await asyncio.sleep(0)
        sent = _outbound_v2_messages(websocket)[-1]
        await _send_v2(
            server,
            state,
            {
                "v": "MCBEWS/2",
                "kind": "response",
                "id": sent["id"],
                "result": sent["params"],
            },
        )
        result_holder["result"] = await request
        finished.set()

    hook.on_connected = on_connected  # type: ignore[method-assign]
    server = McbeServer(
        settings=GatewaySettings(),
        hook=hook,
        sink=DefaultResponseSink(),
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(started.wait(), 2)
    try:
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        await _stop(websocket, task)

    assert result_holder["result"]["result"] == {"value": 1}


def test_gateway_settings_have_no_dispatch_mode() -> None:
    assert GatewaySettings().dispatch.business_queue_maxsize == 256
    assert not hasattr(GatewaySettings().dispatch, "mode")
