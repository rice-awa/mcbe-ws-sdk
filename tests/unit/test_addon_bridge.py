"""Tests for the addon bridge (codec + session + service).

Verifies the key extraction invariants:
  * No module-level ``_addon_bridge_service`` singleton — two instances with
    different timeouts are independent.
  * Codec round-trips with explicit ``AddonProtocolConfig``.
  * Session reassembles multi-fragment bridge responses and UI chat.
  * Service.request_capability drives the request/future lifecycle.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from mcbe_ws_sdk.addon.protocol import (
    AddonBridgeResponse,
    decode_bridge_chat_chunk,
    encode_bridge_request,
    reassemble_bridge_chunks,
)
from mcbe_ws_sdk.addon.service import AddonBridgeService
from mcbe_ws_sdk.addon.session import AddonBridgeSession
from mcbe_ws_sdk.config import AddonBridgeSettings, AddonProtocolConfig


def test_no_module_level_singleton() -> None:
    import mcbe_ws_sdk.addon.service as svc_mod

    assert not hasattr(svc_mod, "_addon_bridge_service")
    assert not hasattr(svc_mod, "get_addon_bridge_service")


def test_two_instances_are_independent() -> None:
    a = AddonBridgeService(AddonBridgeSettings(timeout_seconds=1.0))
    b = AddonBridgeService(AddonBridgeSettings(timeout_seconds=2.0))
    assert a is not b
    assert a._timeout_seconds == 1.0
    assert b._timeout_seconds == 2.0


def test_encode_then_decode_bridge_chunk_roundtrip() -> None:
    protocol = AddonProtocolConfig()
    cmd = encode_bridge_request("req-1", "get_greeting", {"player": "Steve"}, protocol=protocol)
    assert cmd.startswith("scriptevent mcbeai:bridge_request ")

    chunk = "MCBEAI|RESP|req-1|1/2|hello"
    parsed = decode_bridge_chat_chunk(chunk, protocol=protocol)
    assert parsed.request_id == "req-1"
    assert parsed.chunk_index == 1
    assert parsed.total_chunks == 2
    assert parsed.content == "hello"


def test_reassemble_bridge_chunks_parses_json_payload() -> None:
    protocol = AddonProtocolConfig()
    # Reassembly joins fragments BEFORE JSON parse, so boundaries may split JSON.
    chunks = [
        decode_bridge_chat_chunk('MCBEAI|RESP|r1|1/2|{"greet":"hi, ', protocol=protocol),
        decode_bridge_chat_chunk('MCBEAI|RESP|r1|2/2|Steve"}', protocol=protocol),
    ]
    response = reassemble_bridge_chunks(chunks)
    assert isinstance(response, AddonBridgeResponse)
    assert response.request_id == "r1"
    assert response.payload == {"greet": "hi, Steve"}


@pytest.mark.asyncio
async def test_session_reassembles_bridge_response() -> None:
    protocol = AddonProtocolConfig()
    session = AddonBridgeSession(protocol=protocol)
    request = session.create_request(capability="get_greeting", payload={"player": "Steve"})

    rid = request.request_id
    assert session.handle_chat_chunk(f"MCBEAI|RESP|{rid}|1/2|" + '{"k":"') is True
    assert not request.future.done()
    assert session.handle_chat_chunk(f"MCBEAI|RESP|{rid}|2/2|" + 'v"}') is True
    assert request.future.done()
    assert await request.future == {"k": "v"}


def test_session_reassembles_ui_chat() -> None:
    protocol = AddonProtocolConfig()
    session = AddonBridgeSession(protocol=protocol)

    assert (
        session.handle_ui_chat_chunk('MCBEAI|UI_CHAT|m1|1/2|{"player":"Steve","message":"he')
        is None
    )
    result = session.handle_ui_chat_chunk('MCBEAI|UI_CHAT|m1|2/2|llo"}')
    assert result == ("Steve", "hello")


@pytest.mark.asyncio
async def test_service_request_capability_end_to_end() -> None:
    service = AddonBridgeService(AddonBridgeSettings(timeout_seconds=5.0))
    connection_id = UUID(int=1)

    sent: list[str] = []

    async def send_command(cmd: str) -> str:
        sent.append(cmd)
        return "ok"

    # Drive request + resolver concurrently so _send_command actually runs.
    async def resolve_once_pending() -> None:
        await asyncio.sleep(0.02)
        for _ in range(100):
            session = service._sessions.get(connection_id)
            if session is not None and session._pending_requests:
                rid = next(iter(session._pending_requests))
                payload = '{"ok":true}'
                session.handle_chat_chunk(f"MCBEAI|RESP|{rid}|1/1|" + payload)
                return
            await asyncio.sleep(0.005)

    request = asyncio.create_task(
        service.request_capability(
            connection_id, "get_greeting", {"player": "Steve"}, send_command
        )
    )
    resolver = asyncio.create_task(resolve_once_pending())

    result = await request
    await resolver
    assert result == {"ok": True}
    assert len(sent) == 1
    assert sent[0].startswith("scriptevent mcbeai:bridge_request ")


def test_is_bridge_and_ui_chat_message() -> None:
    service = AddonBridgeService(AddonBridgeSettings())
    assert service.is_bridge_chat_message("MCBEAI_TOOL", "MCBEAI|RESP|r1|1/1|data") is True
    assert service.is_bridge_chat_message("MCBEAI_TOOL", "MCBEAI|UI_CHAT|m1|1|data") is False
    assert service.is_ui_chat_message("MCBEAI_TOOL", "MCBEAI|UI_CHAT|m1|1|data") is True
    assert service.is_ui_chat_message("RealPlayer", "MCBEAI|RESP|r1|1|data") is False
