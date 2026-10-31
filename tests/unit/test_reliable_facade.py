"""R01/R03/R04 integration: the opt-in reliable connection path.

These tests use a queue-backed transport so each step is gated on an observable
event (a frame arriving, a handler starting, a counter changing) instead of a
timing guess.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest

from mcbe_ws_sdk.addon.service import AddonBridgeService
from mcbe_ws_sdk.config import (
    AddonBridgeSettings,
    DispatchSettings,
    GatewaySettings,
    SendSchedulerSettings,
)
from mcbe_ws_sdk.errors import BridgeClosedError, SendClosedError
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink
from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)

_BRIDGE_REQUEST_PREFIX = f"scriptevent {MCBEWS_V1.capability_request_script_event_id} "


# --------------------------------------------------------------------------- #
# Transport + hook doubles
# --------------------------------------------------------------------------- #


class QueueWebSocket:
    """Async-iterable transport fed by explicit ``push`` calls."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.on_send: Callable[[str], Awaitable[None]] | None = None

    async def send(self, payload: str) -> None:
        self.sent.append(payload)
        if self.on_send is not None:
            await self.on_send(payload)

    async def push(self, frame: str) -> None:
        await self._incoming.put(frame)

    async def close(self) -> None:
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
        self.state: ConnectionState | None = None
        self.messages: list[PlayerMessageEvent] = []
        self.errors: list[MinecraftErrorFrame] = []
        self.error_seen = asyncio.Event()
        self.on_player: (
            Callable[[ConnectionState, PlayerMessageEvent], Awaitable[None]] | None
        ) = None

    async def on_connected(self, state: ConnectionState) -> None:
        self.state = state
        self.connected.set()

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
    addon: AddonBridgeService | None = None,
    dispatch: DispatchSettings | None = None,
    send: SendSchedulerSettings | None = None,
) -> tuple[McbeServerFacade, QueueWebSocket, asyncio.Task[None]]:
    settings = GatewaySettings(
        dispatch=dispatch or DispatchSettings(mode="reliable"),
        send=send or SendSchedulerSettings(),
    )
    facade = McbeServerFacade(
        settings=settings,
        hook=hook,
        sink=DefaultResponseSink(),
        addon=addon,
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), timeout=2)
    return facade, websocket, task


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


# --------------------------------------------------------------------------- #
# R03-1: every new-path send goes through the scheduler
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
    facade, websocket, task = await _start(hook)

    await asyncio.wait_for(host_sent.wait(), timeout=2)

    assert facade.reliable_dispatch is True
    assert websocket.sent[0] == '{"Result":"true"}'
    assert json.loads(websocket.sent[1])["header"]["messagePurpose"] == "subscribe"
    assert websocket.sent[2] == "host payload"
    state = _require_state(hook)
    assert state.runtime is not None
    await _wait_until(lambda: state.runtime.scheduler.sent_count == 3)

    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# R01-2: one blocked player does not block other players or pending replies
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
    facade, websocket, task = await _start(hook)
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
    # Alice is still blocked while Bob and the command reply completed.
    assert not alice_release.is_set()

    alice_release.set()
    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# R01-3: a full business queue rejects without stalling the reader
