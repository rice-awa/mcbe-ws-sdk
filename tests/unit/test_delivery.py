"""Tests for McbeOutboundDelivery (Task A5)."""

import json
from uuid import UUID

import pytest

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.delivery import McbeOutboundDelivery


@pytest.mark.asyncio
async def test_send_tellraw_long_text_chunked_within_byte_budget():
    """Long tellraw must be chunked by FlowControlMiddleware; each raw commandLine ≤ 461 B."""
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
