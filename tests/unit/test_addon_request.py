"""Tests for outbound addon bridge request encoding."""

from __future__ import annotations

import json

import pytest

from mcbe_ws_sdk.errors import ConfigurationError
from mcbe_ws_sdk.profiles.mcbews_v1.codec import encode_bridge_request
from mcbe_ws_sdk.profiles.mcbews_v1.profile import McbewsV1Profile


def test_encode_bridge_request_uses_bridge_message_id_and_json_body() -> None:
    command = encode_bridge_request(
        "r1",
        "get_greeting",
        {"player": "Steve"},
    )

    prefix = "scriptevent mcbews:bridge_req "
    assert command.startswith(prefix)
    assert json.loads(command[len(prefix) :]) == {
        "v": 2,
        "request_id": "r1",
        "capability": "get_greeting",
        "payload": {"player": "Steve"},
    }


def test_profile_rejects_custom_protocol_message_id() -> None:
    with pytest.raises(ConfigurationError, match="fixed by the MCBEWS/1 manifest"):
        McbewsV1Profile(bridge_request_message_id="custom:bridge")
