"""Tests for outbound addon bridge request encoding."""

from __future__ import annotations

import json

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
    assert json.loads(command[len(prefix):]) == {
        "v": 2,
        "request_id": "r1",
        "capability": "get_greeting",
        "payload": {"player": "Steve"},
    }


def test_encode_bridge_request_uses_custom_protocol_message_id() -> None:
    command = encode_bridge_request(
        "r2",
        "ping",
        {},
        profile=McbewsV1Profile(bridge_request_message_id="custom:bridge"),
    )

    assert command.startswith("scriptevent custom:bridge ")
