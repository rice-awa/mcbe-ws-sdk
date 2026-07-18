"""Tests for response routing (RouteEnvelope + ResponseSink + DefaultResponseSink)."""

from __future__ import annotations

import asyncio

import pytest

from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.messages import StreamChunk, SystemNotification
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
)


@pytest.fixture
def state() -> ConnectionState:
    return ConnectionState()


def test_route_envelope_from_stream_chunk() -> None:
    chunk = StreamChunk(chunk_type="content", content="hi", sequence=1)
    env = RouteEnvelope.from_message(chunk)
    assert env.kind is ResponseKind.STREAM_CHUNK
    assert env.payload is chunk


def test_route_envelope_from_system_notification() -> None:
    note = SystemNotification(level="info", message="ready")
    env = RouteEnvelope.from_message(note)
    assert env.kind is ResponseKind.SYSTEM_NOTIFICATION
    assert env.payload is note


@pytest.mark.parametrize(
    "msg_type,expected_kind",
    [
        ("game_message", ResponseKind.GAME_MESSAGE),
        ("run_command", ResponseKind.RUN_COMMAND),
        ("ai_response_sync", ResponseKind.AI_RESPONSE_SYNC),
    ],
)
def test_route_envelope_from_dict_types(msg_type: str, expected_kind: ResponseKind) -> None:
    env = RouteEnvelope.from_message({"type": msg_type, "payload": "x"})
    assert env.kind is expected_kind


def test_route_envelope_rejects_unknown_type() -> None:
    with pytest.raises(TypeError):
        RouteEnvelope.from_message({"type": "wat"})


def test_route_envelope_rejects_arbitrary_object() -> None:
    with pytest.raises(TypeError):
        RouteEnvelope.from_message(object())


@pytest.mark.asyncio
async def test_default_sink_dispatches_stream_and_system(state: ConnectionState) -> None:
    sink = DefaultResponseSink()
    chunk = StreamChunk(chunk_type="content", content="hello")
    note = SystemNotification(level="warning", message="careful")
    # Both render routes run without raising.
    await sink.dispatch(state, RouteEnvelope(ResponseKind.STREAM_CHUNK, chunk))
    await sink.dispatch(state, RouteEnvelope(ResponseKind.SYSTEM_NOTIFICATION, note))


@pytest.mark.asyncio
async def test_default_sink_rejects_command_routes(state: ConnectionState) -> None:
    sink = DefaultResponseSink()
    for kind in (
        ResponseKind.GAME_MESSAGE,
        ResponseKind.RUN_COMMAND,
        ResponseKind.AI_RESPONSE_SYNC,
    ):
        with pytest.raises(NotImplementedError):
            await sink.dispatch(state, RouteEnvelope(kind, {"type": kind.value}))


def test_default_sink_is_response_sink() -> None:
    assert isinstance(DefaultResponseSink(), ResponseSink)


def test_custom_sink_can_subclass_default() -> None:
    class HostSink(DefaultResponseSink):
        def __init__(self) -> None:
            self.commands: list[dict] = []

        async def on_run_command(self, state: ConnectionState, payload: dict) -> None:
            self.commands.append(payload)

    sink = HostSink()
    assert isinstance(sink, ResponseSink)
    envelope = RouteEnvelope(ResponseKind.RUN_COMMAND, {"cmd": "x"})
    asyncio.run(sink.dispatch(ConnectionState(), envelope))
    assert sink.commands == [{"cmd": "x"}]