# --------------------------------------------------------------------------- #


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
    facade, websocket, task = await _start(
        hook,
        dispatch=DispatchSettings(
            mode="reliable",
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

    # The reader kept draining: a command reply still pairs while the business
    # queue is full and Alice's handler is blocked.
    await websocket.push(_command_response_frame(command_ids[0]))
    response = await asyncio.wait_for(command_task, timeout=2)
    assert response.request_id == command_ids[0]

    alice_release.set()
    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# R01-4 / R04-3: disconnect cancels tasks and closes exactly once
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
    facade, websocket, task = await _start(hook)
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
    assert facade.manager.connection_count == 0

    # A send after close fails explicitly instead of being silently dropped.
    with pytest.raises(SendClosedError):
        await runtime.send_raw("late")
    with pytest.raises(SendClosedError):
        assert state.send_payload is not None
        await state.send_payload("late")

    # Teardown is idempotent.
    await facade.manager.drop_connection(state.id)
    await runtime.close()
    assert facade.manager.connection_count == 0


# --------------------------------------------------------------------------- #
# R04-3: send during the manager-pop / runtime-close window
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_during_drop_window_fails_instead_of_reporting_success() -> None:
    """The reliable transport must not silently succeed after the state is popped."""
    hook = RecordingHook()
    facade, websocket, task = await _start(hook)
    state = _require_state(hook)
    runtime = state.runtime
    assert runtime is not None
    assert state.send_payload is not None

    # ``drop_connection`` pops the active connection before closing the runtime;
    # reproduce exactly that window. Handshake/subscribe were already sent.
    sent_before = runtime.scheduler.sent_count
    facade.manager._connections.pop(state.id)
    with pytest.raises(SendClosedError):
        await state.send_payload("late")
    assert "late" not in websocket.sent
    assert runtime.scheduler.sent_count == sent_before

    # Restore so normal teardown can still find and drop the connection.
    facade.manager._connections[state.id] = state
    await _stop(websocket, task)


@pytest.mark.asyncio
async def test_legacy_send_during_drop_window_keeps_best_effort_behavior() -> None:
    """The legacy path keeps its original silent no-op after a drop."""
    hook = RecordingHook()
    facade = McbeServerFacade(
        settings=GatewaySettings(),
        hook=hook,
        sink=DefaultResponseSink(),
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), timeout=2)
    state = hook.state
    assert state is not None
    assert state.send_payload is not None
    assert state.runtime is None

    facade.manager._connections.pop(state.id)
    assert await state.send_payload("late") is None
    assert "late" not in websocket.sent

    facade.manager._connections[state.id] = state
    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# R04-1: same-player ordering; control messages keep their own lane
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
    facade, websocket, task = await _start(hook)

    await websocket.push(_player_frame("Alice", "first"))
    await asyncio.wait_for(first_started.wait(), timeout=2)
    await websocket.push(_player_frame("Alice", "second"))
    await websocket.push(_error_frame("err-1"))

    # The control lane is independent and runs while Alice's lane is blocked.
    await asyncio.wait_for(hook.error_seen.wait(), timeout=2)
    assert order == ["first"]

    release_first.set()
    await _wait_until(lambda: order == ["first", "second"])
    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# R04-2: a shared lane serialises world-mutating operations
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_shared_world_lane_serialises_operations() -> None:
    hook = RecordingHook()
    facade, websocket, task = await _start(hook)
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
# R04-4: repeated connections leave no SDK state behind
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_repeated_connections_return_to_initial_state() -> None:
    hook = RecordingHook()
    addon = AddonBridgeService(AddonBridgeSettings())
    facade, websocket, task = await _start(hook, addon=addon)
    first_state = _require_state(hook)
    session = addon._session_for(first_state.id)
    pending = session.create_request("demo", {})

    await _stop(websocket, task)

    assert facade.manager.connection_count == 0
    assert facade.manager._sender_tasks == {}
    assert addon._sessions == {}
    with pytest.raises(BridgeClosedError):
        await pending.future

    for _ in range(2):
        hook = RecordingHook()
        facade, websocket, task = await _start(hook, addon=addon)
        new_state = _require_state(hook)
        assert new_state.id != first_state.id
        await _stop(websocket, task)
        assert facade.manager.connection_count == 0
        assert facade.manager._sender_tasks == {}
        assert addon._sessions == {}


# --------------------------------------------------------------------------- #
# R02-2/R02-3: command replies never resolve addon requests, even on id reuse
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_command_response_does_not_resolve_addon_request_with_same_id() -> None:
    hook = RecordingHook()
    addon = AddonBridgeService(AddonBridgeSettings())
    facade, websocket, task = await _start(hook, addon=addon)
    state = _require_state(hook)

    session = addon._session_for(state.id)
    request = session.create_request("demo", {})
    request_id = request.request_id

    await websocket.push(_command_response_frame(request_id))
    await asyncio.sleep(0.02)
    assert not request.future.done()
    assert session._pending_requests.get(request_id) is not None

    await websocket.push(
        _player_frame(
            MCBEWS_V1.trusted_bridge_player_name,
            f"{MCBEWS_V1.capability_response_chat_prefix}|{request_id}|1/1|"
            + json.dumps({"ok": True}),
        )
    )
    await asyncio.wait_for(request.future, timeout=2)
    assert request.future.result() == {"ok": True}

    await _stop(websocket, task)


# --------------------------------------------------------------------------- #
# Stage gate: query-inventory chain needs no host-created request task
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_inventory_chain_awaits_inside_callback_without_host_task() -> None:
    hook = RecordingHook()
    addon = AddonBridgeService(AddonBridgeSettings(timeout_seconds=1.0))
    result_holder: dict[str, Any] = {}
    finished = asyncio.Event()

    async def answer_bridge(payload: str) -> None:
        if not payload.startswith(_BRIDGE_REQUEST_PREFIX):
            return
        body = json.loads(payload[len(_BRIDGE_REQUEST_PREFIX) :])
        request_id = body["request_id"]
        content = json.dumps(
            {"ok": True, "inventories": [{"player": "Alice", "items": []}]},
            separators=(",", ":"),
        )
        await websocket.push(
            _player_frame(
                MCBEWS_V1.trusted_bridge_player_name,
                f"{MCBEWS_V1.capability_response_chat_prefix}|{request_id}|1/1|{content}",
            )
        )

    async def on_player(state: ConnectionState, event: PlayerMessageEvent) -> None:
        client = addon.create_client(state.id, _require_send(state))
        try:
            result_holder["result"] = await client.request(
                "get_inventory_snapshot",
                {"target": event.sender},
            )
        finally:
            finished.set()

    hook.on_player = on_player
    facade, websocket, task = await _start(hook, addon=addon)
    websocket.on_send = answer_bridge

    await websocket.push(_player_frame("Alice", "!inv"))
    await asyncio.wait_for(finished.wait(), timeout=2)

    assert result_holder["result"]["ok"] is True
    assert result_holder["result"]["inventories"][0]["player"] == "Alice"

    await _stop(websocket, task)


@pytest.mark.parametrize(
    "trigger",
    ["on_connected", "on_player_message", "on_ui_chat_reassembled"],
)
@pytest.mark.asyncio
async def test_each_hook_can_await_addon_reply_directly(trigger: str) -> None:
    """R01-1: all three hook kinds may await an addon reply without blocking reads."""
    hook = RecordingHook()
    addon = AddonBridgeService(AddonBridgeSettings(timeout_seconds=1.0))
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    result_holder: dict[str, Any] = {}

    async def handler(state: ConnectionState) -> None:
        started.set()
        # Let the test install the transport responder before the frame is sent.
        await release.wait()
        client = addon.create_client(state.id, _require_send(state))
        try:
            result_holder["result"] = await client.request(
                "get_inventory_snapshot",
                {"target": "Alice"},
            )
        finally:
            finished.set()

    if trigger == "on_connected":

        async def on_connected(state: ConnectionState) -> None:
            hook.state = state
            hook.connected.set()
            await handler(state)

        hook.on_connected = on_connected  # type: ignore[method-assign]
    elif trigger == "on_player_message":

        async def on_player(state: ConnectionState, event: PlayerMessageEvent) -> None:
            await handler(state)

        hook.on_player = on_player
    else:

        async def on_ui(state: ConnectionState, message: Any) -> None:
            await handler(state)

        hook.on_ui_chat_reassembled = on_ui  # type: ignore[method-assign]

    facade, websocket, task = await _start(hook, addon=addon)

    async def answer_bridge(payload: str) -> None:
        if not payload.startswith(_BRIDGE_REQUEST_PREFIX):
            return
        body = json.loads(payload[len(_BRIDGE_REQUEST_PREFIX) :])
        request_id = body["request_id"]
        content = json.dumps(
            {"ok": True, "inventories": [{"player": "Alice", "items": []}]},
            separators=(",", ":"),
        )
        await websocket.push(
            _player_frame(
                MCBEWS_V1.trusted_bridge_player_name,
                f"{MCBEWS_V1.capability_response_chat_prefix}|{request_id}|1/1|{content}",
            )
        )

    websocket.on_send = answer_bridge

    try:
        if trigger == "on_player_message":
            await websocket.push(_player_frame("Alice", "!inv"))
        elif trigger == "on_ui_chat_reassembled":
            await websocket.push(
                _player_frame(
                    MCBEWS_V1.trusted_bridge_player_name,
                    'MCBEWS|UI_CHAT|m1|1/1|{"player":"Alice","message":"hello"}',
                )
            )
        await asyncio.wait_for(started.wait(), timeout=2)
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        release.set()
        await _stop(websocket, task)

    assert result_holder["result"]["ok"] is True
    assert result_holder["result"]["inventories"][0]["player"] == "Alice"


def _require_send(state: ConnectionState) -> Any:
    assert state.send_payload is not None
    return state.send_payload


def test_reliable_settings_are_explicit() -> None:
    assert GatewaySettings().dispatch.mode == "legacy"
    assert DispatchSettings(mode="reliable").mode == "reliable"
    with pytest.raises(ValueError):
        DispatchSettings(mode="fast")  # type: ignore[arg-type]
    assert UUID(int=1)
