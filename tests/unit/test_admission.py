"""Mandatory MCBEWS/2 admission: readiness, rejection and cleanup."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import mcbe_ws_sdk.profiles.mcbews_v2.runtime as v2_runtime
from mcbe_ws_sdk.config import GatewaySettings
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.events import WsEventType
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge
from mcbe_ws_sdk.server import McbeServer

from .test_reliable_facade import (
    QueueWebSocket,
    RecordingHook,
    _player_frame,
    _stop,
)

_NEGOTIATION_PREFIX = "scriptevent mcbews:negotiate_v2 "


class CountingHook(RecordingHook):
    def __init__(self) -> None:
        super().__init__()
        self.connected_count = 0
        self.disconnected_count = 0

    async def on_connected(self, state: ConnectionState) -> None:
        self.connected_count += 1
        await super().on_connected(state)

    async def on_disconnected(self, state: ConnectionState) -> None:
        self.disconnected_count += 1
        await super().on_disconnected(state)


def _negotiation_command(payload: str) -> list[str] | None:
    try:
        command = json.loads(payload).get("body", {}).get("commandLine", "")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    if not command.startswith(_NEGOTIATION_PREFIX):
        return None
    return command[len(_NEGOTIATION_PREFIX) :].split("|")


async def _start(
    hook: RecordingHook, websocket: QueueWebSocket, bridge: V2Bridge | None = None
) -> tuple[McbeServer, asyncio.Task[None]]:
    server = McbeServer(settings=GatewaySettings(), hook=hook, v2=bridge)
    task = asyncio.create_task(server._on_connection(websocket))
    return server, task


async def test_valid_negotiation_emits_ready_once() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.5)
    hook = CountingHook()
    websocket = QueueWebSocket()
    server = McbeServer(settings=GatewaySettings(), hook=hook, v2=bridge)
    events: list[Any] = []
    server.manager.event_bus.subscribe(
        WsEventType.CONNECTED,
        lambda state: events.append(state.id),
        weak=False,
    )
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    try:
        assert hook.connected_count == 1
        assert hook.state is not None
        assert events == [hook.state.id]
        assert hook.state.ready
        assert bridge.connection(hook.state.id) is not None
    finally:
        await _stop(websocket, task)
    assert hook.connected_count == 1
    assert events == [hook.state.id]
    assert hook.disconnected_count == 1


async def test_no_addon_times_out_and_rejects_without_business_events() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.05)
    hook = CountingHook()
    websocket = QueueWebSocket(auto_negotiate=False)
    server, task = await _start(hook, websocket, bridge)
    await asyncio.wait_for(task, 2)

    assert hook.connected_count == 0
    assert hook.messages == []
    assert websocket.close_code == 1008
    assert websocket.close_reason == "NEGOTIATION_TIMEOUT"
    notices = [
        json.loads(payload)["body"]["commandLine"]
        for payload in websocket.sent
        if "mcbews:protocol_reject" in payload
    ]
    assert notices and notices[-1].endswith("NEGOTIATION_TIMEOUT")
    assert server.manager.connection_count == 0


async def test_explicit_old_version_replies_with_unsupported_protocol() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.5)
    hook = CountingHook()
    websocket = QueueWebSocket(auto_negotiate=False)

    async def answer(payload: str) -> None:
        parts = _negotiation_command(payload)
        if parts and parts[0] == "MCBEWS2H":
            await websocket.push(
                _player_frame(
                    "MCBEWS_BRIDGE",
                    f"MCBEWS2N|{parts[1]}|old|1|metadata/1",
                )
            )

    websocket.on_send = answer
    server, task = await _start(hook, websocket, bridge)
    await asyncio.wait_for(task, 2)
    assert websocket.close_reason == "UNSUPPORTED_PROTOCOL"
    assert hook.connected_count == 0


async def test_unsupported_challenge_version_is_rejected_immediately() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.5)
    hook = CountingHook()
    websocket = QueueWebSocket(auto_negotiate=False)

    async def answer(payload: str) -> None:
        parts = _negotiation_command(payload)
        if parts and parts[0] == "MCBEWS2H":
            await websocket.push(
                _player_frame(
                    "MCBEWS_BRIDGE",
                    f"MCBEWS2N|{parts[1]}|epoch|3|metadata/1",
                )
            )

    websocket.on_send = answer
    server, task = await _start(hook, websocket, bridge)
    await asyncio.wait_for(task, 2)
    assert websocket.close_reason == "UNSUPPORTED_PROTOCOL"
    assert hook.connected_count == 0


async def test_pre_ready_business_is_dropped_without_side_effects() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.5)
    hook = CountingHook()
    websocket = QueueWebSocket(auto_negotiate=False)

    async def answer(payload: str) -> None:
        parts = _negotiation_command(payload)
        if parts and parts[0] == "MCBEWS2H":
            await websocket.push(
                _player_frame(
                    "MCBEWS_BRIDGE",
                    f"MCBEWS2N|{parts[1]}|epoch|2|metadata/1,stream/1",
                )
            )
        elif parts and parts[0] == "MCBEWS2C":
            await websocket.push(
                _player_frame("MCBEWS_BRIDGE", f"MCBEWS2N|{parts[1]}|{parts[2]}|ready")
            )

    websocket.on_send = answer
    await websocket.push(_player_frame("Alice", "early chat"))
    await websocket.push(_player_frame("Alice", "MCBEWS2|a.early.1|1/1|{}"))
    server, task = await _start(hook, websocket, bridge)
    await asyncio.wait_for(hook.connected.wait(), 2)
    try:
        assert hook.messages == []
        assert hook.state is not None
        assert hook.state.pre_ready_dropped >= 2
    finally:
        await _stop(websocket, task)


async def test_old_business_frames_after_ready_are_not_executed() -> None:
    hook = CountingHook()
    websocket = QueueWebSocket()
    server, task = await _start(hook, websocket)
    await asyncio.wait_for(hook.connected.wait(), 2)
    try:
        await websocket.push(
            _player_frame("MCBEWS_BRIDGE", 'MCBEWS|BRIDGE|req-1|1/1|{"ok":true}')
        )
        await websocket.push(
            _player_frame("MCBEWS_BRIDGE", 'MCBEWS|SESSION|{"request_id":"s1"}')
        )
        await asyncio.sleep(0.02)
        assert hook.messages == []
        assert not any("mcbews:bridge_req" in payload for payload in websocket.sent)
    finally:
        await _stop(websocket, task)


async def test_disconnect_race_settles_once_and_releases_resources() -> None:
    bridge = V2Bridge(negotiation_timeout_seconds=0.5)
    hook = CountingHook()
    websocket = QueueWebSocket()
    server = McbeServer(settings=GatewaySettings(), hook=hook, v2=bridge)
    task = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    state = hook.state
    assert state is not None

    await asyncio.gather(websocket.close(), websocket.close())
    await asyncio.wait_for(task, 2)

    assert hook.connected_count == 1
    assert hook.disconnected_count == 1
    assert server.manager.connection_count == 0
    assert server.manager._sender_tasks == {}
    assert server.manager._drop_tasks == {}
    assert bridge.connection(state.id) is None
    assert bridge.pool.stats["buffers"] == 0
    assert state.requests is not None and state.requests.pending_count == 0
    assert state.runtime is not None and state.runtime.business.task_count == 0
    assert state.admission_state == "CLOSED"


async def test_lease_expiry_revokes_admission_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(v2_runtime._NEGOTIATION, "refresh_ms", 10)
    bridge = V2Bridge(negotiation_timeout_seconds=0.5)
    hook = CountingHook()
    websocket = QueueWebSocket()
    server, task = await _start(hook, websocket, bridge)
    await asyncio.wait_for(hook.connected.wait(), 2)
    await asyncio.wait_for(task, 2)

    assert websocket.close_code == 1008
    assert websocket.close_reason == "SESSION_INVALID"
    assert hook.connected_count == 1
    assert hook.disconnected_count == 1
    assert server.manager.connection_count == 0


def test_production_python_has_no_v1_business_identifiers() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "mcbe_ws_sdk"
    forbidden = (
        "MCBEWS/1",
        "mcbews_v1",
        "AddonBridge",
        "McbeServerFacade",
        "mcbews:bridge_req",
        "mcbews:text_resp",
        "UiChatMessage",
        "ToolPlayerMessage",
        "SessionRequest",
        "ApprovalDecision",
        "v1_methods",
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text("utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(root)}: {token}")
    assert offenders == []
