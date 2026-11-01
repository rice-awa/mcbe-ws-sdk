"""The single public ``McbeServer`` entry and its native command client."""

from __future__ import annotations

import asyncio
import json

from mcbe_ws_sdk import (
    FlowControlSettings,
    GatewaySettings,
    McbeServer,
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


def test_entry_owns_settings_and_bridge() -> None:
    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(port=9999),
        flow=FlowControlSettings(command_line_byte_budget=400),
    )
    bridge = V2Bridge()
    server = McbeServer(settings=settings, v2=bridge)
    assert server.v2 is bridge
    assert server.settings is settings
    assert server.settings.flow is settings.flow
    assert server.settings.websocket is settings.websocket
    assert McbeServer().settings.flow.command_line_byte_budget == 461


async def test_native_response_pairs_and_connection_releases() -> None:
    hook = RecordingHook()
    server = McbeServer(hook=hook)
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
    assert hook.disconnected.is_set()


async def test_native_command_can_be_awaited_inside_player_callback() -> None:
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
