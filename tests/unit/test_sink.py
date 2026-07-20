"""Tests for response routing (RouteEnvelope + ResponseSink + DefaultResponseSink)."""

from __future__ import annotations

import asyncio

import pytest

from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
)


@pytest.fixture
def state() -> ConnectionState:
    return ConnectionState()


def test_route_envelope_from_outbound_text() -> None:
    msg = OutboundText(content="hi", sequence=1)
    env = RouteEnvelope.from_message(msg)
    assert env.kind is ResponseKind.OUTBOUND_TEXT
    assert env.payload is msg


def test_route_envelope_from_system_notification() -> None:
    note = SystemNotification(level="info", message="ready")
    env = RouteEnvelope.from_message(note)
    assert env.kind is ResponseKind.SYSTEM_NOTIFICATION
    assert env.payload is note


def test_route_envelope_rejects_host_dict_shapes() -> None:
    with pytest.raises(TypeError):
        RouteEnvelope.from_message({"type": "ai_response_sync"})


def test_route_envelope_rejects_unknown_type() -> None:
    with pytest.raises(TypeError):
        RouteEnvelope.from_message({"type": "wat"})


def test_route_envelope_rejects_arbitrary_object() -> None:
    with pytest.raises(TypeError):
        RouteEnvelope.from_message(object())


@pytest.mark.asyncio
async def test_default_sink_dispatches_outbound_and_system(state: ConnectionState) -> None:
    sink = DefaultResponseSink()
    msg = OutboundText(content="hello")
    note = SystemNotification(level="warning", message="careful")
    # Convenience dispatch remains on DefaultResponseSink (not on Protocol).
    await sink.dispatch(state, RouteEnvelope(ResponseKind.OUTBOUND_TEXT, msg))
    await sink.dispatch(state, RouteEnvelope(ResponseKind.SYSTEM_NOTIFICATION, note))


@pytest.mark.asyncio
async def test_sink_dispatches_only_typed_outbound_messages() -> None:
    class RecordingSink(DefaultResponseSink):
        def __init__(self) -> None:
            self.outbound: list[tuple[ConnectionState, OutboundText]] = []

        async def on_outbound_text(
            self, state: ConnectionState, message: OutboundText
        ) -> None:
            self.outbound.append((state, message))

    sink = RecordingSink()
    state = ConnectionState()
    message = OutboundText(content="hello", player_name="Alice")
    await sink.dispatch(state, RouteEnvelope.from_message(message))
    assert sink.outbound == [(state, message)]


def test_default_sink_is_response_sink() -> None:
    assert isinstance(DefaultResponseSink(), ResponseSink)


def test_duck_typed_two_method_sink_is_response_sink() -> None:
    """Protocol only requires the two on_* methods — no dispatch."""

    class TwoMethodSink:
        async def on_outbound_text(
            self, state: ConnectionState, message: OutboundText
        ) -> None:
            return None

        async def on_system_notification(
            self, state: ConnectionState, message: SystemNotification
        ) -> None:
            return None

    assert isinstance(TwoMethodSink(), ResponseSink)


def test_custom_sink_can_subclass_default() -> None:
    class HostSink(DefaultResponseSink):
        def __init__(self) -> None:
            self.messages: list[OutboundText] = []

        async def on_outbound_text(
            self, state: ConnectionState, message: OutboundText
        ) -> None:
            self.messages.append(message)

    sink = HostSink()
    assert isinstance(sink, ResponseSink)
    message = OutboundText(content="x")
    envelope = RouteEnvelope(ResponseKind.OUTBOUND_TEXT, message)
    asyncio.run(sink.dispatch(ConnectionState(), envelope))
    assert len(sink.messages) == 1
    assert sink.messages[0].content == "x"
