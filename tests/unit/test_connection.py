"""Tests for the gateway connection state objects (ConnectionState / PlayerSession)."""

from __future__ import annotations

from uuid import UUID

from mcbe_ws_sdk.gateway.connection import (
    DEFAULT_PLAYER_KEY,
    ConnectionState,
    PlayerSession,
)


def test_default_player_key_is_anonymous() -> None:
    assert DEFAULT_PLAYER_KEY == "__anonymous__"


def test_connection_state_has_unique_id() -> None:
    a = ConnectionState()
    b = ConnectionState()
    assert isinstance(a.id, UUID)
    assert a.id != b.id


def test_get_player_session_creates_and_caches() -> None:
    state = ConnectionState()
    first = state.get_player_session("Steve")
    assert isinstance(first, PlayerSession)
    assert first.player_name == "Steve"
    assert first.context_enabled is True
    # Same key returns the same bucket object (identity).
    assert state.get_player_session("Steve") is first


def test_player_session_defaults() -> None:
    session = PlayerSession(player_name="Alex")
    assert session.context_enabled is True
    assert session.custom_variables == {}


def test_anonymous_player_key() -> None:
    state = ConnectionState()
    anon = state.get_player_session(None)
    assert anon.player_name == DEFAULT_PLAYER_KEY
    assert state.get_player_session("") is anon


def test_clear_player_sessions_drops_all_buckets() -> None:
    state = ConnectionState()
    steve_before = state.get_player_session("Steve")
    assert len(state.all_player_sessions()) == 1
    state.clear_player_sessions()
    assert state.all_player_sessions() == []
    # A fresh bucket is created after a clear — it is NOT the same object.
    steve_after = state.get_player_session("Steve")
    assert steve_after is not steve_before
