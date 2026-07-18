"""Connection state objects owned by the gateway.

This is the gateway's minimal, dependency-free view of an active connection and
the per-player session state it carries. The concrete websockets transport, the
response queue, and host-specific extension data (template / provider / custom
variables) live on the :class:`ConnectionState`; the *agent-broker* pieces
response queue / MessageBroker remain the host's concern and are injected at
construction time (see :class:`~mcbe_ws_sdk.gateway.server_facade.McbeServerFacade`).

Multi-player isolation: a single WebSocket connection is shared by many players
in the MCBE server model, so every per-player setting is bucketed by
``player_name`` via :meth:`ConnectionState.get_player_session`. The top-level
``player_name`` is ONLY a convenience pointer to "most recent speaker" and MUST
NOT be read for routing decisions — always pull the bucket from ``player_event.sender``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

DEFAULT_PLAYER_KEY = "__anonymous__"


@dataclass
class PlayerSession:
    """Per-player, per-connection mutable settings (multiplayer isolation bucket)."""

    player_name: str
    context_enabled: bool = True
    custom_variables: dict[str, str] = field(default_factory=dict)


@dataclass
class ConnectionState:
    """Immutable identity + player-session buckets for one connection.

    Host-specific / transport-specific fields are intentionally absent — the
    gateway never imports ``websockets`` directly and never touches the agent's
    message broker. Anything the host needs (a websocket handle, a queue) is
    attached by the host via ``ConnectionManager`` / the facade, not stored here.
    """

    id: UUID = field(default_factory=uuid4)
    authenticated: bool = False
    player_name: str | None = None  # most-recent speaker convenience pointer only
    _player_sessions: dict[str, PlayerSession] = field(default_factory=dict)

    def get_player_session(self, player_name: str | None = DEFAULT_PLAYER_KEY) -> PlayerSession:
        """Return the per-player bucket, creating a default one if missing."""
        key = player_name or DEFAULT_PLAYER_KEY
        session = self._player_sessions.get(key)
        if session is None:
            session = PlayerSession(player_name=key)
            self._player_sessions[key] = session
        return session

    def clear_player_sessions(self) -> None:
        """Drop every player session bucket (called on disconnect)."""
        self._player_sessions.clear()

    def all_player_sessions(self) -> list[PlayerSession]:
        return list(self._player_sessions.values())
