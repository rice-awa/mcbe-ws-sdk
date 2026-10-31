"""Request streams deliver before completion and release resources on termination."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from mcbe_ws_sdk.config import DispatchSettings, GatewaySettings
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import NegotiationError, V2Bridge, V2Endpoint
from mcbe_ws_sdk.profiles.mcbews_v2.streams import StreamError
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX, ReassemblyPool, fragment

from .test_reliable_facade import QueueWebSocket, RecordingHook, _stop


@dataclass
class _Peer:
    endpoint: V2Endpoint
    bridge: V2Bridge
    messages: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    commands: list[str] = field(default_factory=list)
    sequence: int = 0

    async def send(self, kind: str, stream_id: str, **fields: Any) -> None:
        self.sequence += 1
        message = {"v": "MCBEWS/2", "kind": kind, "id": stream_id, **fields}
        for frame in fragment(message, f"a.test.{self.sequence}", "up"):
            await self.endpoint.receive(frame, source="MCBEWS_BRIDGE")

    async def next_message(self, kind: str) -> dict[str, Any]:
        while True:
            message = await asyncio.wait_for(self.messages.get(), 2)
            if message["kind"] == kind:
                return message

    async def open(self, method: str = "remote", **options: Any) -> tuple[Any, str]:
        stream = await self.endpoint.stream(method, {}, **options)
        message = await self.next_message("stream_open")
        return stream, message["id"]


@asynccontextmanager
async def _peer(**stream_limits: int) -> AsyncIterator[_Peer]:
    bridge = V2Bridge(negotiate=False, stream_limits=stream_limits)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge,
        hook=hook,
        settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable")),
    )
    websocket = QueueWebSocket()
    connection = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    endpoint = bridge.activate(hook.state, epoch="test")
    peer = _Peer(endpoint, bridge)
    pool = ReassemblyPool()

    async def sent(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        if not command.startswith(COMMAND_PREFIX):
            return
        peer.commands.append(command)
        message = pool.receive("peer", "server", command[len(COMMAND_PREFIX) :], "down")
        if message is not None:
            await peer.messages.put(message)
            if message["kind"] == "request":
                await peer.send(
                    "response", message["id"], result=message["params"], meta=message["meta"]
                )

    websocket.on_send = sent
    try:
        yield peer
    finally:
        await _stop(websocket, connection)


async def _items(stream: Any) -> list[Any]:
    return [item async for item in stream]


@pytest.mark.asyncio
async def test_first_item_arrives_before_producer_knows_final_length() -> None:
    async with _peer() as peer:
        stream, stream_id = await peer.open()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def produce() -> None:
            await peer.send("stream_item", stream_id, seq=0, item="first")
            await release.wait()
            await peer.send("stream_item", stream_id, seq=1, item="last")
            await peer.send("stream_end", stream_id, seq=2)
            finished.set()

        producer = asyncio.create_task(produce())
        try:
            assert await asyncio.wait_for(anext(stream), 2) == "first"
            assert not finished.is_set()
            release.set()
            assert await asyncio.wait_for(_items(stream), 2) == ["last"]
            await producer
        finally:
            release.set()
            await producer


@pytest.mark.asyncio
async def test_parallel_streams_and_ordinary_request_remain_isolated() -> None:
    async with _peer() as peer:
        first, first_id = await peer.open("same.method")
        second, second_id = await peer.open("same.method")
        request = asyncio.create_task(peer.endpoint.request("echo", {"ordinary": True}))
        await peer.send("stream_item", second_id, seq=0, item={"stream": 2})
        await peer.send("stream_item", first_id, seq=0, item={"stream": 1})
        await peer.send("stream_end", first_id, seq=1)
        await peer.send("stream_end", second_id, seq=1)
        assert await _items(first) == [{"stream": 1}]
        assert await _items(second) == [{"stream": 2}]
        assert (await asyncio.wait_for(request, 2))["result"] == {"ordinary": True}


@pytest.mark.asyncio
async def test_python_producer_streams_large_item_and_empty_stream_once() -> None:
    async with _peer() as peer:
        release = asyncio.Event()
        large = {"text": "玩家😀" * 1500}
        contexts: list[Any] = []

        async def produce(_method: str, _params: Any, context: Any) -> AsyncIterator[Any]:
            contexts.append(context)
            yield large
            await release.wait()
            yield None

        async def empty(_method: str, _params: Any, _context: Any) -> AsyncIterator[Any]:
            if False:
                yield None

        peer.bridge.streams.register("large", produce)
        peer.bridge.streams.register("empty", empty)
        await peer.send(
            "stream_open", "a.test.81", method="large", params={}, meta={"extra": [None, "值"]}
        )
        item = await peer.next_message("stream_item")
        assert item["item"] == large and item["seq"] == 0
        assert not release.is_set()
        assert contexts[0].metadata == {"extra": [None, "值"]}
        assert contexts[0].client is peer.endpoint
        assert len(peer.commands) > 1
        assert all(len(command.encode()) <= 461 for command in peer.commands)
        await peer.send("stream_open", "a.test.82", method="empty", params={})
        empty_end = await peer.next_message("stream_end")
        assert empty_end["id"] == "a.test.82" and empty_end["seq"] == 0
        release.set()
        assert (await peer.next_message("stream_item"))["item"] is None
        end = await peer.next_message("stream_end")
        assert end["id"] == "a.test.81" and end["seq"] == 2
        await peer.send("stream_open", "a.test.81", method="large", params={})
        await peer.send("stream_open", "a.test.82", method="empty", params={})
        await asyncio.sleep(0)
        assert peer.messages.empty()


@pytest.mark.asyncio
async def test_large_incoming_item_is_delivered_once_after_fragment_reassembly() -> None:
    async with _peer() as peer:
        stream, stream_id = await peer.open()
        item = {"data": "中文😀" * 1600}
        await peer.send("stream_item", stream_id, seq=0, item=item)
        await peer.send("stream_end", stream_id, seq=1)
        await peer.send("stream_end", stream_id, seq=1)
        await peer.send("stream_item", stream_id, seq=1, item="late")
        assert await _items(stream) == [item]
        with pytest.raises(StopAsyncIteration):
            await anext(stream)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,seq", [("stream_item", 1), ("stream_end", 1)])
async def test_missing_or_out_of_order_item_terminates_explicitly(kind: str, seq: int) -> None:
    async with _peer() as peer:
        stream, stream_id = await peer.open()
        await peer.send(
            kind, stream_id, seq=seq, **({"item": "bad"} if kind == "stream_item" else {})
        )
        with pytest.raises(StreamError) as exc:
            await anext(stream)
        assert exc.value.code == "INVALID_MESSAGE"
        assert (await peer.next_message("stream_cancel"))["id"] == stream_id


@pytest.mark.asyncio
async def test_duplicate_logical_item_fails_instead_of_delivering_twice() -> None:
    async with _peer() as peer:
        stream, stream_id = await peer.open()
        await peer.send("stream_item", stream_id, seq=0, item="one")
        assert await anext(stream) == "one"
        await peer.send("stream_item", stream_id, seq=0, item="one")
        with pytest.raises(StreamError, match="INVALID_MESSAGE"):
            await anext(stream)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits,items",
    [
        ({"buffer_items": 1}, ["first", "second"]),
        ({"buffer_bytes": 5}, ["1234"]),
        ({"connection_bytes": 5}, ["1234"]),
    ],
)
async def test_paused_consumer_overload_terminates_and_releases_buffer(
    limits: dict[str, int], items: list[str]
) -> None:
    async with _peer(**limits) as peer:
        stream, stream_id = await peer.open()
        for seq, item in enumerate(items):
            await peer.send("stream_item", stream_id, seq=seq, item=item)
        with pytest.raises(StreamError) as exc:
            await anext(stream)
        assert exc.value.code == "LIMIT_EXCEEDED"
        assert (await peer.next_message("stream_cancel"))["id"] == stream_id
        await peer.send("stream_item", stream_id, seq=len(items), item="late")
        with pytest.raises(StreamError, match="LIMIT_EXCEEDED"):
            await anext(stream)


@pytest.mark.asyncio
async def test_connection_buffer_limit_counts_multiple_streams() -> None:
    async with _peer(connection_bytes=9) as peer:
        first, first_id = await peer.open()
        second, second_id = await peer.open()
        await peer.send("stream_item", first_id, seq=0, item="123")
        await peer.send("stream_item", second_id, seq=0, item="456")
        with pytest.raises(StreamError, match="LIMIT_EXCEEDED"):
            await anext(second)
        assert await anext(first) == "123"
        await first.aclose()


@pytest.mark.asyncio
async def test_consumer_cancel_is_independent_and_early_context_exit_cleans_up() -> None:
    async with _peer() as peer:
        first, first_id = await peer.open()
        second, second_id = await peer.open()
        async with first:
            await peer.send("stream_item", first_id, seq=0, item="first")
            assert await anext(first) == "first"
        assert (await peer.next_message("stream_cancel"))["id"] == first_id
        await peer.send("stream_item", first_id, seq=1, item="late")
        with pytest.raises(StreamError, match="CANCELLED"):
            await anext(first)
        await peer.send("stream_item", second_id, seq=0, item="unaffected")
        await peer.send("stream_end", second_id, seq=1)
        assert await _items(second) == ["unaffected"]
        assert (await peer.endpoint.request("echo", {"alive": True}))["result"] == {"alive": True}


@pytest.mark.asyncio
async def test_remote_cancel_unwinds_producer_without_replaying_completed_work() -> None:
    async with _peer() as peer:
        finished = asyncio.Event()
        entered = asyncio.Event()
        calls = 0

        async def produce(_name: str, _params: Any, _context: Any) -> AsyncIterator[Any]:
            nonlocal calls
            calls += 1
            try:
                yield {"worldChangeDone": True}
                entered.set()
                await asyncio.Event().wait()
            finally:
                finished.set()

        peer.bridge.streams.register("world", produce)
        await peer.send("stream_open", "a.test.81", method="world", params={})
        assert (await peer.next_message("stream_item"))["item"] == {"worldChangeDone": True}
        await asyncio.wait_for(entered.wait(), 2)
        await peer.send("stream_cancel", "a.test.81")
        await asyncio.wait_for(finished.wait(), 2)
        await peer.send("stream_open", "a.test.81", method="world", params={})
        await asyncio.sleep(0)
        assert calls == 1
        assert peer.messages.empty()


@pytest.mark.asyncio
async def test_remote_failure_discards_buffer_and_has_one_terminal_result() -> None:
    async with _peer() as peer:
        stream, stream_id = await peer.open()
        await peer.send("stream_item", stream_id, seq=0, item="buffered")
        await peer.send(
            "stream_error",
            stream_id,
            seq=1,
            error={"code": "HANDLER_FAILED", "message": "remote failed"},
        )
        await peer.send("stream_end", stream_id, seq=1)
        await peer.send("stream_item", stream_id, seq=1, item="late")
        with pytest.raises(StreamError, match="remote failed") as exc:
            await anext(stream)
        assert exc.value.code == "HANDLER_FAILED"


@pytest.mark.asyncio
async def test_producer_exception_becomes_correlated_error() -> None:
    async with _peer() as peer:

        async def produce(_name: str, _params: Any, _context: Any) -> AsyncIterator[Any]:
            yield "first"
            raise RuntimeError("private exception content")

        peer.bridge.streams.register("broken", produce)
        await peer.send("stream_open", "a.test.90", method="broken", params={})
        assert (await peer.next_message("stream_item"))["item"] == "first"
        error = await peer.next_message("stream_error")
        assert error["id"] == "a.test.90" and error["seq"] == 1
        assert error["error"]["code"] == "HANDLER_FAILED"
        assert "private" not in error["error"]["message"]


@pytest.mark.asyncio
async def test_idle_timeout_wakes_waiter_and_cancels_remote() -> None:
    async with _peer(idle_ms=20) as peer:
        stream, stream_id = await peer.open()
        with pytest.raises(StreamError) as exc:
            await asyncio.wait_for(anext(stream), 2)
        assert exc.value.code == "TIMEOUT"
        assert (await peer.next_message("stream_cancel"))["id"] == stream_id


@pytest.mark.asyncio
async def test_endpoint_close_wakes_consumer_and_unwinds_producer() -> None:
    async with _peer() as peer:
        stream, _stream_id = await peer.open()
        producer_entered = asyncio.Event()
        producer_closed = asyncio.Event()

        async def produce(_name: str, _params: Any, _context: Any) -> AsyncIterator[Any]:
            try:
                producer_entered.set()
                await asyncio.Event().wait()
                yield None
            finally:
                producer_closed.set()

        peer.bridge.streams.register("waiting", produce)
        await peer.send("stream_open", "a.test.91", method="waiting", params={})
        await asyncio.wait_for(producer_entered.wait(), 2)
        waiting = asyncio.create_task(anext(stream))
        peer.endpoint.close()
        with pytest.raises(StreamError, match="CANCELLED"):
            await asyncio.wait_for(waiting, 2)
        await asyncio.wait_for(producer_closed.wait(), 2)


@pytest.mark.asyncio
async def test_stream_feature_required_without_legacy_fallback() -> None:
    async with _peer() as peer:
        # The caller sees capability absence before any stream is sent.
        peer.endpoint.features = frozenset({"metadata/1"})
        with pytest.raises(NegotiationError, match="stream/1"):
            await peer.endpoint.stream("remote", {})
        assert peer.messages.empty()


@pytest.mark.asyncio
async def test_cancelled_consumer_wait_cancels_stream_and_ignores_late_items() -> None:
    async with _peer() as peer:
        stream, stream_id = await peer.open()
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert (await peer.next_message("stream_cancel"))["id"] == stream_id
        await peer.send("stream_item", stream_id, seq=0, item="late")
        with pytest.raises(StreamError, match="CANCELLED"):
            await anext(stream)


@pytest.mark.asyncio
async def test_real_node_and_python_stream_both_directions_before_producers_finish() -> None:
    from .test_reliable_facade import _player_frame
    from .test_v2_codec import ROOT, typescript

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
    assert json.loads(await asyncio.wait_for(process.stdout.readline(), 15))["type"] == "ready"
    bridge = V2Bridge(negotiate=False)
    hook = RecordingHook()
    facade = McbeServerFacade(
        v2=bridge,
        hook=hook,
        settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable")),
    )
    websocket = QueueWebSocket()
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    release_python = asyncio.Event()
    python_done = asyncio.Event()
    large = "玩家😀" * 1200
    meta = {"custom": {"nested": [None, "值", True]}}

    async def produce(_name: str, _params: Any, context: Any) -> AsyncIterator[Any]:
        assert context.metadata == meta
        yield {"first": "python"}
        await release_python.wait()
        yield large
        python_done.set()

    bridge.streams.register("py.stream", produce)

    async def write(item: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(item, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()

    async def send(payload: str) -> None:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
        if command.startswith(COMMAND_PREFIX):
            assert len(command.encode()) <= 461
            await write({"op": "frame", "frame": command[len(COMMAND_PREFIX) :]})

    async def read_node() -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            event = json.loads(line)
            if event["type"] == "frame":
                await websocket.push(_player_frame("MCBEWS_BRIDGE", event["frame"]))
            else:
                await events.put(event)

    websocket.on_send = send
    reader = asyncio.create_task(read_node())
    task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    endpoint = bridge.activate(hook.state, epoch="test")
    try:
        stream = await endpoint.stream("ts.stream", {"gate": "next", "large": 1200}, metadata=meta)
        assert await asyncio.wait_for(anext(stream), 5) == {"first": True, "metadata": meta}
        await write(
            {
                "op": "stream",
                "key": "reverse",
                "method": "py.stream",
                "params": {},
                "options": {"metadata": meta},
            }
        )
        event = await asyncio.wait_for(events.get(), 5)
        assert event == {"type": "stream-item", "key": "reverse", "value": {"first": "python"}}
        assert not python_done.is_set()
        assert (await endpoint.request("ts.echo", {"during": "streams"}))["result"]["payload"] == {
            "during": "streams"
        }
        release_python.set()
        await write({"op": "stream_release", "gate": "next"})
        assert await asyncio.wait_for(_items(stream), 5) == [large]
        assert await asyncio.wait_for(events.get(), 5) == {
            "type": "stream-item",
            "key": "reverse",
            "value": large,
        }
        assert await asyncio.wait_for(events.get(), 5) == {"type": "stream-end", "key": "reverse"}
        empty = await endpoint.stream("ts.empty", {})
        assert await asyncio.wait_for(_items(empty), 5) == []
    finally:
        release_python.set()
        await _stop(websocket, task)
        await write({"op": "close"})
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await reader


@pytest.mark.asyncio
async def test_rejected_stream_cannot_restart_after_active_capacity_frees() -> None:
    async with _peer(max_active=1, tombstones=3) as peer:
        occupied, _occupied_id = await peer.open()
        calls: list[str] = []

        async def produce(_name: str, params: Any, _context: Any) -> AsyncIterator[Any]:
            calls.append(params["marker"])
            yield params["marker"]

        peer.bridge.streams.register("side.effect", produce)
        for stream_id in ("a.test.81", "a.test.82"):
            await peer.send(
                "stream_open", stream_id, method="side.effect", params={"marker": "rejected"}
            )
            rejected = await peer.next_message("stream_error")
            assert rejected["id"] == stream_id
            assert rejected["error"]["code"] == "LIMIT_EXCEEDED"
            await asyncio.gather(*peer.endpoint.streams.controls)
        await occupied.aclose()
        await peer.next_message("stream_cancel")
        await asyncio.gather(*peer.endpoint.streams.controls)
        for stream_id in ("a.test.81", "a.test.82"):
            await peer.send(
                "stream_open", stream_id, method="side.effect", params={"marker": "replayed"}
            )
        await peer.send(
            "stream_open", "a.test.83", method="side.effect", params={"marker": "fresh"}
        )
        item = await peer.next_message("stream_item")
        assert item["id"] == "a.test.83" and item["item"] == "fresh"
        assert calls == ["fresh"]
        end = await peer.next_message("stream_end")
        assert end["id"] == "a.test.83"


@pytest.mark.asyncio
async def test_rejected_stream_tombstones_respect_active_and_retired_id_limit() -> None:
    async with _peer(max_active=1, tombstones=2) as peer:
        entered = asyncio.Event()

        async def produce(_name: str, _params: Any, _context: Any) -> AsyncIterator[Any]:
            entered.set()
            await asyncio.Event().wait()
            yield None

        peer.bridge.streams.register("blocked", produce)
        await peer.send("stream_open", "a.test.81", method="blocked", params={})
        await asyncio.wait_for(entered.wait(), 2)
        for stream_id in ("a.test.82", "a.test.83", "a.test.84"):
            await peer.send("stream_open", stream_id, method="blocked", params={})
            assert (await peer.next_message("stream_error"))["id"] == stream_id
            await asyncio.gather(*peer.endpoint.streams.controls)
        manager = peer.endpoint.streams
        assert len(manager.producers) + len(manager.tombstones) == 2
        assert set(manager.tombstones) == {"a.test.82"}


@pytest.mark.asyncio
async def test_stream_producer_nested_request_inherits_incoming_trace_id() -> None:
    async with _peer() as peer:

        async def produce(_name: str, _params: Any, context: Any) -> AsyncIterator[Any]:
            response = await context.client.request("echo", {"nested": True})
            yield response["meta"]

        peer.bridge.streams.register("nested.trace", produce)
        await peer.send(
            "stream_open",
            "a.test.91",
            method="nested.trace",
            params={},
            meta={"sdk.trace_id": "stream_trace"},
        )
        request = await peer.next_message("request")
        assert request["meta"] == {"sdk.trace_id": "stream_trace"}
        item = await peer.next_message("stream_item")
        assert item["item"] == {"sdk.trace_id": "stream_trace"}
        assert (await peer.next_message("stream_end"))["id"] == "a.test.91"


@pytest.mark.asyncio
async def test_close_cancels_blocked_open_before_it_can_start_remote_producer() -> None:
    from mcbe_ws_sdk.profiles.mcbews_v2.streams import StreamManager, StreamRegistry

    entered = asyncio.Event()
    release = asyncio.Event()
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        entered.set()
        await release.wait()
        sent.append(message)

    manager = StreamManager(send, StreamRegistry())
    opener = asyncio.create_task(
        manager.open(
            {
                "v": "MCBEWS/2",
                "kind": "stream_open",
                "id": "p.test.1",
                "method": "remote",
                "params": {},
            }
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        manager.close()
        with pytest.raises(StreamError, match="CANCELLED"):
            await asyncio.wait_for(asyncio.shield(opener), 0.2)
        release.set()
        await manager.wait_closed()
        assert sent == []
        assert not manager.opening and not manager.incoming
    finally:
        manager.close()
        release.set()
        await asyncio.gather(opener, return_exceptions=True)
        await manager.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["idle", "caller"])
async def test_opening_send_stops_on_idle_timeout_or_caller_cancellation(reason: str) -> None:
    from mcbe_ws_sdk.profiles.mcbews_v2.streams import StreamManager, StreamRegistry

    entered = asyncio.Event()
    release = asyncio.Event()
    sent: list[str] = []

    async def send(message: dict[str, Any]) -> None:
        if message["kind"] == "stream_open":
            entered.set()
            await release.wait()
        sent.append(message["kind"])

    manager = StreamManager(send, StreamRegistry(), overrides={"idle_ms": 20})
    opener = asyncio.create_task(
        manager.open(
            {
                "v": "MCBEWS/2",
                "kind": "stream_open",
                "id": "p.test.1",
                "method": "remote",
                "params": {},
            }
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if reason == "caller":
            opener.cancel()
            with pytest.raises(asyncio.CancelledError):
                await opener
        else:
            with pytest.raises(StreamError, match="TIMEOUT"):
                await asyncio.wait_for(asyncio.shield(opener), 2)
        release.set()
        await asyncio.gather(*manager.controls)
        assert sent == ["stream_cancel"]
        assert not manager.opening and not manager.incoming
    finally:
        manager.close()
        release.set()
        await asyncio.gather(opener, return_exceptions=True)
        await manager.wait_closed()


@pytest.mark.asyncio
async def test_close_before_opening_sender_first_step_releases_managed_task() -> None:
    from mcbe_ws_sdk.profiles.mcbews_v2.streams import StreamManager, StreamRegistry

    sent: list[str] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message["kind"])

    manager = StreamManager(send, StreamRegistry())
    opener = asyncio.create_task(
        manager.open(
            {
                "v": "MCBEWS/2",
                "kind": "stream_open",
                "id": "p.test.1",
                "method": "remote",
                "params": {},
            }
        )
    )
    asyncio.get_running_loop().call_soon(manager.close)
    with pytest.raises(StreamError, match="CANCELLED"):
        await opener
    await manager.wait_closed()
    assert sent == []
    assert not manager.opening and not manager.incoming
