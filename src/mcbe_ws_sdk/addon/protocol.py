"""Addon bridge protocol codec.

Relocated from the main repo ``services/addon/protocol.py``.

Codec functions accept an explicit ``protocol: AddonProtocolConfig`` parameter
instead of a hidden module-level ``_protocol()`` global. Callers (the bridge
session and service) hold their own configuration and pass it in, so the codec
has no module-level mutable state and no read of a global settings singleton.

Framing models (``AddonBridgeChunk``, ``AddonBridgeResponse``, ``UiChatChunk``,
``UiChatMessage``) live here with the codec because they are pure data shapes of
the wire protocol rather than business models.
"""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

from mcbe_ws_sdk.config import AddonProtocolConfig
from mcbe_ws_sdk.protocol.addon import (
    AddonBridgeChunk,
    AddonBridgeResponse,
    UiChatChunk,
    UiChatMessage,
)

if TYPE_CHECKING:
    from mcbe_ws_sdk.flow.flow_control import FlowControlMiddleware


def _split_prefix(expected: str) -> tuple[str, str]:
    """Split a ``"NAMESPACE|PREFIX"`` value into its two halves."""
    namespace, _, prefix = expected.partition("|")
    return namespace, prefix


def encode_bridge_request(
    request_id: str,
    capability: str,
    payload: dict[str, Any],
    protocol: AddonProtocolConfig | None = None,
) -> str:
    """Encode a bridge request as a ``scriptevent`` command string."""
    p = protocol if protocol is not None else AddonProtocolConfig()
    body = {
        "request_id": request_id,
        "capability": capability,
        "payload": payload,
    }
    return f"scriptevent {p.bridge_message_id} {json.dumps(body, ensure_ascii=False)}"


def decode_bridge_chat_chunk(
    chunk: str,
    protocol: AddonProtocolConfig | None = None,
) -> AddonBridgeChunk:
    """Parse a bridge response fragment out of a chat message.

    Format: ``MCBEAI|RESP|<request_id>|i/n|<content>``
    """
    p = protocol if protocol is not None else AddonProtocolConfig()
    parts = chunk.split("|", 4)
    if len(parts) != 5:
        raise ValueError("Invalid bridge chunk format")

    namespace, prefix, request_id, part, content = parts
    expected_namespace, expected_prefix = _split_prefix(p.bridge_prefix)
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
    except (ValueError, TypeError) as exc:
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
    """Reassemble fragments and decode the JSON payload."""
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


def encode_ai_response_chunks(
    player_name: str,
    role: str,
    text: str,
    flow: FlowControlMiddleware,
) -> list[str]:
    """Encode an AI response into ``scriptevent`` fragment commands.

    Delegates to the flow middleware's unified chunking so byte-safety and the
    reassembly contract match every other outbound path.
    """
    return flow.chunk_ai_response(player_name=player_name, role=role, text=text)


def decode_ui_chat_chunk(
    chunk: str,
    protocol: AddonProtocolConfig | None = None,
) -> UiChatChunk:
    """Parse a UI chat fragment out of a chat message.

    Format: ``MCBEAI|UI_CHAT|<msg_id>|i/n|<content>``
    """
    p = protocol if protocol is not None else AddonProtocolConfig()
    parts = chunk.split("|", 4)
    if len(parts) != 5:
        raise ValueError("Invalid UI chat chunk format")

    namespace, prefix, msg_id, part, content = parts
    expected_namespace, expected_prefix = _split_prefix(p.ui_chat_prefix)
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
    except (ValueError, TypeError) as exc:
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
    """Reassemble UI chat fragments and decode the JSON payload."""
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
