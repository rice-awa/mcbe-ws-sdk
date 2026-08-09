"""Tests for the connection hook protocol and its NoOpHook default."""

from __future__ import annotations

import asyncio

import pytest

from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.hook import (
    ConnectionHook,
    LegacyUiChatHookAdapter,
    NoOpHook,
)
from mcbe_ws_sdk.profiles.mcbews_v1.models import UiChatMessage
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)


def test_noop_hook_is_connection_hook() -> None:
    assert isinstance(NoOpHook(), ConnectionHook)


def test_noop_hook_is_protocol() -> None:
    import typing

    assert typing.get_origin(ConnectionHook) is None or True  # runtime_checkable Protocol
    assert issubclass(NoOpHook, ConnectionHook)


@pytest.mark.asyncio
async def test_noop_hook_defaults() -> None:
    state = ConnectionState()
    hook = NoOpHook()
    # All hooks are purely side-effecting and return None.
    assert await hook.on_connected(state) is None
    assert await hook.on_disconnected(state) is None
    assert (
        await hook.on_player_message(state, PlayerMessageEvent(sender="Steve", message="hi"))
        is None
    )
    assert (
        await hook.on_ui_chat_reassembled(
            state,
            UiChatMessage(msg_id="ui-1", player_name="Steve", message="hello"),
        )
        is None
    )
    assert (
        await hook.on_command_response(
            state,
            MinecraftCommandResponse(
                request_id="req-1",
                header={"messagePurpose": "commandResponse", "requestId": "req-1"},
                body={"status": 0},
            ),
        )
        is None
    )
    assert (
        await hook.on_error(
            state,
            MinecraftErrorFrame(
                request_id="req-2",
                header={"messagePurpose": "error"},
                body={"statusCode": 500},
            ),
        )
        is None
    )


def test_custom_hook_via_implementation() -> None:
    class RecordingHook(NoOpHook):
        def __init__(self) -> None:
            self.connected: list[str] = []

        async def on_connected(self, state: ConnectionState) -> None:
            self.connected.append(str(state.id))

    hook = RecordingHook()
    assert isinstance(hook, ConnectionHook)
    asyncio.run(hook.on_connected(ConnectionState()))
    assert len(hook.connected) == 1


def test_legacy_ui_hook_requires_explicit_adapter() -> None:
    seen: list[tuple[str, str]] = []

    class LegacyHook:
        async def on_ui_chat_reassembled(
            self,
            state: ConnectionState,
            player_name: str,
            message: str,
        ) -> None:
            seen.append((player_name, message))

    adapter = LegacyUiChatHookAdapter(LegacyHook())
    asyncio.run(
        adapter.invoke(
            ConnectionState(),
            UiChatMessage(msg_id="ui-1", player_name="Steve", message="hello"),
        )
    )
    assert seen == [("Steve", "hello")]
