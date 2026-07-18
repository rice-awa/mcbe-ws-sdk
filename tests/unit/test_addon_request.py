"""Tests for the inbound addon bridge request model and its scriptevent parser."""

from __future__ import annotations

import json

from mcbe_ws_sdk.protocol.addon import AddonBridgeRequest, parse_addon_bridge_request


def test_parse_addon_bridge_request_happy_path() -> None:
    body = json.dumps(
        {
            "request_id": "r1",
            "capability": "get_greeting",
            "payload": {"player": "Steve"},
        }
    )
    cmd = f"scriptevent mcbeai:bridge_request {body}"
    parsed = parse_addon_bridge_request(cmd, "mcbeai:bridge_request")
    assert parsed == AddonBridgeRequest(
        request_id="r1",
        capability="get_greeting",
        payload={"player": "Steve"},
    )


def test_parse_rejects_non_scriptevent() -> None:
    assert parse_addon_bridge_request("say hello", "mcbeai:bridge_request") is None


def test_parse_rejects_other_message_id() -> None:
    cmd = 'scriptevent server:data {"request_id":"r1","capability":"x","payload":{}}'
    assert parse_addon_bridge_request(cmd, "mcbeai:bridge_request") is None


def test_parse_rejects_bad_json() -> None:
    cmd = "scriptevent mcbeai:bridge_request {not json"
    assert parse_addon_bridge_request(cmd, "mcbeai:bridge_request") is None


def test_parse_rejects_non_object_json() -> None:
    cmd = "scriptevent mcbeai:bridge_request [1,2,3]"
    assert parse_addon_bridge_request(cmd, "mcbeai:bridge_request") is None


def test_parse_rejects_schema_violation() -> None:
    # Missing required "capability".
    cmd = 'scriptevent mcbeai:bridge_request {"request_id":"r1","payload":{}}'
    assert parse_addon_bridge_request(cmd, "mcbeai:bridge_request") is None
