from __future__ import annotations

import json
import warnings

import pytest

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.errors import FrameTooLargeError, ProtocolError
from mcbe_ws_sdk.flow import FlowControlMiddleware
from mcbe_ws_sdk.profiles.mcbews_v1 import (
    MCBEWS_V1,
    MCBEWS_V1_MANIFEST,
    MCBEWS_V1_WIRE_VECTORS,
    ApprovalDecision,
    SessionRequest,
    SessionResponse,
    UiChatChunk,
    classify_tool_player_message,
    decode_approval_decision,
    decode_text_response_frame,
    encode_approval_decision,
    encode_session_response_command,
    encode_text_response_commands,
    reassemble_text_response_chunks,
    reassemble_ui_chat_chunks,
)


def _command_line(payload: str) -> str:
    return json.loads(payload)["body"]["commandLine"]


def test_manifest_and_vectors_are_installed_authority() -> None:
    assert MCBEWS_V1_MANIFEST["protocol_line"] == "MCBEWS/1"
    assert MCBEWS_V1_MANIFEST["versions"] == {
        "capability_request_schema": 2,
        "session_schema": 1,
        "text_response_framing": 1,
        "ddui_persistence": 2,
    }
    assert MCBEWS_V1_MANIFEST["text_response"]["usage_completion_only"] is True
    assert "session" in MCBEWS_V1_WIRE_VECTORS
    assert MCBEWS_V1_WIRE_VECTORS["ui_chat"][0]["conversation_id"] == "chat-a"


def test_deprecated_alias_is_read_only_and_warns() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert MCBEWS_V1.request_version == MCBEWS_V1.capability_request_schema_version
    assert any(item.category is DeprecationWarning for item in caught)

    with pytest.raises((AttributeError, TypeError)):
        MCBEWS_V1.request_version = 1  # type: ignore[misc]


def test_typed_approval_round_trip_and_owner_validation() -> None:
    decision = ApprovalDecision(
        approval_id="approval-1",
        player_name="Steve",
        conversation_id="chat-a",
        decision="deny",
    )
    encoded = encode_approval_decision(decision)
    assert encoded.startswith("MCBEWS|TOOL_DENY|")
    decoded = decode_approval_decision(encoded)
    assert decoded.approval_id == "approval-1"
    assert decoded.player_name == "Steve"
    assert not decoded.approved

    with pytest.raises(ProtocolError):
        decode_approval_decision(
            'MCBEWS|TOOL_APPROVE|{"v":1,"approval_id":"approval-1",'
            '"player_name":"Steve","cid":"chat-a","decision":"deny"}'
        )


def test_trusted_tool_player_classifier_never_uses_sender_as_business_player() -> None:
    message = 'MCBEWS|SESSION|{"v":1,"request_id":"s1","action":"list","player_name":"Steve"}'
    assert classify_tool_player_message("Steve", message) is None
    classified = classify_tool_player_message("MCBEWS_BRIDGE", message)
    assert classified is not None
    assert classified.session_request is not None
    assert classified.session_request.player_name == "Steve"


def test_text_usage_is_completion_only_and_cid_survives_reassembly() -> None:
    flow = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=180))
    payloads = encode_text_response_commands(
        player_name="Steve",
        role="assistant",
        text="answer " * 100,
        flow=flow,
        response_id="response-1",
        conversation_id="chat-a",
        title="Chat",
        usage={"i": 3, "o": 5},
    )
    chunks = []
    for payload in payloads:
        frame = json.loads(_command_line(payload).split(" ", 2)[2])
        chunks.append(decode_text_response_frame(frame))
    assert all(chunk.usage is None for chunk in chunks[:-1])
    assert chunks[-1].usage is not None
    message = reassemble_text_response_chunks(chunks)
    assert message.conversation_id == "chat-a"
    assert message.usage is not None and message.usage.output_tokens == 5

    with pytest.raises(ProtocolError, match="UNKNOWN_TEXT_ROLE"):
        decode_text_response_frame(
            {"id": "r", "i": 1, "n": 1, "p": "Steve", "r": "tool", "c": "x"}
        )


def test_ui_chat_cid_round_trip() -> None:
    message = reassemble_ui_chat_chunks(
        [
            UiChatChunk(
                msg_id="ui-1",
                chunk_index=1,
                total_chunks=1,
                content='{"player":"Steve","message":"你好 😀","cid":"chat-a"}',
            )
        ]
    )
    assert message.player_name == "Steve"
    assert message.conversation_id == "chat-a"


def test_session_response_oversize_is_one_correlated_error() -> None:
    response = SessionResponse(
        request_id="s1",
        action="list",
        ok=True,
        data={"sessions": ["x" * 2000]},
    )
    flow = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=461))
    payload = encode_session_response_command(response, flow)
    command_line = _command_line(payload)
    assert command_line.startswith("scriptevent mcbews:session_resp ")
    body = json.loads(command_line.split(" ", 2)[2])
    assert body["request_id"] == "s1"
    assert body["ok"] is False
    assert body["error"]["code"] == "SESSION_RESPONSE_TOO_LARGE"
    assert len(command_line.encode("utf-8")) <= 461

    tiny_budget = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=20))
    with pytest.raises(FrameTooLargeError):
        encode_session_response_command(response, tiny_budget)


def test_session_request_typed_validation_rejects_missing_action_data() -> None:
    with pytest.raises(ValueError, match="switch requires cid"):
        SessionRequest(request_id="s1", action="switch", player_name="Steve")


def test_complete_behavior_vectors_cover_bounds_duplicates_and_empty_wrappers() -> None:
    behavior = MCBEWS_V1_WIRE_VECTORS["behavior"]
    for vector in behavior["chunking"]:
        frame = (
            f"{vector['prefix']}|{vector['id']}|1/1|{vector['payload']}"
        )
        wrapped_bytes = len(f"{vector['wrapper_prefix']}{frame}".encode())
        if vector["name"] == "empty-wrapper-no-room":
            assert wrapped_bytes > vector["budget"]
        elif vector["name"] == "empty-payload":
            assert vector["payload"] == ""
            assert wrapped_bytes <= vector["budget"]
        else:
            assert vector["payload"]
            assert len(vector["payload"]) <= vector["max_content_code_points"]
            assert wrapped_bytes > vector["budget"]

    for vector in behavior["text_response"]:
        chunks = [decode_text_response_frame(dict(raw_chunk)) for raw_chunk in vector["chunks"]]
        if "expected_error" in vector:
            with pytest.raises(ProtocolError, match=vector["expected_error"]):
                reassemble_text_response_chunks(chunks)
            continue
        message = reassemble_text_response_chunks(chunks)
        expected = vector["expected"]
        assert message.response_id == expected["response_id"]
        assert message.player_name == expected["player_name"]
        assert message.role == expected["role"]
        assert message.content == expected["text"]
        assert message.conversation_id == expected["conversation_id"]
        assert message.usage is not None
        assert message.usage.model_dump(by_alias=True) == expected["usage"]
