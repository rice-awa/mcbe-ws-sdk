"""R13 regressions at the actual facade negotiation boundary."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcbe_ws_sdk.config import GatewaySettings
from mcbe_ws_sdk.errors import RequestClosedError
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import NegotiationError, V2Bridge
from mcbe_ws_sdk.profiles.mcbews_v2.transport import fragment
from mcbe_ws_sdk.server import McbeServer

from .test_reliable_facade import QueueWebSocket, RecordingHook, _player_frame, _stop


@pytest.mark.asyncio
async def test_request_and_notice_after_ready_are_admitted_inline() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.2)
    hook = RecordingHook()
    facade = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket(auto_negotiate=False)
    received: asyncio.Queue[str] = asyncio.Queue()

    async def handler(_name: str, params: dict[str, Any], _context: Any) -> dict[str, Any]:
        await received.put(params["value"])
        return {}

    bridge.registry.register("notice", handler)
    bridge.registry.register("reverse", handler)

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:negotiate_v2 "
        if not command.startswith(prefix):
            return
        parts = command[len(prefix) :].split("|")
        if parts[0] == "MCBEWS2H":
            await websocket.push(
                _player_frame(
                    "MCBEWS_BRIDGE",
                    f"MCBEWS2N|{parts[1]}|epoch|2|metadata/1,notifications/1,reverse/1",
                )
            )
        elif parts[0] == "MCBEWS2C":
            await websocket.push(_player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|epoch|ready"))

    websocket.on_send = answer
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    try:
        selection = await bridge.ready(hook.state.id)
        assert selection.version == "MCBEWS/2"
        for message, tid in [
            (
                {
                    "v": "MCBEWS/2",
                    "kind": "notification",
                    "method": "notice",
                    "params": {"value": "notice"},
                },
                "a.epoch.1",
            ),
            (
                {
                    "v": "MCBEWS/2",
                    "kind": "request",
                    "id": "a.epoch.2",
                    "method": "reverse",
                    "params": {"value": "request"},
                },
                "a.epoch.3",
            ),
        ]:
            for frame in fragment(message, tid, "up"):
                await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        assert {
            await asyncio.wait_for(received.get(), 2),
            await asyncio.wait_for(received.get(), 2),
        } == {"notice", "request"}
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_empty_advertised_features_bind_endpoint_and_wrapper() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.2)
    hook = RecordingHook()
    facade = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket(auto_negotiate=False)

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:negotiate_v2 "
        if command.startswith(prefix):
            parts = command[len(prefix) :].split("|")
            if parts[0] == "MCBEWS2H":
                await websocket.push(
                    _player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|empty|2|")
                )
            elif parts[0] == "MCBEWS2C":
                await websocket.push(
                    _player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|empty|ready")
                )

    websocket.on_send = answer
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    try:
        selection = await bridge.ready(hook.state.id)
        assert selection.endpoint is not None and not selection.features
        before = len(websocket.sent)
        with pytest.raises(NegotiationError, match="metadata"):
            await selection.endpoint.request("test", {}, metadata={"x": 1})
        with pytest.raises(NegotiationError, match="notifications"):
            await selection.endpoint.notify("test", {})
        with pytest.raises(NegotiationError, match="stream"):
            await selection.endpoint.request("test", {}, require={"stream/1"})
        with pytest.raises(NegotiationError, match="metadata"):
            await bridge.request(hook.state.id, "test", {}, metadata={"x": 1})
        assert len(websocket.sent) == before
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_actual_node_router_multiplexes_two_facades_and_reconnect_during_transfer() -> None:
    from mcbe_ws_sdk.gateway.connection import ConnectionState
    from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX

    from .test_v2_codec import ROOT, typescript

    typescript([])
    process = await asyncio.create_subprocess_exec(
        "node",
        "addon/tests/v2-live.cjs",
        "--negotiate",
        cwd=ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None
    assert json.loads(await asyncio.wait_for(process.stdout.readline(), 5))["type"] == "ready"
    bridge = V2Bridge(timeout_seconds=8, negotiation_timeout_seconds=3)
    connected: asyncio.Queue[ConnectionState] = asyncio.Queue()

    class Hook(RecordingHook):
        async def on_connected(self, state: ConnectionState) -> None:
            await connected.put(state)

    facade = McbeServer(v2=bridge, hook=Hook(), settings=GatewaySettings())
    sockets: list[QueueWebSocket] = []
    tasks: list[asyncio.Task[None]] = []
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    held: list[str] = []
    partial = asyncio.Event()
    hold_epoch = ""
    hold = False

    async def echo(_name: str, payload: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"payload": payload, "metadata": context.metadata}

    bridge.registry.register("py.echo", echo)

    async def write(item: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(item, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()

    async def send(payload: str) -> None:
        nonlocal hold
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        control_prefix = "scriptevent mcbews:negotiate_v2 "
        if command.startswith(control_prefix):
            await write({"op": "hello", "frame": command[len(control_prefix) :]})
        elif command.startswith(COMMAND_PREFIX):
            frame = command[len(COMMAND_PREFIX) :]
            if hold and frame.startswith(f"MCBEWS2|p.{hold_epoch}."):
                if not partial.is_set():
                    await write({"op": "frame", "frame": frame})
                    partial.set()
                else:
                    held.append(frame)
            else:
                await write({"op": "frame", "frame": frame})

    async def read_node() -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            event = json.loads(line)
            if event["type"] == "frame":
                for socket in sockets:
                    await socket.push(_player_frame("MCBEWS_BRIDGE", event["frame"]))
            else:
                await events.put(event)

    async def connect() -> tuple[QueueWebSocket, asyncio.Task[None], Any]:
        socket = QueueWebSocket(auto_negotiate=False)
        socket.on_send = send
        sockets.append(socket)
        task = asyncio.create_task(facade._on_connection(socket))
        tasks.append(task)
        state = await asyncio.wait_for(connected.get(), 3)
        selected = await asyncio.wait_for(bridge.ready(state.id), 5)
        assert selected.endpoint is not None
        return socket, task, selected.endpoint

    reader = asyncio.create_task(read_node())
    try:
        socket_a, task_a, peer_a = await connect()
        _socket_b, _task_b, peer_b = await connect()
        assert peer_a.epoch != peer_b.epoch
        payload_a = {"text": "A玩家" * 700}
        payload_b = {"text": "B玩家" * 700}
        await write(
            {
                "op": "request",
                "key": "A",
                "epoch": peer_a.epoch,
                "method": "py.echo",
                "params": payload_a,
            }
        )
        await write(
            {
                "op": "request",
                "key": "B",
                "epoch": peer_b.epoch,
                "method": "py.echo",
                "params": payload_b,
            }
        )
        results = [await asyncio.wait_for(events.get(), 5), await asyncio.wait_for(events.get(), 5)]
        by_key = {item["key"]: item for item in results}
        assert by_key["A"]["message"]["result"]["payload"] == payload_a
        assert by_key["B"]["message"]["result"]["payload"] == payload_b
        hold_epoch = peer_b.epoch
        hold = True
        pending_b = asyncio.create_task(peer_b.request("ts.reverse", payload_b))
        await asyncio.wait_for(partial.wait(), 3)
        async with asyncio.timeout(3):
            while not held:
                await asyncio.sleep(0)
        sockets.remove(socket_a)
        await _stop(socket_a, task_a)
        _socket_a2, _task_a2, peer_a2 = await connect()
        assert peer_a2.epoch not in {peer_a.epoch, peer_b.epoch}
        hold = False
        for frame in held:
            await write({"op": "frame", "frame": frame})
        response_a, response_b = await asyncio.gather(
            peer_a2.request("ts.reverse", payload_a), pending_b
        )
        assert response_a["result"]["response"]["result"]["payload"] == payload_a
        assert response_b["result"]["response"]["result"]["payload"] == payload_b
    finally:
        for socket, task in zip(list(sockets), [t for t in tasks if not t.done()], strict=False):
            await _stop(socket, task)
        await write({"op": "close"})
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await reader


@pytest.mark.asyncio
async def test_busy_admission_is_an_error_not_a_fallback() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.2)
    hook = RecordingHook()
    facade = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket(auto_negotiate=False)

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:negotiate_v2 MCBEWS2H|"
        if command.startswith(prefix):
            await websocket.push(
                _player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{command[len(prefix) :]}|-|busy")
            )

    websocket.on_send = answer
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(task, 2)
    assert websocket.close_code == 1008
    assert websocket.close_reason == "NEGOTIATION_BUSY"
    assert not hook.connected.is_set()


@pytest.mark.asyncio
async def test_blocked_confirmation_send_is_bounded_and_removes_early_activation() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.1)
    hook = RecordingHook()
    facade = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket(auto_negotiate=False)
    blocked = asyncio.Event()

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:negotiate_v2 "
        if not command.startswith(prefix):
            return
        parts = command[len(prefix) :].split("|")
        if parts[0] == "MCBEWS2H":
            await websocket.push(_player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|early|2|"))
        elif parts[0] == "MCBEWS2C":
            await websocket.push(_player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|early|ready"))
            await blocked.wait()

    websocket.on_send = answer
    task = asyncio.create_task(facade._on_connection(websocket))
    try:
        await asyncio.wait_for(task, 2)
        assert websocket.close_reason == "NEGOTIATION_CONFIRMATION_TIMEOUT"
        assert not hook.connected.is_set()
    finally:
        blocked.set()
        if not task.done():
            await _stop(websocket, task)


@pytest.mark.asyncio
async def test_lease_ack_renews_and_missing_ack_retires_endpoint(monkeypatch: Any) -> None:
    from mcbe_ws_sdk.profiles.mcbews_v2.runtime import _NEGOTIATION

    monkeypatch.setitem(_NEGOTIATION, "refresh_ms", 40)
    bridge = V2Bridge(negotiation_timeout_seconds=0.2)
    hook = RecordingHook()
    facade = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket(auto_negotiate=False)
    refreshed = asyncio.Event()
    respond = True

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:negotiate_v2 "
        if not command.startswith(prefix):
            return
        parts = command[len(prefix) :].split("|")
        if parts[0] == "MCBEWS2H":
            await websocket.push(_player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|lease|2|"))
        elif parts[0] == "MCBEWS2C":
            await websocket.push(_player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|lease|ready"))
        elif parts[0] == "MCBEWS2R" and respond:
            await websocket.push(_player_frame("MCBEWS_BRIDGE", "MCBEWS2N|lease|lease"))
            refreshed.set()

    websocket.on_send = answer
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    try:
        selection = await bridge.ready(hook.state.id)
        await asyncio.wait_for(refreshed.wait(), 1)
        assert bridge.connection(hook.state.id) is selection.endpoint
        respond = False
        async with asyncio.timeout(1):
            while bridge.connection(hook.state.id) is not None:
                await asyncio.sleep(0.01)
        with pytest.raises(RequestClosedError, match="closed"):
            await bridge.ready(hook.state.id)
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_endpoint_close_stops_queued_handlers_and_retires_owner() -> None:
    bridge = V2Bridge()
    hook = RecordingHook()
    facade = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None and hook.state.runtime is not None
    endpoint = bridge.connection(hook.state.id)
    assert endpoint is not None
    callbacks: list[Any] = []
    called = False

    async def handler(_name: str, _params: dict[str, Any], _context: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    bridge.registry.register("queued", handler)
    original = hook.state.runtime.business.submit

    def capture(callback: Any, **_kwargs: Any) -> bool:
        callbacks.append(callback)
        return True

    hook.state.runtime.business.submit = capture  # type: ignore[method-assign]
    message = {"v": "MCBEWS/2", "kind": "notification", "method": "queued", "params": {}}
    try:
        for frame in fragment(message, f"a.{endpoint.epoch}.1", "up"):
            await endpoint.receive(frame, source="MCBEWS_BRIDGE")
        assert len(callbacks) == 1
        endpoint.close()
        await callbacks[0]()
        assert not called and bridge.connection(hook.state.id) is None
    finally:
        hook.state.runtime.business.submit = original  # type: ignore[method-assign]
        await _stop(websocket, task)
