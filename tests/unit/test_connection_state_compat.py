"""Legacy positional ConnectionState construction still feeds response routing."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from mcbe_ws_sdk.config import DispatchSettings, FlowControlSettings, SendSchedulerSettings
from mcbe_ws_sdk.gateway.connection import ConnectionManager, ConnectionState, enqueue_response
from mcbe_ws_sdk.gateway.messages import OutboundText
from mcbe_ws_sdk.gateway.runtime import ConnectionRuntime
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink


async def _send_noop(payload: str) -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("with_tracking", [False, True])
async def test_legacy_positional_state_enqueues_and_routes(with_tracking: bool) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue()
    identity = UUID(int=100)
    original = ConnectionState(identity, None, None, queue)
    assert original.response_queue is queue
    assert original.send_bulk is None
    bulk = ConnectionState(identity, None, None, queue, send_bulk=_send_noop)
    assert bulk.response_queue is queue
    assert bulk.send_bulk is _send_noop
    tracking = (
        ConnectionRuntime(
            connection_id=identity,
            dispatch=DispatchSettings(mode="reliable"),
            send=SendSchedulerSettings(),
            flow=FlowControlSettings(),
            transport=_send_noop,
        )
        if with_tracking
        else None
    )
    if tracking is None:
        state = ConnectionState(identity, None, _send_noop, queue)
    else:
        state = ConnectionState(
            identity, None, _send_noop, queue, tracking, tracking.requests, tracking.commands
        )
        assert state.runtime is tracking
        assert state.requests is tracking.requests
        assert state.command_client is tracking.commands
    assert state.response_queue is queue
    assert state.send_bulk is None
    routed = asyncio.Event()

    class SignalSink(DefaultResponseSink):
        async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
            received.append((state, message))
            routed.set()

    received: list[tuple[ConnectionState, OutboundText]] = []
    sink = SignalSink()
    manager = ConnectionManager(sink=sink)
    message = OutboundText(content="legacy", sequence=1)
    enqueue_response(state, message)
    assert queue.qsize() == 1
    sender = asyncio.create_task(manager._response_sender(state))
    try:
        await asyncio.wait_for(routed.wait(), 2)
        assert received == [(state, message)]
    finally:
        sender.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sender
        if tracking is not None:
            await tracking.close()
