from __future__ import annotations

import json
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from mcbe_ws_sdk.errors import FrameTooLargeError, ProtocolError
from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    AddonBridgeChunk,
    AddonBridgeRequest,
    AddonBridgeResponse,
    ApprovalDecision,
    SessionAction,
    SessionRequest,
    SessionResponse,
    TextResponseChunk,
    TextResponseMessage,
    TokenUsage,
    UiChatChunk,
    UiChatMessage,
)
from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1, McbewsV1Profile

if TYPE_CHECKING:
    from mcbe_ws_sdk.flow.flow_control import FlowControlMiddleware


def _split_prefix(expected: str) -> tuple[str, str]:
    namespace, separator, prefix = expected.partition("|")
    if not separator or not namespace or not prefix:
        raise ProtocolError("invalid MCBEWS/1 chat prefix")
    return namespace, prefix


ModelT = TypeVar("ModelT", bound=BaseModel)


def _validate_model(model_type: type[ModelT], value: Any) -> ModelT:
    try:
        return model_type.model_validate(value)
    except ValidationError as exc:
        raise ProtocolError(str(exc)) from exc


def encode_bridge_request(
    request_id: str,
    capability: str,
    payload: dict[str, Any],
    profile: McbewsV1Profile = MCBEWS_V1,
) -> str:
    body = _validate_model(
        AddonBridgeRequest,
        {
            "v": profile.capability_request_schema_version,
            "request_id": request_id,
            "capability": capability,
            "payload": payload,
        },
    ).model_dump_json(by_alias=True, exclude_none=True)
    return f"scriptevent {profile.capability_request_script_event_id} {body}"


def decode_bridge_chat_chunk(
    chunk: str,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> AddonBridgeChunk:
    parts = chunk.split("|", 4)
    if len(parts) != 5:
        raise ValueError("Invalid bridge chunk format")

    namespace, prefix, request_id, part, content = parts
    expected_namespace, expected_prefix = _split_prefix(profile.capability_response_chat_prefix)
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
    profile: McbewsV1Profile = MCBEWS_V1,
) -> UiChatChunk:
    parts = chunk.split("|", 4)
    if len(parts) != 5:
        raise ValueError("Invalid UI chat chunk format")

    namespace, prefix, msg_id, part, content = parts
    expected_namespace, expected_prefix = _split_prefix(profile.ui_chat_chunk_prefix)
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

    player_name = payload.get("player", payload.get("player_name", ""))
    message = payload.get("message", "")
    conversation_id = payload.get("cid", payload.get("conversation_id", "default"))
    if (
        not isinstance(player_name, str)
        or not isinstance(message, str)
        or not isinstance(conversation_id, str)
    ):
        raise ValueError("Invalid UI chat payload fields")
    if not player_name or not message:
        raise ValueError("Invalid UI chat payload: missing player or message")

    return _validate_model(
        UiChatMessage,
        {
            "msg_id": msg_id,
            "player_name": player_name,
            "message": message,
            "cid": conversation_id or "default",
        },
    )


