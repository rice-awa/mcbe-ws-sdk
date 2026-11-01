"""Version discovery across the server's actual connection lifecycle."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcbe_ws_sdk.config import FlowControlSettings, GatewaySettings
from mcbe_ws_sdk.errors import RequestClosedError
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import NegotiationError, V2Bridge
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX, ReassemblyPool, fragment
from mcbe_ws_sdk.server import McbeServer

from .test_reliable_facade import QueueWebSocket, RecordingHook, _player_frame, _stop
from .test_v2_codec import ROOT, typescript


def _server(hook: RecordingHook, bridge: V2Bridge, **settings: FlowControlSettings) -> McbeServer:
    return McbeServer(
        v2=bridge,
        hook=hook,
        settings=GatewaySettings(**settings),
    )


async def _wait_for_state(server: McbeServer) -> ConnectionState:
    async with asyncio.timeout(2):
        while True:
            states = list(server.manager._connections.values())
            if states:
                return states[0]
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_new_peers_negotiate_and_reconnect_with_fresh_epoch() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.2)
    hook = RecordingHook()
    server = _server(hook, bridge)
    epochs: list[str] = []

    async def connect() -> tuple[QueueWebSocket, asyncio.Task[None], str]:
        hook.connected.clear()
        websocket = QueueWebSocket()

        async def capture(payload: str) -> None:
            command = json.loads(payload).get("body", {}).get("commandLine", "")
            prefix = "scriptevent mcbews:negotiate_v2 "
            if command.startswith(prefix):
                parts = command[len(prefix) :].split("|")
                if parts[0] == "MCBEWS2H":
                    epochs.append(f"e{parts[1][:22]}")

        websocket.on_send = capture
        task = asyncio.create_task(server._on_connection(websocket))
        await asyncio.wait_for(hook.connected.wait(), 2)
        assert hook.state is not None
        selection = await asyncio.wait_for(bridge.ready(hook.state.id), 2)
        assert selection.version == "MCBEWS/2"
        assert selection.endpoint is not None
        return websocket, task, selection.endpoint.epoch

    websocket, task, first_epoch = await connect()
    await _stop(websocket, task)
    websocket, task, second_epoch = await connect()
    try:
        assert len(epochs) == 2 and epochs[0] != epochs[1]
        assert first_epoch != second_epoch
        assert second_epoch == epochs[1]
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_old_addon_never_downgrades_and_times_out() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.05)
    hook = RecordingHook()
    server = _server(hook, bridge)
    websocket = QueueWebSocket(auto_negotiate=False)
    business_calls: list[str] = []

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:negotiate_v2 "
        if command.startswith(prefix):
            parts = command[len(prefix) :].split("|")
            if parts[0] == "MCBEWS2H":
                # An MCBEWS/1 addon would answer the v1 bridge event, never this.
                business_calls.append(parts[1])

    websocket.on_send = answer
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(task, 2)

    assert len(business_calls) == 1
    assert websocket.close_code == 1008
    assert websocket.close_reason == "NEGOTIATION_TIMEOUT"
    assert not hook.connected.is_set()


@pytest.mark.asyncio
async def test_real_node_addon_negotiates_and_exchanges_v2() -> None:
    typescript([])
    process = await asyncio.create_subprocess_exec(
        "node",
        "addon/tests/v2-live.cjs",
        "--negotiate",
        cwd=ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    assert json.loads(await asyncio.wait_for(process.stdout.readline(), 5))["type"] == "ready"
    bridge = V2Bridge(negotiation_timeout_seconds=2)
    hook = RecordingHook()
    server = _server(hook, bridge)
    websocket = QueueWebSocket(auto_negotiate=False)

    async def write(item: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(item, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()

    async def send(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        hello = "scriptevent mcbews:negotiate_v2 "
        if command.startswith(hello):
            await write({"op": "hello", "frame": command[len(hello) :]})
        elif command.startswith(COMMAND_PREFIX):
            await write({"op": "frame", "frame": command[len(COMMAND_PREFIX) :]})

    async def read_node() -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            event = json.loads(line)
            assert event["type"] == "frame"
            await websocket.push(_player_frame("MCBEWS_BRIDGE", event["frame"]))

    websocket.on_send = send
    reader = asyncio.create_task(read_node())
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    try:
        selected = await asyncio.wait_for(bridge.ready(hook.state.id), 5)
        assert selected.version == "MCBEWS/2"
        meta = {"unknown": {"list": [1, None, "玩家"]}}
        result = await bridge.request(hook.state.id, "ts.echo", {"n": 1}, metadata=meta)
        assert result["result"]["payload"] == {"n": 1}
        assert result["meta"] == meta
        assert result["result"]["caller"] == {"kind": "server", "source": "Entity"}
        assert "stream/1" in selected.features
        with pytest.raises(NegotiationError, match="credit"):
            await bridge.request(hook.state.id, "ts.echo", {}, require={"credit/1"})
    finally:
        await _stop(websocket, task)
        await write({"op": "close"})
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await reader


@pytest.mark.asyncio
async def test_unsupported_version_is_an_error_not_a_legacy_downgrade() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.2)
    hook = RecordingHook()
    server = _server(hook, bridge)
    websocket = QueueWebSocket(auto_negotiate=False)

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:negotiate_v2 MCBEWS2H|"
        if command.startswith(prefix):
            attempt = command[len(prefix) :]
            await websocket.push(
                _player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{attempt}|challenge|3|metadata/1")
            )

    websocket.on_send = answer
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(task, 2)
    assert websocket.close_code == 1008
    assert websocket.close_reason == "UNSUPPORTED_PROTOCOL"
    assert not hook.connected.is_set()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_negotiation_timeout_must_be_finite_and_positive(timeout: float) -> None:
    with pytest.raises(ValueError):
        V2Bridge(negotiation_timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_discovery_respects_lower_command_budget() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.1)
    hook = RecordingHook()
    server = _server(
        hook,
        bridge,
        flow=FlowControlSettings(command_line_byte_budget=30),
    )
    websocket = QueueWebSocket(auto_negotiate=False)
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(task, 2)
    assert websocket.close_reason == "INVALID_NEGOTIATION"
    assert not hook.connected.is_set()
    assert all("mcbews:negotiate_v2" not in payload for payload in websocket.sent)


@pytest.mark.asyncio
async def test_stale_responses_unsolicited_and_fragments_are_ignored() -> None:
    bridge = V2Bridge(timeout_seconds=1)
    hook = RecordingHook()
    server = _server(hook, bridge)
    websocket = QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    endpoint = bridge.connection(hook.state.id)
    assert endpoint is not None
    epoch = endpoint.epoch
    calls: list[str] = []
    delivered = asyncio.Event()
    outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    pool = ReassemblyPool()

    async def handler(_name: str, payload: dict[str, Any], _context: Any) -> dict[str, Any]:
        calls.append(payload["value"])
        delivered.set()
        return {}

    async def capture(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        if command.startswith(COMMAND_PREFIX):
            message = pool.receive("c", "s", command[len(COMMAND_PREFIX) :], "down")
            if message:
                await outgoing.put(message)

    bridge.registry.register("notice", handler)
    websocket.on_send = capture
    try:
        pending = asyncio.create_task(endpoint.request("echo", {}))
        request = await asyncio.wait_for(outgoing.get(), 2)
        stale_reply = {"v": "MCBEWS/2", "kind": "response", "id": request["id"], "result": "stale"}
        stale_notice = {
            "v": "MCBEWS/2",
            "kind": "notification",
            "method": "notice",
            "params": {"value": "stale" * 100},
        }
        fresh_notice = {
            "v": "MCBEWS/2",
            "kind": "notification",
            "method": "notice",
            "params": {"value": "fresh"},
        }
        for frame in fragment(stale_reply, "a.previous.1", "up"):
            await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        for frame in fragment(stale_notice, "a.previous.2", "up"):
            await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        for frame in fragment(fresh_notice, f"a.{epoch}.3", "up"):
            await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        await asyncio.wait_for(delivered.wait(), 2)
        assert calls == ["fresh"] and not pending.done()
        fresh_reply = {**stale_reply, "result": "fresh"}
        for frame in fragment(fresh_reply, f"a.{epoch}.4", "up"):
            await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        assert (await asyncio.wait_for(pending, 2))["result"] == "fresh"
        assert bridge.pool.stats["buffers"] == 0
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_close_during_discovery_fails_waiter_and_clears_pending() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=1)
    hook = RecordingHook()
    server = _server(hook, bridge)
    websocket = QueueWebSocket(auto_negotiate=False)
    task = asyncio.create_task(server._on_connection(websocket))
    state = await _wait_for_state(server)
    assert state.requests is not None
    waiter = asyncio.create_task(bridge.ready(state.id))
    await asyncio.sleep(0)
    await _stop(websocket, task)
    with pytest.raises(RequestClosedError, match="closed"):
        await waiter
    assert state.requests.pending_count == 0
