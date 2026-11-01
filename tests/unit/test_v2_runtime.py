"""MCBEWS/2 messages cross the server's live connection boundary."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcbe_ws_sdk.config import GatewaySettings
from mcbe_ws_sdk.errors import (
    RemoteCallError,
    RequestClosedError,
    RequestTimeoutError,
    SendClosedError,
)
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX, ReassemblyPool, fragment
from mcbe_ws_sdk.server import McbeServer

from .test_reliable_facade import QueueWebSocket, RecordingHook, _player_frame, _stop
from .test_v2_codec import ROOT, typescript


@pytest.mark.asyncio
async def test_server_pairs_v2_reply_and_dispatches_reverse_request() -> None:
    bridge = V2Bridge()
    hook = RecordingHook()
    server = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    endpoint = bridge.connection(hook.state.id)
    assert endpoint is not None
    epoch = endpoint.epoch
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
            for frame in fragment(response, f"a.{epoch}.1", "up"):
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
            "id": f"a.{epoch}.2",
            "method": "reverse",
            "params": {"value": 7},
            "meta": metadata,
        }
        for frame in fragment(request, f"a.{epoch}.3", "up"):
            await websocket.push(_player_frame("MCBEWS_BRIDGE", frame))
        await asyncio.wait_for(done.wait(), 2)
        assert received[-1]["result"] == {"echo": {"value": 7}}
        assert received[-1]["meta"] == metadata
    finally:
        await _stop(websocket, task)


@pytest.mark.asyncio
async def test_node_addon_router_and_server_exchange_nested_calls_and_notifications() -> None:
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
    server = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket(auto_negotiate=False)
    task = asyncio.create_task(server._on_connection(websocket))
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    notices: asyncio.Queue[tuple[dict[str, Any], dict[str, Any]]] = asyncio.Queue()

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
            if event["type"] == "frame":
                await websocket.push(_player_frame("MCBEWS_BRIDGE", event["frame"]))
            else:
                await events.put(event)

    async def echo(_name: str, payload: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"payload": payload, "metadata": context.metadata, "caller": context.caller}

    bridge.registry.register("py.echo", echo)
    bridge.registry.register("py.concurrent", echo)

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
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    endpoint = bridge.connection(hook.state.id)
    assert endpoint is not None
    meta = {"custom": {"nested": ["玩家", None, True]}}
    try:
        await write(
            {
                "op": "request",
                "key": "reverse",
                "method": "py.concurrent",
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
        notice_event = await asyncio.wait_for(events.get(), 5)
        assert notice_event == {
            "type": "notification",
            "payload": {"hello": "world"},
            "metadata": meta,
        }
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
    server = McbeServer(v2=bridge, hook=hook, settings=GatewaySettings())
    websocket = QueueWebSocket()
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None and hook.state.requests is not None
    endpoint = bridge.connection(hook.state.id)
    assert endpoint is not None
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


@pytest.mark.asyncio
async def test_connections_share_pool_and_release_independently() -> None:
    bridge = V2Bridge()
    connected: asyncio.Queue[ConnectionState] = asyncio.Queue()

    class Hook(RecordingHook):
        async def on_connected(self, state: ConnectionState) -> None:
            await connected.put(state)

    server = McbeServer(v2=bridge, hook=Hook(), settings=GatewaySettings())
    first_socket, second_socket = QueueWebSocket(), QueueWebSocket()
    first_task = asyncio.create_task(server._on_connection(first_socket))
    first = await asyncio.wait_for(connected.get(), 2)
    second_task = asyncio.create_task(server._on_connection(second_socket))
    second = await asyncio.wait_for(connected.get(), 2)
    first_peer = bridge.connection(first.id)
    second_peer = bridge.connection(second.id)
    assert first_peer is not None and second_peer is not None
    message = {
        "v": "MCBEWS/2", "kind": "notification", "method": "notice",
        "params": {"text": "long" * 200},
    }
    try:
        spoofed = fragment(message, f"a.{first_peer.epoch}.1", "up")[0]
        await first_socket.push(_player_frame("Alice", spoofed))
        for index in range(1, 18):
            frame = fragment(message, f"a.{first_peer.epoch}.{index}", "up")[0]
            await first_socket.push(_player_frame("MCBEWS_BRIDGE", frame))
        frame = fragment(message, f"a.{second_peer.epoch}.1", "up")[0]
        await second_socket.push(_player_frame("MCBEWS_BRIDGE", frame))
        async with asyncio.timeout(2):
            while bridge.pool.stats["buffers"] != 17:
                await asyncio.sleep(0)
        await _stop(first_socket, first_task)
        assert bridge.pool.stats["buffers"] == 1
        await _stop(second_socket, second_task)
        assert bridge.pool.stats["buffers"] == 0
    finally:
        if not first_task.done():
            await _stop(first_socket, first_task)
        if not second_task.done():
            await _stop(second_socket, second_task)
