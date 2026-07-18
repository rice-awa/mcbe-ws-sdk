import pytest

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.flow import FlowControlMiddleware


def test_raw_command_over_budget_raises():
    mid = FlowControlMiddleware(FlowControlSettings())
    long_cmd = "say " + "x" * 1000
    with pytest.raises(ValueError):
        mid.chunk_raw_command(long_cmd)


def test_chunk_delay_for_default_ai_resp():
    mid = FlowControlMiddleware(FlowControlSettings())
    assert mid.chunk_delay_for("ai_resp") == 0.15


def test_tellraw_short_text_single_chunk_under_budget():
    mid = FlowControlMiddleware(FlowControlSettings())
    payloads = mid.chunk_tellraw("Hello world")
    assert len(payloads) == 1
    import json
    data = json.loads(payloads[0])
    assert len(data["body"]["commandLine"].encode("utf-8")) <= 461


def test_tellraw_long_text_chunks_within_budget():
    mid = FlowControlMiddleware(FlowControlSettings())
    long_text = ("这是一个很长的句子用来测试分片。" * 20) + ("Short sentence. " * 50)
    payloads = mid.chunk_tellraw(long_text)
    assert len(payloads) >= 2
    for payload in payloads:
        import json
        data = json.loads(payload)
        assert len(data["body"]["commandLine"].encode("utf-8")) <= 461
