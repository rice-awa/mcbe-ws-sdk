"""Regression coverage for a connection closed before discovery runs."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest

from mcbe_ws_sdk.config import DispatchSettings, GatewaySettings
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge

from .test_reliable_facade import QueueWebSocket


@pytest.mark.asyncio
async def test_preclosed_facade_cleans_never_started_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = V2Bridge()
    facade = McbeServerFacade(
        v2=bridge, settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
    )
    starts: list[
        tuple[ConnectionState, asyncio.Future[Any], asyncio.Future[Any], asyncio.Task[Any]]
    ] = []
    closes: list[tuple[str, bool, bool, int]] = []
    original_start = bridge.start
    original_close = bridge.close

    def observe_start(
        state: ConnectionState,
        send_hello: Callable[[str], Awaitable[None]],
        legacy_request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        original_start(state, send_hello, legacy_request)
        assert state.requests is not None
        discovery = bridge._discoveries[state.id]
        pending = state.requests.get("addon.v2.negotiation", discovery.attempt)
        assert pending is not None
        starts.append((state, discovery.challenge, pending.future, bridge._negotiations[state.id]))

    def observe_close(connection_id: UUID) -> None:
        state, challenge, pending, task = starts[-1]
        assert state.id == connection_id and state.requests is not None
        coroutine_state = inspect.getcoroutinestate(task.get_coro())
        original_close(connection_id)
        closes.append(
            (
                coroutine_state,
                challenge.cancelled(),
                pending.cancelled(),
                state.requests.pending_count,
            )
        )

    monkeypatch.setattr(bridge, "start", observe_start)
    monkeypatch.setattr(bridge, "close", observe_close)
    for _ in range(3):
        websocket = QueueWebSocket()
        await websocket.close()
        await facade._on_connection(websocket)

    assert len(starts) == 3
    assert not bridge._discoveries
    assert not bridge._negotiations
    assert not bridge._connections
    assert not bridge._controls
    assert not bridge._legacy
    assert not bridge._leases
    assert not bridge._lease_acks
    assert closes == [(inspect.CORO_CREATED, True, True, 0)] * 3
    for state, challenge, pending, task in starts:
        assert state.requests is not None and state.requests.pending_count == 0
        assert challenge.cancelled() and pending.cancelled() and task.cancelled()
        original_close(state.id)
        original_close(state.id)