def encode_session_request_chat(
    request: SessionRequest,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> str:
    """Encode the atomic session request chat envelope."""

    body = json.dumps(
        request.model_dump(by_alias=True, exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{profile.session_request_chat_prefix}|{body}"


def decode_session_request_chat(
    message: str,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> SessionRequest:
    """Decode ``MCBEWS|SESSION|<json>`` into a validated DTO."""

    prefix = f"{profile.session_request_chat_prefix}|"
    if not message.startswith(prefix):
        raise ProtocolError("invalid session request prefix")
    body = message[len(prefix) :]
    try:
        value = json.loads(body)
    except JSONDecodeError as exc:
        raise ProtocolError("invalid session request JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("session request must be a JSON object")
    return _validate_model(SessionRequest, value)


def encode_session_response_json(response: SessionResponse) -> str:
    """Serialize a session response with compact wire aliases."""

    return json.dumps(
        response.model_dump(by_alias=True, exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _session_response_error(
    response: SessionResponse,
    *,
    request_id: str,
    action: SessionAction,
) -> SessionResponse:
    return SessionResponse.failure(
        request_id=request_id,
        action=action,
        code="SESSION_RESPONSE_TOO_LARGE",
        message="session response exceeds the MCBEWS/1 atomic frame budget",
    )


def encode_session_response_command(
    response: SessionResponse,
    flow: FlowControlMiddleware,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> str:
    """Encode one atomic ``mcbews:session_resp`` commandRequest payload.

    A large successful result is replaced by a correlated structured error.  No
    generic chunker is called, so a receiver never sees a fragment that looks
    like a complete JSON response.
    """

    command = (
        f"scriptevent {profile.session_response_script_event_id} "
        f"{encode_session_response_json(response)}"
    )
    def atomic_payload(raw_command: str) -> str:
        if len(raw_command.encode("utf-8")) > profile.session_response_max_command_bytes:
            raise FrameTooLargeError(
                "session response command exceeds the MCBEWS/1 atomic command budget"
            )
        return flow.chunk_raw_command(raw_command)[0]

    try:
        return atomic_payload(command)
    except FrameTooLargeError:
        fallback = _session_response_error(
            response,
            request_id=response.request_id,
            action=response.action,
        )
        fallback_command = (
            f"scriptevent {profile.session_response_script_event_id} "
            f"{encode_session_response_json(fallback)}"
        )
        try:
            return atomic_payload(fallback_command)
        except FrameTooLargeError as exc:
            raise FrameTooLargeError(
                "SESSION_RESPONSE_TOO_LARGE error cannot fit the configured atomic frame budget"
            ) from exc


def decode_session_response_message(
    message: str,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> SessionResponse:
    """Decode an Addon ``mcbews:session_resp`` event body."""

    try:
        value = json.loads(message)
    except JSONDecodeError as exc:
        raise ProtocolError("invalid session response JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("session response must be a JSON object")
    return _validate_model(SessionResponse, value)


def _decode_approval_payload(
    value: Any,
    *,
    decision: Literal["approve", "deny"],
) -> ApprovalDecision:
    if isinstance(value, str):
        approval_id = value.strip()
        if not approval_id:
            raise ProtocolError("approval_id must not be empty")
        return ApprovalDecision(approval_id=approval_id, decision=decision, legacy=True)
    if not isinstance(value, dict):
        raise ProtocolError("approval decision must be a JSON object or legacy id")
    body = dict(value)
    claimed_decision = body.get("decision")
    if claimed_decision is not None and claimed_decision != decision:
        raise ProtocolError("approval decision prefix and payload disagree")
    body["decision"] = decision
    try:
        return ApprovalDecision.model_validate(body)
    except ValidationError as exc:
        raise ProtocolError(str(exc)) from exc


def decode_approval_decision(
    message: str,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> ApprovalDecision:
    """Decode a typed or one-cycle legacy ToolPlayer approval decision."""

    prefixes = (
        (f"{profile.approval_allow_chat_prefix}|", "approve"),
        (f"{profile.approval_deny_chat_prefix}|", "deny"),
    )
    for prefix, decision in prefixes:
        if not message.startswith(prefix):
            continue
        payload = message[len(prefix) :]
        try:
            value = json.loads(payload)
        except JSONDecodeError:
            value = payload
        return _decode_approval_payload(value, decision=decision)  # type: ignore[arg-type]
    raise ProtocolError("invalid approval decision prefix")


def decode_text_response_frame(value: str | dict[str, Any]) -> TextResponseChunk:
    """Decode and validate one text response frame, rejecting unknown roles."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except JSONDecodeError as exc:
            raise ProtocolError("invalid text response JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("text response frame must be a JSON object")
    if value.get("r") not in {"user", "assistant", "approval"}:
        raise ProtocolError(f"UNKNOWN_TEXT_ROLE: {value.get('r')!r}")
    try:
        return TextResponseChunk.model_validate(value)
    except ValidationError as exc:
        message = str(exc)
        if "role" in message:
            message = f"UNKNOWN_TEXT_ROLE: {message}"
        raise ProtocolError(message) from exc


def reassemble_text_response_chunks(chunks: list[TextResponseChunk]) -> TextResponseMessage:
    """Reassemble already validated text chunks with metadata consistency checks."""

    if not chunks:
        raise ProtocolError("text response chunks must not be empty")
    first = chunks[0]
    if any(
        chunk.response_id != first.response_id
        or chunk.total != first.total
        or chunk.player_name != first.player_name
        or chunk.role != first.role
        or chunk.conversation_id != first.conversation_id
        or chunk.title != first.title
        for chunk in chunks
    ):
        raise ProtocolError("text response metadata conflict")
    if first.total > MCBEWS_V1.response_max_chunks_per_message:
        raise ProtocolError("text response chunk count exceeds configured limit")
    by_index: dict[int, TextResponseChunk] = {}
    total_bytes = 0
    for chunk in chunks:
        previous = by_index.get(chunk.index)
        if previous is not None:
            if previous.content != chunk.content or previous.usage != chunk.usage:
                raise ProtocolError("text response duplicate metadata conflict")
            continue
        by_index[chunk.index] = chunk
        total_bytes += len(chunk.content.encode("utf-8"))
    if total_bytes > MCBEWS_V1.response_max_message_bytes:
        raise ProtocolError("text response message exceeds configured byte limit")
    expected = set(range(1, first.total + 1))
    if set(by_index) != expected:
        raise ProtocolError("text response chunks are incomplete")
    final = by_index[first.total]
    return TextResponseMessage(
        response_id=first.response_id,
        player_name=first.player_name,
        role=first.role,
        content="".join(by_index[index].content for index in range(1, first.total + 1)),
        conversation_id=first.conversation_id,
        title=first.title,
        usage=final.usage,
    )


def encode_text_response_commands(
    *,
    player_name: str,
    role: str,
    text: str,
    flow: FlowControlMiddleware,
    response_id: str | None = None,
    conversation_id: str | None = None,
    title: str | None = None,
    usage: dict[str, int] | None = None,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> list[str]:
    if not isinstance(player_name, str) or not player_name.strip():
        raise ProtocolError("text response player_name must not be empty")
    if response_id is not None and (not isinstance(response_id, str) or not response_id.strip()):
        raise ProtocolError("text response response_id must not be empty")
    if conversation_id is not None and (
        not isinstance(conversation_id, str) or not conversation_id.strip()
    ):
        raise ProtocolError("text response conversation_id must not be empty")
    message_id = response_id or f"resp-{uuid4().hex}"
    if role not in {"user", "assistant", "approval"}:
        raise ProtocolError(f"UNKNOWN_TEXT_ROLE: {role}")
    compact_usage: TokenUsage | None = None
    if usage is not None:
        try:
            compact_usage = TokenUsage.model_validate(usage)
        except ValidationError as exc:
            raise ProtocolError(f"invalid text response usage: {exc}") from exc

    def encode_frame(content: str, index: int, total: int) -> str:
        frame: dict[str, object] = {
            "id": message_id,
            "i": index,
            "n": total,
            "p": player_name,
            "r": role,
            "c": content,
        }
        # cid/title are stream metadata and therefore stay on every frame.
        if conversation_id is not None:
            frame["cid"] = conversation_id
        if title is not None:
            frame["t"] = title
        # Usage is a completion-only field.  It must not inflate every frame.
        if compact_usage is not None and index == total:
            frame["u"] = compact_usage.model_dump(by_alias=True)
        return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))

    return flow.chunk_framed_scriptevent(
        text,
        message_id=profile.text_response_script_event_id,
        encode_frame=encode_frame,
        emit_empty=True,
    )


def encode_approval_decision(
    decision: ApprovalDecision,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> str:
    """Encode a typed approval decision for the trusted ToolPlayer channel."""

    prefix = (
        profile.approval_allow_chat_prefix
        if decision.approved
        else profile.approval_deny_chat_prefix
    )
    if decision.legacy:
        return f"{prefix}|{decision.approval_id}"
    body = decision.model_dump(
        by_alias=True,
        exclude_none=True,
        exclude={"legacy", "decision"},
    )
    return f"{prefix}|{json.dumps(body, ensure_ascii=False, separators=(',', ':'))}"


# Historical spelling retained as a deprecated import alias for one cycle.
encode_text_resp_commands = encode_text_response_commands


__all__ = [
    "decode_approval_decision",
    "decode_bridge_chat_chunk",
    "decode_session_request_chat",
    "decode_session_response_message",
    "decode_text_response_frame",
    "decode_ui_chat_chunk",
    "encode_approval_decision",
    "encode_bridge_request",
    "encode_session_request_chat",
    "encode_session_response_command",
    "encode_session_response_json",
    "encode_text_response_commands",
    "encode_text_resp_commands",
    "reassemble_bridge_chunks",
    "reassemble_text_response_chunks",
    "reassemble_ui_chat_chunks",
]
