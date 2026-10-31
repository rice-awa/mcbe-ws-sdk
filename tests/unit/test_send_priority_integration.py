"""R03 integration: real delivery/runtime/command paths carry frame categories.

The test drives ``McbeOutboundDelivery`` (long tellraw), ``ConnectionRuntime``
and ``NativeCommandClient`` together over the reliable facade, then inspects the
scheduler's queued categories. This is the path production code uses; it is not
a synthetic call to ``OutboundScheduler.send`` with a hand-picked priority.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from mcbe_ws_sdk.config import (
    DispatchSettings,
    FlowControlSettings,
    GatewaySettings,
    SendSchedulerSettings,
)
from mcbe_ws_sdk.delivery.outbound import McbeOutboundDelivery
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.sender import SendPriority
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink


class GatedWebSocket:
    """Async-iterable transport whose sends can be held by a gate."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.gated = False
        self.gate = asyncio.Event()
        self.send_started = asyncio.Event()
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, payload: str) -> None:
        if self.gated:
            self.send_started.set()
            await self.gate.wait()
        self.sent.append(payload)

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


class ConnectedHook(NoOpHook):
    def __init__(self) -> None:
        self.connected = asyncio.Event()
        self.state: ConnectionState | None = None

    async def on_connected(self, state: ConnectionState) -> None:
        self.state = state
        self.connected.set()


async def _wait_until(predicate: object, *, timeout: float = 2.0) -> None:
    iterations = int(timeout / 0.005)
    for _ in range(iterations):
        if predicate():  # type: ignore[operator]
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached before the timeout")


def _command_response_frame(request_id: str) -> str:
    return json.dumps(
        {
            "header": {"requestId": request_id, "messagePurpose": "commandResponse"},
            "body": {"statusCode": 0, "statusMessage": "ok"},
        }
    )


@pytest.mark.asyncio
async def test_two_long_tellraws_and_a_command_keep_distinct_priorities() -> None:
    hook = ConnectedHook()
    settings = GatewaySettings(
        dispatch=DispatchSettings(mode="reliable"),
        send=SendSchedulerSettings(),
    )
    facade = McbeServerFacade(
        settings=settings,
        hook=hook,
        sink=DefaultResponseSink(),
    )
    websocket = GatedWebSocket()
    connection_task = asyncio.create_task(facade._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), timeout=2)
    assert hook.state is not None
    state = hook.state
    assert state.send_payload is not None
    assert state.send_bulk is not None
    assert state.command_client is not None
    assert state.runtime is not None

    flow = FlowControlSettings()
    delivery_one = McbeOutboundDelivery(
        connection_id=state.id,
        send_payload=state.send_payload,
        send_bulk=state.send_bulk,
        settings=flow,
    )
    delivery_two = McbeOutboundDelivery(
        connection_id=state.id,
        send_payload=state.send_payload,
        send_bulk=state.send_bulk,
        settings=flow,
    )

    websocket.gated = True
    long_text = "long tellraw content " * 60
    tellraw_one = asyncio.create_task(
        delivery_one.send_tellraw(long_text, color="", source="integration", target="Alice")
    )
    # Wait until the first bulk frame owns the transport, then queue the second
    # tellraw and the command behind it with a known gap.
    await asyncio.wait_for(websocket.send_started.wait(), timeout=2)
    tellraw_two = asyncio.create_task(
        delivery_two.send_tellraw(long_text, color="", source="integration", target="Bob")
    )
    command = asyncio.create_task(state.command_client.run("say hi"))
    scheduler = state.runtime.scheduler

    try:
        await _wait_until(
            lambda: scheduler.pending_by_priority[SendPriority.BULK] >= 1
            and scheduler.pending_by_priority[SendPriority.REQUEST] >= 1
        )
        queued = scheduler.pending_by_priority
        assert queued[SendPriority.BULK] >= 1
        assert queued[SendPriority.REQUEST] >= 1

        websocket.gate.set()
        await asyncio.wait_for(_wait_until(lambda: _find_command_id(websocket)), timeout=2)
        request_id = _find_command_id(websocket)
        assert request_id is not None
        await websocket.push(_command_response_frame(request_id))
        response = await asyncio.wait_for(command, timeout=2)
        assert response.request_id == request_id
        await asyncio.wait_for(asyncio.gather(tellraw_one, tellraw_two), timeout=2)
    finally:
        websocket.gate.set()
        await websocket.close()
        await asyncio.wait_for(connection_task, timeout=2)

    sent_by_priority = scheduler.sent_by_priority
    assert sent_by_priority[SendPriority.BULK] >= 2
    assert sent_by_priority[SendPriority.REQUEST] >= 1


def _find_command_id(websocket: GatedWebSocket) -> str | None:
    for payload in websocket.sent:
        try:
            data: Any = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        body = data.get("body") if isinstance(data, dict) else None
        if not isinstance(body, dict) or body.get("commandLine") != "say hi":
            continue
        header = data.get("header")
        if isinstance(header, dict) and header.get("requestId"):
            return str(header["requestId"])
    return None
