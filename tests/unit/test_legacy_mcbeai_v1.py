from __future__ import annotations

import inspect
import json
from pathlib import Path
from uuid import UUID

import pytest

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.delivery import McbeOutboundDelivery
from mcbe_ws_sdk.errors import ConfigurationError
from mcbe_ws_sdk.flow import FlowControlMiddleware
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.codec import (
    encode_bridge_request,
    encode_legacy_response_commands,
)
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.delivery import LegacyMcbeAiV1Delivery
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import (
    LEGACY_MCBEAI_V1,
    LegacyMcbeAiV1Profile,
)


def test_legacy_profile_keeps_v1_wire_identifiers() -> None:
    assert LEGACY_MCBEAI_V1.bridge_request_message_id == "mcbeai:bridge_request"
    assert LEGACY_MCBEAI_V1.bridge_response_prefix == "MCBEAI|RESP"
    assert LEGACY_MCBEAI_V1.ui_chat_prefix == "MCBEAI|UI_CHAT"
    assert LEGACY_MCBEAI_V1.bridge_sender == "MCBEAI_TOOL"
    assert LEGACY_MCBEAI_V1.response_message_id == "mcbeai:ai_resp"


def test_legacy_profile_request_version_is_fixed_to_v2() -> None:
    with pytest.raises(ConfigurationError, match="legacy request_version must be 2"):
        LegacyMcbeAiV1Profile(request_version=1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("response_chunk_delay", -0.1),
        ("response_chunk_delay", True),
        ("response_chunk_delay", float("nan")),
        ("response_chunk_delay", float("inf")),
        ("response_chunk_delay", "slow"),
        ("response_prelude_delay", -0.1),
        ("response_prelude_delay", False),
        ("response_prelude_delay", float("nan")),
        ("response_prelude_delay", float("-inf")),
        ("response_prelude_delay", None),
    ],
)
def test_legacy_profile_rejects_invalid_delay_values(field: str, value: object) -> None:
    expected = f"legacy {field} must be a finite non-negative real number"
    with pytest.raises(ConfigurationError, match=expected):
        LegacyMcbeAiV1Profile(**{field: value})  # type: ignore[arg-type]


def test_generic_flow_chunks_custom_framed_event() -> None:
    flow = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=120))
    payloads = flow.chunk_framed_scriptevent(
        "hello" * 30,
        message_id="example:event",
        encode_frame=lambda content, index, total: json.dumps(
            {"i": index, "n": total, "c": content}, separators=(",", ":")
        ),
    )
    assert all("mcbeai" not in payload for payload in payloads)


def test_python_encoder_matches_shared_v2_vector() -> None:
    vectors = json.loads(Path("tests/fixtures/legacy_mcbeai_v1_vectors.json").read_text("utf-8"))
    vector = vectors["bridge_requests"][1]
    assert encode_bridge_request(
        request_id="r-1", capability="greet", payload={"name": "Steve"}
    ) == f"scriptevent mcbeai:bridge_request {vector['message']}"


def test_legacy_response_encoder_is_byte_safe_and_round_trips() -> None:
    flow = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=180))
    payloads = encode_legacy_response_commands(
        player_name="Alice中文",
        role="assistant",
        text="answer|😀" * 80,
        flow=flow,
        response_id="resp-1",
    )
    command_lines = [json.loads(payload)["body"]["commandLine"] for payload in payloads]
    assert all(len(line.encode("utf-8")) <= 180 for line in command_lines)
    frames = [json.loads(line.split(" ", 2)[2]) for line in command_lines]
    assert {frame["id"] for frame in frames} == {"resp-1"}
    assert {frame["p"] for frame in frames} == {"Alice中文"}
    assert {frame["r"] for frame in frames} == {"assistant"}
    assert "".join(frame["c"] for frame in sorted(frames, key=lambda item: item["i"])) == (
        "answer|😀" * 80
    )


@pytest.mark.asyncio
async def test_legacy_delivery_applies_profile_delays() -> None:
    sent: list[str] = []
    slept: list[float] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    async def sleep(delay: float) -> None:
        slept.append(delay)

    outbound = McbeOutboundDelivery(
        connection_id=UUID(int=81),
        send_payload=send_payload,
        settings=FlowControlSettings(command_line_byte_budget=180),
    )
    delivery = LegacyMcbeAiV1Delivery(outbound, sleeper=sleep)
    count = await delivery.send_response(
        player_name="Alice", role="assistant", text="x" * 800, response_id="resp-2"
    )
    assert count == len(sent)
    assert slept == [0.5]


def test_core_flow_has_no_legacy_delay_keys_or_dead_splitters() -> None:
    settings = FlowControlSettings()
    old_splitter = "_" + "split_text"
    old_chunker = "_" + "chunk_by_limits"
    assert "ai_resp" in settings.chunk_delays
    assert settings.chunk_delays["ai_resp"] == 0.15
    assert "ai_resp_prelude" not in settings.chunk_delays
    assert not hasattr(FlowControlMiddleware, old_splitter)
    assert not hasattr(FlowControlMiddleware, old_chunker)
    assert list(inspect.signature(FlowControlMiddleware.chunk_raw_command).parameters) == [
        "self",
        "command",
    ]
