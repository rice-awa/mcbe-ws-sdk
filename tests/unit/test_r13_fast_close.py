"""Regression coverage for a connection closed before discovery runs."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable

import pytest

from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge
from mcbe_ws_sdk.server import McbeServer

from .test_reliable_facade import QueueWebSocket


def _assert_bridge_empty(bridge: V2Bridge) -> None:
    assert not bridge._discoveries
    assert not bridge._negotiations
    assert not bridge._connections
    assert not bridge._controls
    assert not bridge._leases
    assert not bridge._lease_acks


@pytest.mark.asyncio
async def test_preclosed_transport_never_starts_discovery_and_leaks_nothing() -> None:
    bridge = V2Bridge()
    facade = McbeServer(v2=bridge)
    starts: list[ConnectionState] = []
    original_start = bridge.start

    def observe_start(
        state: ConnectionState,
        send_hello: Callable[[str], Awaitable[None]],
    ) -> None:
        starts.append(state)
        original_start(state, send_hello)

    bridge.start = observe_start  # type: ignore[method-assign]
    for _ in range(3):
        websocket = QueueWebSocket()
        await websocket.close()
        await facade._on_connection(websocket)

    assert starts == []
    _assert_bridge_empty(bridge)
    assert facade.manager.connection_count == 0


@pytest.mark.asyncio
async def test_close_before_discovery_first_step_cancels_everything() -> None:
    bridge = V2Bridge()
    facade = McbeServer(v2=bridge)

    async def send(raw: str) -> None:
        return None

    for _ in range(3):
        state = await facade.manager.create_connection(send_payload=send)
        facade._install_tracking(state, send)
        bridge.start(state, send)
        discovery = bridge._discoveries[state.id]
        assert state.requests is not None
        pending = state.requests.get("addon.v2.negotiation", discovery.attempt)
        task = bridge._negotiations[state.id]
        assert pending is not None
        assert inspect.getcoroutinestate(task.get_coro()) is inspect.CORO_CREATED

        bridge.close(state.id)
        assert discovery.challenge.cancelled()
        assert pending.future.cancelled()
        assert state.requests.pending_count == 0
        with pytest.raises(asyncio.CancelledError):
            await task
        await facade.manager.drop_connection(state.id)

    _assert_bridge_empty(bridge)
    assert facade.manager.connection_count == 0
