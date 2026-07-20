"""Tests for the gateway connection state objects (ConnectionState)."""

from __future__ import annotations

import dataclasses
from uuid import UUID

from mcbe_ws_sdk.gateway.connection import ConnectionState


def test_connection_state_has_unique_id() -> None:
    a = ConnectionState()
    b = ConnectionState()
    assert isinstance(a.id, UUID)
    assert a.id != b.id


def test_connection_state_contains_no_host_session_fields() -> None:
    fields = {field.name for field in dataclasses.fields(ConnectionState)}
    assert "authenticated" not in fields
    assert "_player_sessions" not in fields
    assert "context_enabled" not in fields
    assert "custom_variables" not in fields
