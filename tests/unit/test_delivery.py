"""Tests for McbeOutboundDelivery (Task A5)."""

import json
from uuid import UUID

import pytest
import structlog

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.delivery import McbeOutboundDelivery


@pytest.mark.asyncio
async def test_send_tellraw_long_text_chunked_within_byte_budget():
    """Long tellraw must be chunked by FlowControlMiddleware; each raw commandLine <= 461 B."""
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    delivery = McbeOutboundDelivery(
        connection_id=UUID("12345678-1234-5678-1234-567812345678"),
        send_payload=send_payload,
        settings=FlowControlSettings(),
    )

    long_text = ("这是一个很长的句子用来测试分片机制是否正常工作。" * 15) + ("Short one. " * 40)
    count = await delivery.send_tellraw(long_text, color="§a", source="test")

    assert count >= 2
    assert len(sent) == count
    for payload in sent:
        data = json.loads(payload)
        assert data["header"]["messagePurpose"] == "commandRequest"
        cmd_line = data["body"]["commandLine"]
        assert cmd_line.startswith("tellraw ")
        assert len(cmd_line.encode("utf-8")) <= 461
        assert data["body"]["origin"]["type"] == "say"


@pytest.mark.asyncio
async def test_send_tellraw_short_text_single_payload_raw_commandline():
    """Short tellraw sends exactly one raw commandLine via send_payload."""
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    delivery = McbeOutboundDelivery(
        connection_id=UUID("12345678-1234-5678-1234-567812345678"),
        send_payload=send_payload,
        settings=FlowControlSettings(),
    )

    count = await delivery.send_tellraw("Hi", color="§b", source="test")
    assert count == 1
    assert len(sent) == 1
    data = json.loads(sent[0])
    assert data["body"]["commandLine"].startswith("tellraw ")


async def _send_noop(payload: str) -> None:
    pass


@pytest.mark.asyncio
async def test_info_delivery_log_omits_raw_text_and_command() -> None:
    """Info log contains metadata only; no player text or command line."""
    secret = "private-player-text"
    delivery = McbeOutboundDelivery(
        connection_id=UUID(int=71),
        send_payload=_send_noop,
        settings=FlowControlSettings(),
        log_raw_payloads=False,
    )
    with structlog.testing.capture_logs() as logs:
        await delivery.send_tellraw(secret, color="", source="test", target="Alice")
    info = next(item for item in logs if item["event"] == "websocket_response_sent")
    assert info["request_id"]
    assert info["command_type"] == "tellraw"
    assert info["command_line_length"] > 0
    assert info["command_line_bytes"] >= info["command_line_length"]
    assert all(secret not in str(item) for item in logs)
    assert all("tellraw Alice" not in str(item) for item in logs)


@pytest.mark.asyncio
async def test_payload_debug_log_respects_log_raw_payloads_flag() -> None:
    """Debug payload log is absent by default, present only when opt-in."""
    delivery_default = McbeOutboundDelivery(
        connection_id=UUID(int=72),
        send_payload=_send_noop,
        settings=FlowControlSettings(),
    )
    delivery_opted_in = McbeOutboundDelivery(
        connection_id=UUID(int=73),
        send_payload=_send_noop,
        settings=FlowControlSettings(),
        log_raw_payloads=True,
    )

    with structlog.testing.capture_logs() as logs:
        await delivery_default.send_tellraw("default", color="", source="test", target="Steve")
    assert not any("websocket_response_payload" in str(item) for item in logs)

    with structlog.testing.capture_logs() as logs:
        await delivery_opted_in.send_tellraw("secret", color="", source="test", target="Steve")
    assert any("websocket_response_payload" in str(item) for item in logs)
