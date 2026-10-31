"""MCBEWS/2 messages cross the facade's live connection boundary."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcbe_ws_sdk.config import DispatchSettings, GatewaySettings
from mcbe_ws_sdk.errors import (
    RemoteCallError,
    RequestClosedError,
    RequestTimeoutError,
    SendClosedError,
)
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX, ReassemblyPool, fragment

from .test_reliable_facade import QueueWebSocket, RecordingHook, _player_frame, _stop
from .test_v2_codec import ROOT, typescript


@pytest.mark.asyncio
async def test_facade_pairs_v2_reply_and_dispatches_reverse_request() -> None:
    bridge = V2Bridge()
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    endpoint = bridge.activate(hook.state, epoch="test")
    received: list[dict[str, Any]] = []
    downlink = ReassemblyPool()
    done = asyncio.Event()
    metadata = {"extension": {"items": [None, "玩家", {"flag": True}]}}

    async def answer(_name: str, payload: dict[str, Any], context: Any) -> dict[str, Any]:
        assert context.metadata == metadata
        assert context.caller == "MCBEWS_BRIDGE"
        return {"echo": payload}

    bridge.registry.register("reverse", answer)

    async def on_send(payload: str) -> None:
        packet = json.loads(payload)
        command = packet.get("body", {}).get("commandLine", "")
        if not command.startswith(COMMAND_PREFIX):
            return
        message = downlink.receive("conn", "server", command[len(COMMAND_PREFIX) :], "down")
        if message is None:
            return
        received.append(message)
        if message["kind"] == "request":
            response = {
                "v": "MCBEWS/2",
                "kind": "response",
                "id": message["id"],
                "result": message["params"],
                "meta": message["meta"],
            }
            for frame in fragment(response, "a.test.1", "up"):
                await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        else:
            done.set()

    websocket.on_send = on_send
    try:
        response = await asyncio.wait_for(
            endpoint.request("echo", {"value": "hello"}, metadata=metadata, trace_id="trace_1"), 2
        )
        assert response["result"] == {"value": "hello"}
        assert response["meta"] == {**metadata, "sdk.trace_id": "trace_1"}
        request = {
            "v": "MCBEWS/2",
            "kind": "request",
            "id": "a.test.2",
            "method": "reverse",
            "params": {"value": 7},
            "meta": metadata,
        }
        for frame in fragment(request, "a.test.3", "up"):
            await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        await asyncio.wait_for(done.wait(), 2)
        assert received[-1]["result"] == {"echo": {"value": 7}}
        assert received[-1]["meta"] == metadata
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_node_addon_router_and_facade_exchange_nested_calls_and_notifications() -> None:
    typescript([])
    process = await asyncio.create_subprocess_exec(
        "node",
        "addon/tests/v2-live.cjs",
        cwd=ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    assert json.loads(await asyncio.wait_for(process.stdout.readline(), 5))["type"] == "ready"
    bridge = V2Bridge(timeout_seconds=2)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    endpoint = bridge.activate(hook.state, epoch="test")
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    notices: asyncio.Queue[tuple[dict[str, Any], dict[str, Any]]] = asyncio.Queue()

    async def write(item: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(item, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()

    async def send(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        if command.startswith(COMMAND_PREFIX):
            await write({"op": "frame", "frame": command[len(COMMAND_PREFIX) :]})

    async def read_node() -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            event = json.loads(line)
            if event["type"] == "frame":
                await websocket.push(_player_frame("MCBEWS_BRIDGE", event["frame"]))
            else:
                await events.put(event)

    async def echo(_name: str, payload: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"payload": payload, "metadata": context.metadata, "caller": context.caller}

    bridge.registry.register("py.echo", echo)

    async def cycle(_name: str, payload: dict[str, Any], context: Any) -> dict[str, Any]:
        await context.client.request("ts.cycle", payload)
        return {}

    bridge.registry.register("py.cycle", cycle)

    async def notice(_name: str, payload: dict[str, Any], context: Any) -> dict[str, Any]:
        await notices.put((payload, dict(context.metadata)))
        return {}

    bridge.registry.register("py.notice", notice)
    websocket.on_send = send
    reader = asyncio.create_task(read_node())
    meta = {"custom": {"nested": ["玩家", None, True]}}
    try:
        await write(
            {
                "op": "request",
                "key": "reverse",
                "method": "py.echo",
                "params": {"from": "addon"},
                "options": {"metadata": meta, "traceId": "same_trace"},
            }
        )
        response = await asyncio.wait_for(
            endpoint.request(
                "ts.reverse", {"from": "python"}, metadata=meta, trace_id="same_trace"
            ),
            5,
        )
        assert response["meta"] == {**meta, "sdk.trace_id": "same_trace"}
        nested = response["result"]["response"]
        assert nested["result"]["payload"] == {"from": "python"}
        assert nested["result"]["caller"] == "MCBEWS_BRIDGE"
        assert nested["meta"]["sdk.trace_id"] == "same_trace"
        event = await asyncio.wait_for(events.get(), 5)
        assert event["type"] == "result" and event["key"] == "reverse"
        assert event["message"]["result"]["metadata"] == {**meta, "sdk.trace_id": "same_trace"}
        with pytest.raises(RemoteCallError) as failure:
            await asyncio.wait_for(endpoint.request("ts.cycle", {}), 5)
        assert failure.value.code == "HANDLER_FAILED"
        await endpoint.notify("ts.notice", {"hello": "world"}, metadata=meta)
        notice = await asyncio.wait_for(events.get(), 5)
        assert notice == {"type": "notification", "payload": {"hello": "world"}, "metadata": meta}
        await write(
            {
                "op": "notify",
                "method": "py.notice",
                "params": {"from": "addon"},
                "options": {"metadata": meta},
            }
        )
        assert await asyncio.wait_for(notices.get(), 5) == ({"from": "addon"}, meta)
        with pytest.raises(RemoteCallError) as missing:
            await endpoint.request("ts.missing", {})
        assert missing.value.code == "UNSUPPORTED_METHOD"
    finally:
        await _stop(websocket, task)
        await write({"op": "close"})
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await reader


@pytest.mark.asyncio
async def test_pending_cleanup_on_encode_timeout_cancel_and_disconnect() -> None:
    bridge = V2Bridge(timeout_seconds=0.05)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge, hook=hook, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    websocket = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None and hook.state.requests is not None
    endpoint = bridge.activate(hook.state, epoch="cleanup")
    try:
        with pytest.raises(ValueError, match="reserved"):
            await endpoint.request("echo", {}, metadata={"sdk.identity": {"admin": True}})
        assert hook.state.requests.pending_count == 0
        with pytest.raises(RequestTimeoutError):
            await endpoint.request("echo", {})
        assert hook.state.requests.pending_count == 0
        pending = asyncio.create_task(endpoint.request("echo", {}))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert hook.state.requests.pending_count == 0
        pending = asyncio.create_task(endpoint.request("echo", {}))
        while hook.state.requests.pending_count == 0:
            await asyncio.sleep(0)
        await _stop(websocket, task)
        with pytest.raises((RequestClosedError, SendClosedError)):
            await pending
        assert hook.state.requests.pending_count == 0
        assert bridge.pool.stats["bytes"] == 0
    finally:
        if not task.done():
            await _stop(websocket, task)
