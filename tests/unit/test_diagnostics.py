"""R08 event-gated correlation, packet privacy and failure isolation."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest

from mcbe_ws_sdk import DiagnosticSettings, TraceContext, current_trace, trace_scope
from mcbe_ws_sdk.config import DispatchSettings, GatewaySettings, SendSchedulerSettings
from mcbe_ws_sdk.diagnostics import Diagnostics, is_external_echo
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.dispatch import BusinessDispatcher
from mcbe_ws_sdk.gateway.events import WsEventType
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)
from mcbe_ws_sdk.tracing import observe


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
        self.on_player: Callable[[ConnectionState, PlayerMessageEvent], Awaitable[None]] | None = (
            None
        )

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


async def start(settings: DiagnosticSettings | None = None, observers: tuple[Any, ...] = ()):
    hook = RecordingHook()
    facade = McbeServerFacade(
        settings=GatewaySettings(
            dispatch=DispatchSettings(mode="reliable"),
            send=SendSchedulerSettings(min_interval_seconds=0),
        ),
        hook=hook,
        diagnostics=settings,
        diagnostic_observers=observers,
    )
    ws = QueueWebSocket()
    task = asyncio.create_task(facade._on_connection(ws))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None
    return facade, ws, task, hook, hook.state


async def stop(ws: QueueWebSocket, task: asyncio.Task[None]) -> None:
    await ws.close()
    await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_context_captured_per_submission_and_restored() -> None:
    dispatcher = BusinessDispatcher(connection_id=UUID(int=1), max_concurrent=1)
    gate, first, second = asyncio.Event(), asyncio.Event(), asyncio.Event()
    seen = []

    async def block() -> None:
        first.set()
        await gate.wait()

    async def capture() -> None:
        seen.append(current_trace())
        second.set()

    with trace_scope(trace_id="first", player="Alice"):
        dispatcher.submit(block)
    await first.wait()
    with trace_scope(trace_id="second", player="Bob"):
        dispatcher.submit(capture)
    gate.set()
    await asyncio.wait_for(second.wait(), 1)
    assert seen == [TraceContext(player="Bob", trace_id="second")]
    assert current_trace() == TraceContext()
    await dispatcher.close()


@pytest.mark.asyncio
async def test_player_two_requests_share_trace_but_keep_distinct_request_ids() -> None:
    records: list[dict[str, Any]] = []
    complete = asyncio.Event()

    def observer(record):
        records.append(record)
        if record["stage"] == "processing_completed" and record.get("event_name"):
            complete.set()

    facade, ws, task, hook, state = await start(observers=(observer,))

    async def on_send(payload):
        data = json.loads(payload)
        if data.get("header", {}).get("messagePurpose") == "commandRequest":
            await ws.push(_command_response_frame(data["header"]["requestId"]))

    ws.on_send = on_send

    async def player(state, event):
        await state.command_client.run("say first")
        await state.command_client.run("say second")

    hook.on_player = player
    await ws.push(_player_frame("Alice", "private text"))
    await asyncio.wait_for(complete.wait(), 2)
    related = [r for r in records if r.get("player") == "Alice"]
    replies = [r for r in related if r["stage"] == "remote_success"]
    assert len(replies) == 2
    ids = {r["request_id"] for r in replies}
    assert len(ids) == 2
    assert len({r["trace_id"] for r in related}) == 1
    assert not ids.intersection({r["trace_id"] for r in related})
    for request_id in ids:
        phases = {r["stage"] for r in related if r.get("request_id") == request_id}
        assert {
            "request_registered",
            "enqueued",
            "prepared",
            "transport_sent",
            "received",
            "remote_success",
        } <= phases
    assert all("packet" not in r for r in records)
    assert "private text" not in json.dumps(records)
    await stop(ws, task)
    assert state.diagnostics.task_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [False, True])
async def test_full_packets_filters_and_redaction_before_every_observer(full) -> None:
    records = []
    seen = asyncio.Event()

    def mutate(record):
        record["stage"] = "changed"

    def observer(record):
        records.append(record)
        seen.set()

    sink = Diagnostics(
        UUID(int=1),
        DiagnosticSettings(
            full_packets=full,
            connection_ids=frozenset({str(UUID(int=1))}),
            request_ids=frozenset({"wanted"}),
            secrets=("literal-secret",),
            secret_keys=frozenset({"Password"}),
        ),
        (mutate, observer),
    )
    with trace_scope(trace_id="trace", request_id="other"):
        sink.record("skip", packet='{"Password":"hidden"}')
        sink.record(
            "received",
            packet='{"Password":"hidden","nested":["literal-secret"]}',
            wire_request_id="wanted",
        )
    await asyncio.wait_for(seen.wait(), 1)
    assert len(records) == 1 and records[0]["stage"] == "received"
    if full:
        assert records[0]["packet"] == {"Password": "[REDACTED]", "nested": ["[REDACTED]"]}
    else:
        assert "packet" not in records[0]
    assert "hidden" not in json.dumps(records)
    await sink.close()


@pytest.mark.asyncio
async def test_full_bidirectional_packets_echo_and_identical_player_text() -> None:
    records = []
    received = asyncio.Event()

    def observer(record):
        records.append(record)
        if record["stage"] == "received" and record.get("player") == "Bob":
            received.set()

    facade, ws, task, hook, state = await start(DiagnosticSettings(full_packets=True), (observer,))
    raw_seen = []
    facade.manager.event_bus.subscribe(
        WsEventType.RAW_INBOUND, lambda *args: raw_seen.append(args[-1]), weak=False
    )
    await ws.push(_player_frame("External", "same"))
    await ws.push(_player_frame("Bob", "same"))
    await asyncio.wait_for(received.wait(), 2)
    echoes = [r for r in records if r.get("classification") == "external_echo"]
    players = [r for r in records if r.get("player") == "Bob"]
    assert len(echoes) == 1 and echoes[0]["packet"]["body"]["message"] == "same"
    assert players[0]["classification"] == "inbound"
    assert any(r["stage"] == "prepared" and "packet" in r for r in records)
    assert len(raw_seen) == 2
    assert is_external_echo("External") and not is_external_echo("External", "player")
    assert not is_external_echo("Bob", "say")
    await stop(ws, task)


@pytest.mark.asyncio
async def test_overflow_throwing_and_slow_observer_cannot_block_reply_pairing() -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()
    failures = []

    def throwing(record):
        failures.append(record)
        raise ValueError("observer failed")

    async def slow(record):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    facade, ws, task, hook, state = await start(
        DiagnosticSettings(queue_maxsize=2), (throwing, slow)
    )
    await asyncio.wait_for(started.wait(), 1)

    async def on_send(payload):
        data = json.loads(payload)
        if data.get("header", {}).get("messagePurpose") == "commandRequest":
            # Response remains a response even with external provenance fields.
            frame = json.loads(_command_response_frame(data["header"]["requestId"]))
            frame["body"].update(sender="External", message="same")
            await ws.push(json.dumps(frame))

    ws.on_send = on_send
    response = await asyncio.wait_for(state.command_client.run("say same"), 2)
    assert response.body["statusCode"] == 0
    assert state.requests.pending_count == 0
    assert state.diagnostics.dropped_count > 0
    assert state.diagnostics.observer_failures > 0
    await stop(ws, task)
    assert cancelled.is_set() and state.diagnostics.task_count == 0


@pytest.mark.asyncio
async def test_transport_and_remote_failures_are_not_success() -> None:
    records = []
    seen = asyncio.Event()

    def observer(record):
        records.append(record)
        if record["stage"] in {"transport_failed", "remote_failure"}:
            seen.set()

    facade, ws, task, hook, state = await start(observers=(observer,))

    async def remote_failure(payload):
        data = json.loads(payload)
        if data.get("header", {}).get("messagePurpose") == "commandRequest":
            reply = json.loads(_command_response_frame(data["header"]["requestId"]))
            reply["body"]["statusCode"] = -1
            await ws.push(json.dumps(reply))

    ws.on_send = remote_failure
    result = await state.command_client.run("invalid")
    assert result.body["statusCode"] == -1  # Preserve legacy return contract.
    await asyncio.wait_for(seen.wait(), 2)
    remote_id = result.request_id
    assert not any(
        r["stage"] == "remote_success" and r.get("request_id") == remote_id for r in records
    )
    seen.clear()

    async def fail(payload):
        raise OSError("transport unavailable")

    ws.on_send = fail
    with pytest.raises(OSError):
        await state.command_client.run("say fail")
    await asyncio.wait_for(seen.wait(), 2)
    failed = next(r for r in records if r["stage"] == "transport_failed")
    assert not any(
        r["stage"] in {"transport_sent", "remote_success"}
        and r.get("request_id") == failed["request_id"]
        for r in records
    )
    assert state.requests.pending_count == 0
    await stop(ws, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("parseable", [True, False])
async def test_packet_preparation_failure_is_private_and_best_effort(
    parseable, monkeypatch
) -> None:
    records, logs = [], []
    seen = asyncio.Event()

    class Logger:
        def debug(self, event, **fields):
            logs.append(fields)

    monkeypatch.setattr("mcbe_ws_sdk.diagnostics.structlog.get_logger", lambda *args: Logger())

    def observer(record):
        records.append(record)
        seen.set()

    sink = Diagnostics(UUID(int=1), DiagnosticSettings(full_packets=True), (observer,))
    depth = sys.getrecursionlimit() * 2 // 3 if parseable else sys.getrecursionlimit() + 100
    raw = '{"n":' * depth + '{"password":"must-stay-private"}' + "}" * depth
    if parseable:
        assert isinstance(json.loads(raw), dict)
    try:
        sink.record("received", packet=raw)
        sink.record("after_failure")
        await asyncio.wait_for(seen.wait(), 1)
        assert [r["stage"] for r in records] == ["after_failure"]
        assert "must-stay-private" not in json.dumps([records, logs])
        assert sink.preparation_failures == sink.dropped_count == 1
        assert sink.observer_failures == 0
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_packet_encoding_failure_is_counted_without_delivery() -> None:
    records = []
    sink = Diagnostics(UUID(int=1), DiagnosticSettings(full_packets=True), (records.append,))
    try:
        sink.record("received", packet="private\ud800")
        assert sink.preparation_failures == sink.dropped_count == 1
        assert sink.task_count == 0
        assert records == []
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_deep_valid_packet_cannot_interrupt_next_reply_pairing() -> None:
    facade, ws, task, hook, state = await start(DiagnosticSettings(full_packets=True))
    depth = sys.getrecursionlimit() * 2 // 3
    raw = '{"n":' * depth + '{"password":"must-stay-private"}' + "}" * depth
    assert isinstance(json.loads(raw), dict)

    async def on_send(payload):
        data = json.loads(payload)
        if data.get("header", {}).get("messagePurpose") == "commandRequest":
            await ws.push(raw)
            await ws.push(_command_response_frame(data["header"]["requestId"]))

    ws.on_send = on_send
    try:
        response = await asyncio.wait_for(state.command_client.run("say next"), 2)
        assert response.body["statusCode"] == 0
        assert state.requests.pending_count == 0
        assert state.diagnostics.preparation_failures == 1
        assert not task.done()
    finally:
        await stop(ws, task)


@pytest.mark.asyncio
async def test_logger_failure_and_packet_size_bound(monkeypatch) -> None:
    seen = asyncio.Event()
    records = []

    class BrokenLogger:
        def debug(self, *args, **kwargs):
            raise RuntimeError("logging handler failed")

    monkeypatch.setattr(
        "mcbe_ws_sdk.diagnostics.structlog.get_logger", lambda *args: BrokenLogger()
    )

    def observer(record):
        records.append(record)
        seen.set()

    sink = Diagnostics(
        UUID(int=1), DiagnosticSettings(full_packets=True, max_packet_bytes=2), (observer,)
    )
    with trace_scope(diagnostics=sink):
        observe("received", packet="large")
    await asyncio.wait_for(seen.wait(), 1)
    assert records[0]["packet_omitted"] == "size_limit"
    assert sink.observer_failures == 1
    await sink.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_addon_logical_request_and_reply_share_original_player_trace(failed) -> None:
    from mcbe_ws_sdk.delivery.outbound import McbeOutboundDelivery
    from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1

    records = []
    complete = asyncio.Event()

    def observer(record):
        records.append(record)
        if record.get("category") == "addon.bridge" and record["stage"] in {
            "remote_success",
            "remote_failure",
            "request_failed",
        }:
            complete.set()

    facade, ws, task, hook, state = await start(observers=(observer,))
    logical = []

    async def on_send(payload):
        data = json.loads(payload)
        command = data.get("body", {}).get("commandLine", "")
        if command.startswith("scriptevent "):
            request = json.loads(command.split(" ", 2)[2])
            logical.append(request["request_id"])
            await ws.push(_command_response_frame(data["header"]["requestId"]))
            content = json.dumps(
                {
                    "ok": not failed,
                    "result": {"value": 1},
                    "error": {"code": "DENIED", "message": "denied"},
                }
            )
            await ws.push(
                _player_frame(
                    MCBEWS_V1.trusted_bridge_player_name,
                    f"{MCBEWS_V1.capability_response_chat_prefix}|{request['request_id']}|1/1|{content}",
                )
            )

    ws.on_send = on_send

    async def player(state, event):
        delivery = McbeOutboundDelivery(
            connection_id=state.id, send_payload=state.send_payload, settings=facade.settings.flow
        )

        async def command(value):
            await delivery.send_raw_command(value)

        try:
            await facade.addon.request_capability(state.id, "custom", {}, command)
        except Exception:
            if not failed:
                raise

    hook.on_player = player
    await ws.push(_player_frame("Alice", "go"))
    await asyncio.wait_for(complete.wait(), 2)
    related = [r for r in records if r.get("request_id") == logical[0]]
    phases = {r["stage"] for r in related}
    assert {"request_registered", "enqueued", "prepared", "transport_sent", "received"} <= phases
    assert ("remote_failure" if failed else "remote_success") in phases
    if failed:
        assert "remote_success" not in phases
    assert {r["player"] for r in related} == {"Alice"}
    assert len({r["trace_id"] for r in related}) == 1
    sent = next(r for r in related if r["stage"] == "prepared")
    assert sent["wire_request_id"] != sent["request_id"] != sent["trace_id"]
    acknowledgements = [r for r in related if r.get("frame_type") == "commandResponse"]
    assert len(acknowledgements) == 1
    assert acknowledgements[0]["trace_id"] == sent["trace_id"]
    await stop(ws, task)
    assert facade.addon._diagnostics == {}


@pytest.mark.asyncio
async def test_response_queue_preserves_operation_context_without_changing_items() -> None:
    from mcbe_ws_sdk.gateway.connection import ConnectionManager, enqueue_response
    from mcbe_ws_sdk.gateway.messages import OutboundText
    from mcbe_ws_sdk.gateway.sink import DefaultResponseSink

    seen = asyncio.Event()
    traces = []

    class Sink(DefaultResponseSink):
        async def on_outbound_text(self, state, message):
            traces.append(current_trace())
            seen.set()

    manager = ConnectionManager(sink=Sink())

    async def send(payload):
        pass

    state = await manager.create_connection(send_payload=send)
    message = OutboundText(content="private")
    with trace_scope(trace_id="queued", player="Alice"):
        enqueue_response(state, message)
    await asyncio.wait_for(seen.wait(), 1)
    assert traces[0].trace_id == "queued" and traces[0].player == "Alice"
    assert len(state._response_traces) == 0
    await manager.drop_connection(state.id)


def test_trace_logging_processor_preserves_explicit_fields() -> None:
    from mcbe_ws_sdk.logging import trace_context_processor

    with trace_scope(trace_id="shared", player="Alice"):
        record = trace_context_processor(None, "info", {"request_id": "wire"})
    assert record == {"request_id": "wire", "trace_id": "shared", "player": "Alice"}
