from __future__ import annotations

import json
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.models import (
    AddonBridgeChunk,
    AddonBridgeRequest,
    AddonBridgeResponse,
    UiChatChunk,
    UiChatMessage,
)
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import (
    LEGACY_MCBEAI_V1,
    LegacyMcbeAiV1Profile,
)

if TYPE_CHECKING:
    from mcbe_ws_sdk.flow.flow_control import FlowControlMiddleware
    from mcbe_ws_sdk.profiles import AddonBridgeProfile


def _split_prefix(expected: str) -> tuple[str, str]:
    namespace, _, prefix = expected.partition("|")
    return namespace, prefix


def encode_bridge_request(
    request_id: str,
    capability: str,
    payload: dict[str, Any],
    profile: LegacyMcbeAiV1Profile | AddonBridgeProfile = LEGACY_MCBEAI_V1,
) -> str:
    body = AddonBridgeRequest(
        v=profile.request_version,  # type: ignore[arg-type]  # Protocol int vs Literal[2]
        request_id=request_id,
        capability=capability,
        payload=payload,
    ).model_dump_json()
    return f"scriptevent {profile.bridge_request_message_id} {body}"


def decode_bridge_chat_chunk(
    chunk: str,
    profile: LegacyMcbeAiV1Profile | AddonBridgeProfile = LEGACY_MCBEAI_V1,
) -> AddonBridgeChunk:
    parts = chunk.split("|", 4)
    if len(parts) != 5:
        raise ValueError("Invalid bridge chunk format")

    namespace, prefix, request_id, part, content = parts
    expected_namespace, expected_prefix = _split_prefix(profile.bridge_response_prefix)
    if namespace != expected_namespace:
        raise ValueError("Invalid bridge chunk namespace")
    if prefix != expected_prefix:
        raise ValueError("Invalid bridge chunk prefix")
    if not request_id:
        raise ValueError("Invalid bridge chunk metadata")

    try:
        index_str, total_str = part.split("/", 1)
        chunk_index = int(index_str)
        total_chunks = int(total_str)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid bridge chunk metadata") from exc

    if chunk_index <= 0 or total_chunks <= 0 or chunk_index > total_chunks:
        raise ValueError("Invalid bridge chunk metadata")

    return AddonBridgeChunk(
        request_id=request_id,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        content=content,
    )


def reassemble_bridge_chunks(chunks: list[AddonBridgeChunk]) -> AddonBridgeResponse:
    if not chunks:
        raise ValueError("Bridge chunks must not be empty")

    sorted_chunks = sorted(chunks, key=lambda item: item.chunk_index)
    request_id = sorted_chunks[0].request_id
    total_chunks = sorted_chunks[0].total_chunks

    if any(chunk.request_id != request_id for chunk in sorted_chunks):
        raise ValueError("Bridge chunks request_id mismatch")
    if any(chunk.total_chunks != total_chunks for chunk in sorted_chunks):
        raise ValueError("Bridge chunks total_chunks mismatch")

    expected_indexes = list(range(1, total_chunks + 1))
    actual_indexes = [chunk.chunk_index for chunk in sorted_chunks]
    if actual_indexes != expected_indexes:
        raise ValueError("Bridge chunks are incomplete or out of sequence")

    content = "".join(chunk.content for chunk in sorted_chunks)
    try:
        payload = json.loads(content)
    except JSONDecodeError as exc:
        raise ValueError("Invalid bridge payload JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid bridge payload JSON")

    return AddonBridgeResponse(request_id=request_id, payload=payload)


def decode_ui_chat_chunk(
    chunk: str,
    profile: LegacyMcbeAiV1Profile | AddonBridgeProfile = LEGACY_MCBEAI_V1,
) -> UiChatChunk:
    parts = chunk.split("|", 4)
    if len(parts) != 5:
        raise ValueError("Invalid UI chat chunk format")

    namespace, prefix, msg_id, part, content = parts
    expected_namespace, expected_prefix = _split_prefix(profile.ui_chat_prefix)
    if namespace != expected_namespace:
        raise ValueError("Invalid UI chat chunk namespace")
    if prefix != expected_prefix:
        raise ValueError("Invalid UI chat chunk prefix")
    if not msg_id:
        raise ValueError("Invalid UI chat chunk metadata")

    try:
        index_str, total_str = part.split("/", 1)
        chunk_index = int(index_str)
        total_chunks = int(total_str)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid UI chat chunk metadata") from exc

    if chunk_index <= 0 or total_chunks <= 0 or chunk_index > total_chunks:
        raise ValueError("Invalid UI chat chunk metadata")

    return UiChatChunk(
        msg_id=msg_id,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        content=content,
    )


def reassemble_ui_chat_chunks(chunks: list[UiChatChunk]) -> UiChatMessage:
    if not chunks:
        raise ValueError("UI chat chunks must not be empty")

    sorted_chunks = sorted(chunks, key=lambda item: item.chunk_index)
    msg_id = sorted_chunks[0].msg_id
    total_chunks = sorted_chunks[0].total_chunks

    if any(chunk.msg_id != msg_id for chunk in sorted_chunks):
        raise ValueError("UI chat chunks msg_id mismatch")
    if any(chunk.total_chunks != total_chunks for chunk in sorted_chunks):
        raise ValueError("UI chat chunks total_chunks mismatch")

    expected_indexes = list(range(1, total_chunks + 1))
    actual_indexes = [chunk.chunk_index for chunk in sorted_chunks]
    if actual_indexes != expected_indexes:
        raise ValueError("UI chat chunks are incomplete or out of sequence")

    content = "".join(chunk.content for chunk in sorted_chunks)
    try:
        payload = json.loads(content)
    except JSONDecodeError as exc:
        raise ValueError("Invalid UI chat payload JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid UI chat payload JSON")

    player_name = payload.get("player", "")
    message = payload.get("message", "")
    if not message:
        raise ValueError("Invalid UI chat payload: missing message")

    return UiChatMessage(msg_id=msg_id, player_name=player_name, message=message)


def encode_legacy_response_commands(
    *,
    player_name: str,
    role: str,
    text: str,
    flow: FlowControlMiddleware,
    response_id: str | None = None,
    profile: LegacyMcbeAiV1Profile = LEGACY_MCBEAI_V1,
) -> list[str]:
    message_id = response_id or f"resp-{uuid4().hex}"

    def encode_frame(content: str, index: int, total: int) -> str:
        return json.dumps(
            {
                "id": message_id,
                "i": index,
                "n": total,
                "p": player_name,
                "r": role,
                "c": content,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    return flow.chunk_framed_scriptevent(
        text,
        message_id=profile.response_message_id,
        encode_frame=encode_frame,
        emit_empty=True,
    )
