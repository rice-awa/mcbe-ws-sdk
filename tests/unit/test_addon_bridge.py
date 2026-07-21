"""Tests for the addon bridge (codec + session + service).

Verifies the key extraction invariants:
  * No module-level ``_addon_bridge_service`` singleton — two instances with
    different timeouts are independent.
  * Codec round-trips with the isolated legacy profile.
  * Session reassembles multi-fragment bridge responses and UI chat.
  * Service.request_capability drives the request/future lifecycle.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from mcbe_ws_sdk.addon.service import AddonBridgeService, AddonMessageResult
from mcbe_ws_sdk.addon.session import AddonBridgeSession
from mcbe_ws_sdk.config import AddonBridgeSettings
from mcbe_ws_sdk.errors import (
    BridgeClosedError,
    BridgeLimitError,
    BridgeTimeoutError,
    ProtocolError,
)
from mcbe_ws_sdk.profiles.mcbews_v1.codec import (
    AddonBridgeResponse,
    decode_bridge_chat_chunk,
    encode_bridge_request,
    reassemble_bridge_chunks,
)
from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    AddonBridgeChunk,
    AddonBridgeRequest,
    UiChatChunk,
    UiChatMessage,
)
from mcbe_ws_sdk.profiles.mcbews_v1.profile import McbewsV1Profile


def _complete_ui_chunk(player: str, message: str) -> str:
    return f'MCBEWS|UI_CHAT|m1|1/1|{{"player":"{player}","message":"{message}"}}'


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
    cmd = encode_bridge_request("req-1", "get_greeting", {"player": "Steve"})
    assert cmd.startswith("scriptevent mcbews:bridge_req ")

    chunk = "MCBEWS|BRIDGE|req-1|1/2|hello"
    parsed = decode_bridge_chat_chunk(chunk)
    assert parsed.request_id == "req-1"
    assert parsed.chunk_index == 1
    assert parsed.total_chunks == 2
    assert parsed.content == "hello"


def test_reassemble_bridge_chunks_parses_json_payload() -> None:
    # Reassembly joins fragments BEFORE JSON parse, so boundaries may split JSON.
    chunks = [
        decode_bridge_chat_chunk('MCBEWS|BRIDGE|r1|1/2|{"greet":"hi, '),
        decode_bridge_chat_chunk('MCBEWS|BRIDGE|r1|2/2|Steve"}'),
    ]
    response = reassemble_bridge_chunks(chunks)
    assert isinstance(response, AddonBridgeResponse)
    assert response.request_id == "r1"
    assert response.payload == {"greet": "hi, Steve"}


@pytest.mark.asyncio
async def test_session_reassembles_bridge_response() -> None:
    session = AddonBridgeSession(AddonBridgeSettings())
    request = session.create_request(capability="get_greeting", payload={"player": "Steve"})

    rid = request.request_id
    first = session.handle_chat_chunk(f"MCBEWS|BRIDGE|{rid}|1/2|" + '{"k":"')
    assert isinstance(first, AddonBridgeChunk)
    assert not request.future.done()
    second = session.handle_chat_chunk(f"MCBEWS|BRIDGE|{rid}|2/2|" + 'v"}')
    assert isinstance(second, AddonBridgeChunk)
    assert request.future.done()
    assert await request.future == {"k": "v"}


def test_session_reassembles_ui_chat() -> None:
    session = AddonBridgeSession(AddonBridgeSettings())

    first_chunk, first_message = session.handle_ui_chat_chunk(
        'MCBEWS|UI_CHAT|m1|1/2|{"player":"Steve","message":"he'
    )
    assert isinstance(first_chunk, UiChatChunk)
    assert first_message is None

    second_chunk, second_message = session.handle_ui_chat_chunk('MCBEWS|UI_CHAT|m1|2/2|llo"}')
    assert isinstance(second_chunk, UiChatChunk)
    assert second_message == UiChatMessage(msg_id="m1", player_name="Steve", message="hello")


def test_legacy_wire_models_preserve_unknown_fields() -> None:
    request = AddonBridgeRequest.model_validate(
        {
            "v": 2,
            "request_id": "r1",
            "capability": "greet",
            "payload": {"name": "Steve"},
            "trace": "keep-me",
        }
    )
    bridge_chunk = AddonBridgeChunk.model_validate(
        {
            "request_id": "r1",
            "chunk_index": 1,
            "total_chunks": 1,
            "content": "{}",
            "trace": "chunk-extra",
        }
    )
    response = AddonBridgeResponse.model_validate(
        {"request_id": "r1", "payload": {"ok": True}, "trace": "resp-extra"}
    )
    ui_chunk = UiChatChunk.model_validate(
        {
            "msg_id": "m1",
            "chunk_index": 1,
            "total_chunks": 1,
            "content": '{"player":"Steve","message":"hi"}',
            "trace": "ui-extra",
        }
    )
    ui_message = UiChatMessage.model_validate(
        {
            "msg_id": "m1",
            "player_name": "Steve",
            "message": "hi",
            "trace": "msg-extra",
        }
    )

    assert request.model_extra == {"trace": "keep-me"}
    assert request.model_dump()["trace"] == "keep-me"
    assert bridge_chunk.model_dump()["trace"] == "chunk-extra"
    assert response.model_extra == {"trace": "resp-extra"}
    assert response.model_dump()["trace"] == "resp-extra"
    assert ui_chunk.model_extra == {"trace": "ui-extra"}
    assert ui_chunk.model_dump()["trace"] == "ui-extra"
    assert ui_message.model_extra == {"trace": "msg-extra"}
    assert ui_message.model_dump()["trace"] == "msg-extra"


def test_profile_can_override_bridge_request_message_id() -> None:
    profile = McbewsV1Profile(bridge_request_message_id="custom:bridge")
    command = encode_bridge_request("r1", "ping", {}, profile=profile)
    assert command.startswith("scriptevent custom:bridge ")


@pytest.mark.asyncio
async def test_service_request_capability_end_to_end() -> None:
    service = AddonBridgeService(AddonBridgeSettings(timeout_seconds=5.0))
    connection_id = UUID(int=1)

    sent: list[str] = []

    async def send_command(cmd: str) -> None:
        sent.append(cmd)
        return None

    # Drive request + resolver concurrently so _send_command actually runs.
    async def resolve_once_pending() -> None:
        await asyncio.sleep(0.02)
        for _ in range(100):
            session = service._sessions.get(connection_id)
            if session is not None and session._pending_requests:
                rid = next(iter(session._pending_requests))
                payload = '{"ok":true}'
                session.handle_chat_chunk(f"MCBEWS|BRIDGE|{rid}|1/1|" + payload)
                return
            await asyncio.sleep(0.005)

    request = asyncio.create_task(
        service.request_capability(connection_id, "get_greeting", {"player": "Steve"}, send_command)
    )
    resolver = asyncio.create_task(resolve_once_pending())

    result = await request
    await resolver
    assert result == {"ok": True}
    assert len(sent) == 1
    assert sent[0].startswith("scriptevent mcbews:bridge_req ")


@pytest.mark.asyncio
async def test_send_failure_cleans_pending_request() -> None:
    service = AddonBridgeService(AddonBridgeSettings())
    connection_id = UUID(int=51)

    async def fail(command: str) -> None:
        raise LookupError("send failed")

    with pytest.raises(LookupError):
        await service.request_capability(connection_id, "x", {}, fail)
    assert service._sessions[connection_id]._pending_requests == {}


@pytest.mark.asyncio
async def test_send_transport_timeout_propagates_and_cleans_pending_request() -> None:
    service = AddonBridgeService(AddonBridgeSettings())
    connection_id = UUID(int=52)
    transport_error = TimeoutError("transport timeout")

    async def timeout(command: str) -> None:
        raise transport_error

    with pytest.raises(TimeoutError, match="transport timeout") as exc_info:
        await service.request_capability(connection_id, "x", {}, timeout)
    assert exc_info.value is transport_error
    assert service._sessions[connection_id]._pending_requests == {}


@pytest.mark.asyncio
async def test_timeout_is_typed_and_cleans_pending_request() -> None:
    service = AddonBridgeService(AddonBridgeSettings(timeout_seconds=0.01))
    connection_id = UUID(int=53)

    async def accept(command: str) -> None:
        return None

    with pytest.raises(BridgeTimeoutError):
        await service.request_capability(connection_id, "x", {}, accept)
    assert service._sessions[connection_id]._pending_requests == {}


@pytest.mark.asyncio
async def test_caller_cancellation_remains_cancelled_and_cleans_pending_request() -> None:
    service = AddonBridgeService(AddonBridgeSettings())
    connection_id = UUID(int=54)

    async def accept(command: str) -> None:
        return None

    task = asyncio.create_task(service.request_capability(connection_id, "x", {}, accept))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert service._sessions[connection_id]._pending_requests == {}


@pytest.mark.asyncio
async def test_close_connection_finishes_pending_request_with_bridge_closed() -> None:
    service = AddonBridgeService(AddonBridgeSettings())
    connection_id = UUID(int=55)
    session = service._session_for(connection_id)
    request = session.create_request("x", {})

    service.close_connection(connection_id)

    with pytest.raises(BridgeClosedError):
        await request.future


@pytest.mark.asyncio
async def test_session_rejects_excessive_chunk_count() -> None:
    session = AddonBridgeSession(AddonBridgeSettings(max_chunks_per_message=2))
    request = session.create_request("x", {})
    with pytest.raises(BridgeLimitError):
        session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/3|x")
    assert request.request_id not in session._pending_requests
    assert request.future.done()
    with pytest.raises(BridgeLimitError):
        await request.future


@pytest.mark.asyncio
async def test_session_prunes_expired_buffers_before_accepting_new_chunk() -> None:
    """Expired buffers prune on every accept, even far below the old 75% watermark."""
    now = 100.0

    def clock() -> float:
        return now

    # max_buffer_ids large enough that one buffer is well under 75% of the limit,
    # so prune must run unconditionally (not only at high watermark).
    session = AddonBridgeSession(
        AddonBridgeSettings(buffer_ttl_seconds=1.0, max_buffer_ids=10),
        clock=clock,
    )
    request = session.create_request("x", {})

    session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/2|a")
    assert session._chunk_buffers[request.request_id].chunks.keys() == {1}

    now = 101.0
    chunk, ui_message = session.handle_ui_chat_chunk(
        'MCBEWS|UI_CHAT|m1|1/2|{"player":"Steve","message":"he'
    )

    assert isinstance(chunk, UiChatChunk)
    assert ui_message is None
    assert request.request_id not in session._chunk_buffers
    assert "m1" in session._ui_chat_chunk_buffers
    assert request.future.done()
    with pytest.raises(BridgeLimitError, match="chunk buffer expired"):
        await request.future


@pytest.mark.asyncio
async def test_session_rejects_excessive_buffer_ids() -> None:
    session = AddonBridgeSession(AddonBridgeSettings(max_buffer_ids=1))
    request = session.create_request("x", {})

    session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/2|a")

    with pytest.raises(BridgeLimitError, match="maximum buffer ids exceeded"):
        session.handle_ui_chat_chunk('MCBEWS|UI_CHAT|m1|1/2|{"player":"Steve","message":"he')


@pytest.mark.asyncio
async def test_session_rejects_message_byte_limit() -> None:
    session = AddonBridgeSession(AddonBridgeSettings(max_message_bytes=1))
    request = session.create_request("x", {})

    with pytest.raises(BridgeLimitError, match="message byte limit exceeded"):
        session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/2|ab")
    assert request.request_id not in session._pending_requests
    assert request.future.done()
    with pytest.raises(BridgeLimitError, match="message byte limit exceeded"):
        await request.future


@pytest.mark.asyncio
async def test_session_rejects_total_buffer_byte_limit() -> None:
    session = AddonBridgeSession(
        AddonBridgeSettings(max_message_bytes=10, max_total_buffer_bytes=3)
    )
    request = session.create_request("x", {})

    session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/2|ab")

    with pytest.raises(BridgeLimitError, match="total buffer byte limit exceeded"):
        session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|2/2|cd")
    assert request.request_id not in session._pending_requests
    assert request.request_id not in session._chunk_buffers
    assert request.future.done()
    with pytest.raises(BridgeLimitError, match="total buffer byte limit exceeded"):
        await request.future


@pytest.mark.asyncio
async def test_session_rejects_changed_total_and_completes_request() -> None:
    session = AddonBridgeSession(AddonBridgeSettings())
    request = session.create_request("x", {})

    session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/2|a")

    with pytest.raises(ProtocolError, match="chunk total changed"):
        session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|2/3|b")
    assert request.request_id not in session._pending_requests
    assert request.request_id not in session._chunk_buffers
    assert request.future.done()
    with pytest.raises(ProtocolError, match="chunk total changed"):
        await request.future


@pytest.mark.asyncio
async def test_session_rejects_duplicate_changed_content_and_completes_request() -> None:
    session = AddonBridgeSession(AddonBridgeSettings())
    request = session.create_request("x", {})

    session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/2|a")

    with pytest.raises(ProtocolError, match="duplicate chunk content changed"):
        session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/2|b")
    assert request.request_id not in session._pending_requests
    assert request.request_id not in session._chunk_buffers
    assert request.future.done()
    with pytest.raises(ProtocolError, match="duplicate chunk content changed"):
        await request.future


@pytest.mark.asyncio
async def test_malformed_bridge_response_completes_future_with_protocol_error() -> None:
    session = AddonBridgeSession(AddonBridgeSettings())
    request = session.create_request("x", {})

    with pytest.raises(ProtocolError):
        session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|1/1|not-json")

    assert request.request_id not in session._pending_requests
    assert request.request_id not in session._chunk_buffers
    assert request.future.done()
    with pytest.raises(ProtocolError):
        await request.future


@pytest.mark.asyncio
async def test_decode_stage_malformed_bridge_chunk_completes_pending_request() -> None:
    session = AddonBridgeSession(AddonBridgeSettings())
    request = session.create_request("x", {})

    with pytest.raises(ProtocolError):
        session.handle_chat_chunk(f"MCBEWS|BRIDGE|{request.request_id}|3/2|x")

    assert request.request_id not in session._pending_requests
    assert request.request_id not in session._chunk_buffers
    assert request.future.done()
    with pytest.raises(ProtocolError):
        await request.future


def test_is_bridge_and_ui_chat_message() -> None:
    service = AddonBridgeService(AddonBridgeSettings())
    assert service.is_bridge_chat_message("MCBEWS_BRIDGE", "MCBEWS|BRIDGE|r1|1/1|data") is True
    assert service.is_bridge_chat_message("MCBEWS_BRIDGE", "MCBEWS|UI_CHAT|m1|1|data") is False
    assert service.is_ui_chat_message("MCBEWS_BRIDGE", "MCBEWS|UI_CHAT|m1|1|data") is True
    assert service.is_ui_chat_message("RealPlayer", "MCBEWS|BRIDGE|r1|1|data") is False


@pytest.mark.asyncio
async def test_service_handle_player_message_returns_structured_bridge_chunk() -> None:
    service = AddonBridgeService(AddonBridgeSettings())
    connection_id = UUID(int=11)
    session = service._session_for(connection_id)
    request = session.create_request(capability="demo", payload={"x": 1})

    result = await service.handle_player_message(
        connection_id,
        "MCBEWS_BRIDGE",
        f'MCBEWS|BRIDGE|{request.request_id}|1/1|{{"ok":true}}',
    )

    assert result == AddonMessageResult(
        handled=True,
        bridge_chunk=AddonBridgeChunk(
            request_id=request.request_id,
            chunk_index=1,
            total_chunks=1,
            content='{"ok":true}',
        ),
    )


@pytest.mark.asyncio
async def test_service_handle_player_message_returns_structured_ui_result() -> None:
    service = AddonBridgeService(AddonBridgeSettings())
    seen: list[tuple[UUID, str, str]] = []

    async def on_ui(connection_id: UUID, player_name: str, message: str) -> None:
        seen.append((connection_id, player_name, message))

    service.set_ui_chat_callback(on_ui)
    connection_id = UUID(int=12)

    result = await service.handle_player_message(
        connection_id,
        "MCBEWS_BRIDGE",
        'MCBEWS|UI_CHAT|m1|1/1|{"player":"Steve","message":"hello"}',
    )

    assert result == AddonMessageResult(
        handled=True,
        ui_chunk=UiChatChunk(
            msg_id="m1",
            chunk_index=1,
            total_chunks=1,
            content='{"player":"Steve","message":"hello"}',
        ),
        ui_message=UiChatMessage(msg_id="m1", player_name="Steve", message="hello"),
    )
    assert seen == [(connection_id, "Steve", "hello")]


@pytest.mark.asyncio
async def test_ui_callback_failure_is_awaited_and_no_task_leaks() -> None:
    service = AddonBridgeService(AddonBridgeSettings())

    async def fail_callback(connection_id: UUID, player: str, message: str) -> None:
        raise LookupError("callback failed")

    service.set_ui_chat_callback(fail_callback)
    before = set(asyncio.all_tasks())
    with pytest.raises(LookupError, match="callback failed"):
        await service.handle_player_message(
            UUID(int=52), "MCBEWS_BRIDGE", _complete_ui_chunk("Alice", "hello")
        )
    assert set(asyncio.all_tasks()) == before
