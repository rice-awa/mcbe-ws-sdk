"""Runnable examples use public requests, subscriptions and stream cancellation."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from typing import Any
from unittest.mock import AsyncMock

from mcbe_ws_sdk import (
    ConnectionState,
    DefaultResponseSink,
    McbeServer,
    NegotiationError,
    OutboundText,
    V2Bridge,
)
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX

from .test_reliable_facade import QueueWebSocket, _command_response_frame, _player_frame, _stop
from .test_v2_codec import ROOT, typescript


def _example(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / name / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReplySink(DefaultResponseSink):
    def __init__(self) -> None:
        self.replies: asyncio.Queue[str] = asyncio.Queue()

    async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
        await self.replies.put(message.content)

    async def until(self, text: str) -> list[str]:
        replies = []
        async with asyncio.timeout(5):
            while True:
                reply = await self.replies.get()
                replies.append(reply)
                if text in reply:
                    return replies


async def test_basic_example_awaits_command_and_subscribes_through_public_api() -> None:
    module = _example("basic-server")
    connected = asyncio.Event()

    class Hook(module.ExampleHook):
        async def on_connected(self, state):
            await super().on_connected(state)
            self.state = state
            connected.set()

    hook = Hook()
    sink = ReplySink()
    server = McbeServer(hook=hook, sink=sink)
    websocket = QueueWebSocket()

    async def send(payload: str) -> None:
        packet = json.loads(payload)
        if packet.get("body", {}).get("commandLine") == "time query daytime":
            await websocket.push(_command_response_frame(packet["header"]["requestId"]))

    websocket.on_send = send
    connection = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(connected.wait(), 2)
    try:
        assert hook.state.native_events.listener_count == 1
        await websocket.push(_player_frame("Alex", "!wscmd time query daytime"))
        replies = await sink.until("statusCode")
        assert "'statusCode': 0" in replies[-1]
    finally:
        await _stop(websocket, connection)


async def test_addon_example_inventory_uses_negotiated_public_request() -> None:
    module = _example("addon-server")
    bridge = AsyncMock()
    bridge.request.return_value = {"result": {"ok": True, "payload": {"inventories": []}}}
    hook = module.AddonDemoHook(bridge)
    state = ConnectionState(response_queue=asyncio.Queue())
    await hook._dispatch_command(state, "Alex", "!inv")
    bridge.request.assert_awaited_once_with(
        state.id, "get_inventory_snapshot", {"target": "Alex"}, metadata=None
    )
    progress = state.response_queue.get_nowait()
    assert "正在请求" in progress.content
    final = state.response_queue.get_nowait()
    assert "未找到玩家背包" in final.content


async def test_addon_example_reports_stream_unavailable_without_retry() -> None:
    module = _example("addon-server")
    bridge = AsyncMock()
    bridge.stream.side_effect = NegotiationError(
        "UNSUPPORTED_PROTOCOL", "unsupported required features: stream/1"
    )
    hook = module.AddonDemoHook(bridge)
    state = ConnectionState(response_queue=asyncio.Queue())
    await hook._dispatch_command(state, "Alex", "!stream 2")
    bridge.stream.assert_awaited_once_with(state.id, "demo.ticks", {"count": 2})
    reply = state.response_queue.get_nowait()
    assert "流失败" in reply.content and "stream/1" in reply.content
    assert state.response_queue.empty()


async def test_addon_example_custom_metadata_stream_completion_and_cancel() -> None:
    typescript([])
    process = await asyncio.create_subprocess_exec(
        "node",
        "tests/smoke/r17-addon-runner.cjs",
        cwd=ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    assert json.loads(await asyncio.wait_for(process.stdout.readline(), 5))["type"] == "ready"
    module = _example("addon-server")
    bridge = V2Bridge()
    connected = asyncio.Event()

    class Hook(module.AddonDemoHook):
        async def on_connected(self, state):
            await super().on_connected(state)
            self.state = state
            connected.set()

    hook = Hook(bridge)
    sink = ReplySink()
    server = McbeServer(hook=hook, sink=sink, v2=bridge)
    websocket = QueueWebSocket(auto_negotiate=False)
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def write(item):
        process.stdin.write((json.dumps(item) + "\n").encode())
        await process.stdin.drain()

    async def send(payload):
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        hello = "scriptevent mcbews:negotiate_v2 "
        if command.startswith(hello):
            await write({"op": "hello", "frame": command[len(hello) :]})
        elif command.startswith(COMMAND_PREFIX):
            await write({"op": "frame", "frame": command[len(COMMAND_PREFIX) :]})

    async def read():
        while line := await process.stdout.readline():
            event = json.loads(line)
            if event["type"] == "frame":
                await websocket.push(_player_frame("MCBEWS_BRIDGE", event["frame"]))
            else:
                await events.put(event)

    # Install the bridge before the connection starts: the SDK sends the
    # negotiation hello immediately after handshake + subscribe.
    websocket.on_send = send
    reader = asyncio.create_task(read())
    connection = asyncio.create_task(server._on_connection(websocket))
    try:
        await asyncio.wait_for(connected.wait(), 5)
        await websocket.push(_player_frame("Alex", "!echo hello"))
        replies = await sink.until('"text": "hello"')
        assert any("返回元数据" in reply and "example.label" in reply for reply in replies)
        assert '"example.player": "Alex"' in replies[-1]
        await websocket.push(_player_frame("Alex", "!stream 2"))
        replies = await sink.until("流已结束")
        assert sum("流数据" in reply for reply in replies) == 2
        stopped = await asyncio.wait_for(events.get(), 2)
        assert stopped == {"type": "producer-stopped", "cancelled": False}
        await websocket.push(_player_frame("Alex", "!stream-cancel 2"))
        replies = await sink.until("流已取消")
        assert sum("流数据" in reply for reply in replies) == 2
        stopped = await asyncio.wait_for(events.get(), 2)
        assert stopped == {"type": "producer-stopped", "cancelled": True}
        await websocket.push(_player_frame("Alex", "!echo after-cancel"))
        await sink.until('"text": "after-cancel"')
        assert hook.state.requests.pending_count == 0
    finally:
        await _stop(websocket, connection)
        await write({"op": "close"})
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await reader
