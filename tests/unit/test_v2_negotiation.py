"""Version discovery across the facade's actual connection lifecycle."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcbe_ws_sdk.config import DispatchSettings, FlowControlSettings, GatewaySettings
from mcbe_ws_sdk.errors import FrameTooLargeError, RequestClosedError
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import NegotiationError, V2Bridge
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX, ReassemblyPool, fragment

from .test_reliable_facade import QueueWebSocket, RecordingHook, _player_frame, _stop
from .test_v2_codec import ROOT, typescript


@pytest.mark.asyncio
async def test_new_peers_negotiate_and_reconnect_with_fresh_epoch() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.2)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    epochs: list[str] = []

    async def connect() -> tuple[QueueWebSocket, asyncio.Task[None], Any]:
        hook.connected.clear()
        websocket = QueueWebSocket()

        async def answer(payload: str) -> None:
            command = json.loads(payload).get("body", {}).get("commandLine", "")
            prefix = "scriptevent mcbews:negotiate_v2 "
            if command.startswith(prefix):
                parts = command[len(prefix) :].split("|")
                if parts[0] == "MCBEWS2H":
                    attempt = parts[1]
                    epoch = "e" + attempt[:23]
                    epochs.append(epoch)
                    await websocket.push(
                        _player_frame(
                            "MCBEWS_BRIDGE",
                            f"MCBEWS2N|{attempt}|{epoch}|2|metadata/1,notifications/1,reverse/1",
                        )
                    )
                elif parts[0] == "MCBEWS2C":
                    await websocket.push(
                        _player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|{parts[2]}|ready")
                    )

        websocket.on_send = answer
        task = asyncio.create_task(facade._on_connection(websocket))
        await asyncio.wait_for(hook.connected.wait(), 2)
        assert hook.state is not None
        selection = await asyncio.wait_for(bridge.ready(hook.state.id), 2)
        return websocket, task, selection

    websocket, task, selection = await connect()
    assert selection.version == "MCBEWS/2"
    assert "metadata/1" in selection.features
    await _stop(websocket, task)
    websocket, task, selection = await connect()
    try:
        assert selection.version == "MCBEWS/2"
        assert len(epochs) == 2 and epochs[0] != epochs[1]
        assert selection.endpoint is not None and selection.endpoint.epoch == epochs[1]
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_old_addon_fallback_calls_only_legacy_capabilities() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.05)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:bridge_req "
        if command.startswith(prefix):
            request = json.loads(command[len(prefix) :])
            content = json.dumps({"ok": True, "players": ["Alice"]}, separators=(",", ":"))
            await websocket.push(
                _player_frame(
                    "MCBEWS_BRIDGE", f"MCBEWS|BRIDGE|{request['request_id']}|1/1|{content}"
                )
            )

    websocket.on_send = answer
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    try:
        selection = await bridge.ready(hook.state.id)
        assert selection.version == "MCBEWS/1"
        result = await bridge.request(hook.state.id, "get_player_snapshot", {"target": "Alice"})
        assert result["result"] == {"ok": True, "players": ["Alice"]}
        with pytest.raises(NegotiationError, match="unsupported v1 capability"):
            await bridge.request(hook.state.id, "unknown", {})
    finally:
        await _stop(websocket, task)


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
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()

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
    task = asyncio.create_task(facade._on_connection(websocket))
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
async def test_absent_addon_does_not_block_chat_and_required_features_fail() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.05)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    try:
        await websocket.push(_player_frame("Alice", "ordinary chat"))
        async with asyncio.timeout(1):
            while not hook.messages:
                await asyncio.sleep(0)
        assert hook.messages[0].message == "ordinary chat"
        selected = await bridge.ready(hook.state.id)
        assert selected.version == "MCBEWS/1"
        with pytest.raises(NegotiationError, match="metadata"):
            await bridge.request(hook.state.id, "get_player_snapshot", {}, metadata={"x": 1})
        with pytest.raises(NegotiationError, match="stream"):
            await bridge.request(hook.state.id, "get_player_snapshot", {}, require={"stream/1"})
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_unsupported_version_is_an_error_not_a_legacy_downgrade() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.2)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()

    async def answer(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        prefix = "scriptevent mcbews:negotiate_v2 MCBEWS2H|"
        if command.startswith(prefix):
            epoch = command[len(prefix) :]
            await websocket.push(
                _player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{epoch}|challenge|3|metadata/1")
            )

    websocket.on_send = answer
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    try:
        with pytest.raises(NegotiationError, match="unsupported protocol"):
            await bridge.ready(hook.state.id)
    finally:
        await _stop(websocket, task)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_negotiation_timeout_must_be_finite_and_positive(timeout: float) -> None:
    with pytest.raises(ValueError):
        V2Bridge(negotiation_timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_discovery_respects_lower_command_budget() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.1)
    hook = RecordingHook()
    settings = GatewaySettings(
        dispatch=DispatchSettings(mode="reliable"),
        flow=FlowControlSettings(command_line_byte_budget=30),
    )
    facade = McbeServerFacade(v2=bridge, hook=hook, settings=settings)
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    try:
        with pytest.raises(FrameTooLargeError):
            await bridge.ready(hook.state.id)
        assert all("mcbews:negotiate_v2" not in payload for payload in websocket.sent)
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_stale_responses_unsolicited_and_fragments_are_ignored() -> None:
    bridge = V2Bridge(negotiate=False, timeout_seconds=1)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    endpoint = bridge.activate(hook.state, epoch="current")
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
        for frame in fragment(fresh_notice, "a.current.3", "up"):
            await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        await asyncio.wait_for(delivered.wait(), 2)
        assert calls == ["fresh"] and not pending.done()
        fresh_reply = {**stale_reply, "result": "fresh"}
        for frame in fragment(fresh_reply, "a.current.4", "up"):
            await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        assert (await asyncio.wait_for(pending, 2))["result"] == "fresh"
        assert bridge.pool.stats["buffers"] == 0
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_close_during_discovery_fails_waiter_and_clears_pending() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=1)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None and hook.state.requests is not None
    waiter = asyncio.create_task(bridge.ready(hook.state.id))
    await asyncio.sleep(0)
    await _stop(websocket, task)
    with pytest.raises(RequestClosedError, match="closed"):
        await waiter
    assert hook.state.requests.pending_count == 0
