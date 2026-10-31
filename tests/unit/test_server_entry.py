"""Public server defaults and command awaiting work through a live reader."""

from __future__ import annotations

import asyncio
import json

import pytest

from mcbe_ws_sdk import (
    DispatchSettings,
    FlowControlSettings,
    GatewaySettings,
    McbeServer,
    McbeServerFacade,
    V2Bridge,
    WebsocketTransportConfig,
)

from .test_reliable_facade import (
    QueueWebSocket,
    RecordingHook,
    _command_response_frame,
    _player_frame,
    _stop,
)


def test_new_entry_preserves_settings_and_legacy_defaults() -> None:
    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(port=9999),
        flow=FlowControlSettings(command_line_byte_budget=400),
        dispatch=DispatchSettings(business_queue_maxsize=17),
    )
    bridge = V2Bridge()
    server = McbeServer(settings=settings, v2=bridge)
    assert server.v2 is bridge
    assert server.reliable_dispatch
    assert server.settings.dispatch.business_queue_maxsize == 17
    assert server.settings.flow is settings.flow
    assert server.settings.websocket is settings.websocket
    assert settings.dispatch.mode == "legacy"
    assert not McbeServerFacade().reliable_dispatch
    assert McbeServer().settings.flow.command_line_byte_budget == 461
    assert McbeServer().v2.negotiate


@pytest.mark.parametrize("entry", [McbeServer, McbeServerFacade])
async def test_public_entries_pair_native_response_and_release_connection(entry) -> None:
    hook = RecordingHook()
    server = entry(hook=hook)
    websocket = QueueWebSocket()
    connection = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    assert hook.state is not None and hook.state.command_client is not None

    async def answer(payload: str) -> None:
        packet = json.loads(payload)
        if packet.get("body", {}).get("commandLine") == "time query daytime":
            await websocket.push(_command_response_frame(packet["header"]["requestId"]))

    websocket.on_send = answer
    try:
        response = await asyncio.wait_for(hook.state.command_client.run("time query daytime"), 2)
        assert response.body["statusCode"] == 0
    finally:
        await _stop(websocket, connection)
    assert server.manager.connection_count == 0
    assert hook.state.requests.pending_count == 0


async def test_new_entry_can_await_native_command_inside_player_callback() -> None:
    hook = RecordingHook()
    done = asyncio.Event()

    async def command(state, _event) -> None:
        response = await state.command_client.run("time query daytime")
        assert response.body["statusCode"] == 0
        done.set()

    hook.on_player = command
    server = McbeServer(hook=hook)
    websocket = QueueWebSocket()

    async def answer(payload: str) -> None:
        packet = json.loads(payload)
        if packet.get("body", {}).get("commandLine") == "time query daytime":
            await websocket.push(_command_response_frame(packet["header"]["requestId"]))

    websocket.on_send = answer
    connection = asyncio.create_task(server._on_connection(websocket))
    await asyncio.wait_for(hook.connected.wait(), 2)
    try:
        await websocket.push(_player_frame("Alex", "!wscmd time query daytime"))
        await asyncio.wait_for(done.wait(), 2)
    finally:
        await _stop(websocket, connection)
