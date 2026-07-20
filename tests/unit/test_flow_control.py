import inspect

import pytest

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.errors import FrameTooLargeError
from mcbe_ws_sdk.flow import FlowControlMiddleware


def test_raw_command_over_budget_raises():
    mid = FlowControlMiddleware(FlowControlSettings())
    long_cmd = "say " + "x" * 1000
    with pytest.raises(FrameTooLargeError):
        mid.chunk_raw_command(long_cmd)


def test_raw_command_over_custom_budget_raises_frame_too_large_error():
    middleware = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=16))
    with pytest.raises(FrameTooLargeError):
        middleware.chunk_raw_command("say this command is too long")


def test_chunk_delay_for_unknown_kind_defaults_to_zero() -> None:
    mid = FlowControlMiddleware(FlowControlSettings())
    assert mid.chunk_delay_for("unknown") == 0.0


def test_chunk_raw_command_signature_stays_generic() -> None:
    assert list(inspect.signature(FlowControlMiddleware.chunk_raw_command).parameters) == [
        "self",
        "command",
    ]


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


def test_tellraw_wrapper_overflow_is_frame_too_large():
    mid = FlowControlMiddleware(FlowControlSettings())
    with pytest.raises(FrameTooLargeError):
        mid.chunk_tellraw("hi", target="名" * 200)
