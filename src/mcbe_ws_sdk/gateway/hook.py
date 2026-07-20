"""Connection lifecycle hook protocol.

The host (the main repo's ``McbeHost``) implements these six points to
inject the application-specific behaviour the gateway deliberately does NOT
implement: prompt/context management, LLM dispatch, command routing, and addon
linkage. ``NoOpHook`` is the gateway's built-in default — it defines the
complete contract so a host can subclass and override only what it needs.

All hooks return ``None`` and are purely side-effecting. ``on_player_message``
receives an optional pre-parsed :class:`~mcbe_ws_sdk.command.registry.ParsedCommand`
so the host does not need to re-run the registry.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcbe_ws_sdk.command.registry import ParsedCommand
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)


@runtime_checkable
class ConnectionHook(Protocol):
    """Lifecycle + protocol hooks the host injects into the gateway."""

    async def on_connected(self, state: ConnectionState) -> None:
        """Fired after the transport connection is established."""
        ...

    async def on_disconnected(self, state: ConnectionState) -> None:
        """Fired on transport disconnect — host clears per-connection state here."""
        ...

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: ParsedCommand | None = None,
    ) -> None:
        """Inbound chat/scriptevent from a player.

        ``parsed`` is the registry match for ``player_event.message`` when one
        exists; ``None`` means free-form chat (or no matching command prefix).
        """
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
    """Gateway default ``ConnectionHook`` — every hook is a no-op."""

    async def on_connected(self, state: ConnectionState) -> None:
        return None

    async def on_disconnected(self, state: ConnectionState) -> None:
        return None

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: ParsedCommand | None = None,
    ) -> None:
        return None

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
