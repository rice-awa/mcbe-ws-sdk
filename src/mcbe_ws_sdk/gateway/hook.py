"""Connection lifecycle hook protocol.

The host (the main repo's ``McbeHost``) implements these six / seven points to
inject the application-specific behaviour the gateway deliberately does NOT
implement: login/JWT handling, prompt/context management, LLM dispatch, command
routing, and addon linkage. ``NoOpHook`` is the gateway's built-in default — it
defines the complete contract so a host can subclass and override only what it
needs.

Hook return convention:
  * ``on_player_message`` / ``on_bridge_message`` return ``bool`` — ``True`` means
    "consumed, stop the default handler"; ``False`` lets the default proceed.
  * All other hooks return ``None`` and are purely side-effecting.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.protocol.addon import AddonBridgeRequest
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)


@runtime_checkable
class ConnectionHook(Protocol):
    """Lifecycle + protocol hooks the host injects into the gateway."""

    async def on_connected(self, state: ConnectionState) -> None:
        """Fired after the transport connection is established (pre-auth)."""
        ...

    async def on_authenticated(self, state: ConnectionState, player: str) -> None:
        """Fired when a player successfully authenticates on this connection."""
        ...

    async def on_disconnected(self, state: ConnectionState) -> None:
        """Fired on transport disconnect — host clears per-connection state here."""
        ...

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
    ) -> bool:
        """Inbound chat/scriptevent from a player. Return True to mark consumed."""
        ...

    async def on_bridge_message(
        self,
        state: ConnectionState,
        request: AddonBridgeRequest,
    ) -> bool:
        """Inbound addon capability request (``mcbeai:bridge_request``). Return True to consume."""
        ...

    async def on_ui_chat_reassembled(
        self,
        state: ConnectionState,
        player_name: str,
        message: str,
    ) -> None:
        """Fired when a fragmented UI_CHAT message is fully reassembled."""
        ...

    async def on_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
        """Inbound ``commandResponse`` (resolves run_command futures)."""
        ...

    async def on_error(self, state: ConnectionState, error: MinecraftErrorFrame) -> None:
        """Inbound typed ``error`` frame."""
        ...


class NoOpHook:
    """Gateway default ``ConnectionHook`` — every hook is a no-op / not-consumed."""

    async def on_connected(self, state: ConnectionState) -> None:
        return None

    async def on_authenticated(self, state: ConnectionState, player: str) -> None:
        return None

    async def on_disconnected(self, state: ConnectionState) -> None:
        return None

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
    ) -> bool:
        return False

    async def on_bridge_message(
        self,
        state: ConnectionState,
        request: AddonBridgeRequest,
    ) -> bool:
        return False

    async def on_ui_chat_reassembled(
        self,
        state: ConnectionState,
        player_name: str,
        message: str,
    ) -> None:
        return None

    async def on_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
        return None

    async def on_error(self, state: ConnectionState, error: MinecraftErrorFrame) -> None:
        return None
