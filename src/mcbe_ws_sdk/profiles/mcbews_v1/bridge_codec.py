from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from mcbe_ws_sdk.errors import ProtocolError
from mcbe_ws_sdk.profiles.mcbews_v1.bridge_models import (
    AddonBridgeChunk,
    AddonBridgeRequest,
    AddonBridgeResponse,
)
from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1, McbewsV1Profile


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
