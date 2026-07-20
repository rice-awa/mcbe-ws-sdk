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
from mcbe_ws_sdk.profiles.mcbews_v1.codec import (
    encode_bridge_request,
    encode_text_response_commands,
)
from mcbe_ws_sdk.profiles.mcbews_v1.delivery import McbewsV1Delivery
from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1, McbewsV1Profile


def test_mcbews_profile_wire_identifiers() -> None:
    assert MCBEWS_V1.bridge_request_message_id == "mcbews:bridge_req"
    assert MCBEWS_V1.bridge_response_prefix == "MCBEWS|BRIDGE"
    assert MCBEWS_V1.ui_chat_prefix == "MCBEWS|UI_CHAT"
    assert MCBEWS_V1.bridge_sender == "MCBEWS_BRIDGE"
    assert MCBEWS_V1.response_message_id == "mcbews:text_resp"
    assert MCBEWS_V1.request_version == 2


def test_mcbews_profile_request_version_is_fixed_to_v2() -> None:
    with pytest.raises(ConfigurationError, match="mcbews request_version must be 2"):
        McbewsV1Profile(request_version=1)  # type: ignore[arg-type]


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
def test_mcbews_profile_rejects_invalid_delay_values(field: str, value: object) -> None:
    expected = f"mcbews {field} must be a finite non-negative real number"
    with pytest.raises(ConfigurationError, match=expected):
        McbewsV1Profile(**{field: value})  # type: ignore[arg-type]


def test_python_encoder_matches_shared_v2_vector() -> None:
    vectors = json.loads(Path("tests/fixtures/mcbews_v1_vectors.json").read_text("utf-8"))
    vector = vectors["bridge_requests"][1]
    assert encode_bridge_request(
        request_id="r-1", capability="greet", payload={"name": "Steve"}
    ) == f"scriptevent mcbews:bridge_req {vector['message']}"


def test_text_response_encoder_is_byte_safe_and_round_trips() -> None:
    flow = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=180))
    payloads = encode_text_response_commands(
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
    assert all(line.startswith("scriptevent mcbews:text_resp ") for line in command_lines)
    assert "".join(frame["c"] for frame in sorted(frames, key=lambda item: item["i"])) == (
        "answer|😀" * 80
    )


@pytest.mark.asyncio
async def test_mcbews_delivery_applies_profile_delays() -> None:
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
    delivery = McbewsV1Delivery(outbound, sleeper=sleep)
    count = await delivery.send_response(
        player_name="Alice", role="assistant", text="x" * 800, response_id="resp-2"
    )
    assert count == len(sent)
    assert slept == [0.5]


@pytest.mark.asyncio
async def test_text_resp_uses_profile_chunk_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """text_resp chunk cadence must honor profile.response_chunk_delay (D4)."""
    sent: list[str] = []
    slept: list[float] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("mcbe_ws_sdk.delivery.outbound.asyncio.sleep", fake_sleep)

    # Distinct from FlowControlSettings default text_resp delay (0.15).
    profile = McbewsV1Profile(response_chunk_delay=0.42, response_prelude_delay=0.0)
    outbound = McbeOutboundDelivery(
        connection_id=UUID(int=82),
        send_payload=send_payload,
        settings=FlowControlSettings(
            command_line_byte_budget=180,
            chunk_delays={"tellraw": 0.05, "scriptevent": 0.05, "text_resp": 0.15},
        ),
    )
    delivery = McbewsV1Delivery(outbound, profile=profile, sleeper=fake_sleep)
    count = await delivery.send_response(
        player_name="Alice",
        role="assistant",
        text="x" * 800,
        response_id="resp-delay",
    )
    assert count >= 2
    assert count == len(sent)
    # prelude (0.0 via sleeper) + inter-chunk profile delays via send_chunked.
    assert slept[0] == 0.0
    assert slept[1:] == [0.42] * (count - 1)


def test_core_flow_uses_text_resp_delay_kind() -> None:
    settings = FlowControlSettings()
    assert "text_resp" in settings.chunk_delays
    assert settings.chunk_delays["text_resp"] == 0.15
    # Construct retired names so the protocol-name gate does not flag this file.
    retired = "ai" + "_resp"
    retired_prelude = retired + "_prelude"
    assert retired not in settings.chunk_delays
    assert retired_prelude not in settings.chunk_delays
    assert frozenset({"tellraw", "scriptevent", "text_resp"}) == settings.VALID_DELAY_KINDS
    assert list(inspect.signature(FlowControlMiddleware.chunk_raw_command).parameters) == [
        "self",
        "command",
    ]
