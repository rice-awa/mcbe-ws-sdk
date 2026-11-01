import inspect
import json

import pytest

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.errors import FrameTooLargeError
from mcbe_ws_sdk.flow import FlowControlMiddleware


def _tellraw_text(payload: str) -> str:
    command = json.loads(payload)["body"]["commandLine"]
    rawtext = json.loads(command.split(" ", 2)[2])["rawtext"]
    return rawtext[0]["text"]


def _scriptevent_content(payload: str, message_id: str = "server:data") -> str:
    command = json.loads(payload)["body"]["commandLine"]
    return command[len(f"scriptevent {message_id} ") :]


def _command_line(payload: str) -> str:
    return json.loads(payload)["body"]["commandLine"]


def test_raw_command_over_budget_raises():
    mid = FlowControlMiddleware(FlowControlSettings())
    long_cmd = "say " + "x" * 1000
    with pytest.raises(FrameTooLargeError):
        mid.chunk_raw_command(long_cmd)


def test_raw_command_over_custom_budget_raises_frame_too_large_error():
    middleware = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=16))
    with pytest.raises(FrameTooLargeError):
        middleware.chunk_raw_command("say this command is too long")


@pytest.mark.parametrize("size", [460, 461])
def test_raw_command_at_the_byte_budget_is_accepted(size: int) -> None:
    command = "say " + "x" * (size - 4)
    assert len(command.encode("utf-8")) == size
    payloads = FlowControlMiddleware(FlowControlSettings()).chunk_raw_command(command)
    assert len(payloads) == 1
    assert _command_line(payloads[0]) == command


def test_raw_command_one_byte_over_budget_is_rejected() -> None:
    command = "say " + "x" * (462 - 4)
    assert len(command.encode("utf-8")) == 462
    with pytest.raises(FrameTooLargeError, match="462 > 461"):
        FlowControlMiddleware(FlowControlSettings()).chunk_raw_command(command)


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
    assert len(_command_line(payloads[0]).encode("utf-8")) <= 461


def test_tellraw_long_text_chunks_within_budget():
    mid = FlowControlMiddleware(FlowControlSettings())
    long_text = ("这是一个很长的句子用来测试分片。" * 20) + ("Short sentence. " * 50)
    payloads = mid.chunk_tellraw(long_text)
    assert len(payloads) >= 2
    for payload in payloads:
        assert len(_command_line(payload).encode("utf-8")) <= 461


def test_tellraw_cjk_and_emoji_reassemble_without_loss() -> None:
    mid = FlowControlMiddleware(FlowControlSettings())
    text = "玩家😀正在测试分片。\n" * 40
    payloads = mid.chunk_tellraw(text)
    assert len(payloads) >= 2
    for payload in payloads:
        assert len(_command_line(payload).encode("utf-8")) <= 461
    color = "§a"
    rebuilt = "".join(_tellraw_text(payload)[len(color) :] for payload in payloads)
    assert rebuilt == text


def test_tellraw_long_target_still_chunks_and_preserves_target() -> None:
    mid = FlowControlMiddleware(FlowControlSettings())
    target = "A" * 40
    text = "玩家😀" * 300
    payloads = mid.chunk_tellraw(text, target=target)
    assert len(payloads) >= 2
    for payload in payloads:
        command = _command_line(payload)
        assert command.startswith(f"tellraw {target} ")
        assert len(command.encode("utf-8")) <= 461
    color = "§a"
    rebuilt = "".join(_tellraw_text(payload)[len(color) :] for payload in payloads)
    assert rebuilt == text


def test_tellraw_wrapper_overflow_is_frame_too_large():
    mid = FlowControlMiddleware(FlowControlSettings())
    with pytest.raises(FrameTooLargeError):
        mid.chunk_tellraw("hi", target="名" * 200)


def test_help_lines_keep_whole_command_tokens_at_boundaries() -> None:
    settings = FlowControlSettings(
        chunk_sentence_mode=True,
        max_chunk_content_length=20,
    )
    mid = FlowControlMiddleware(settings)
    help_text = (
        "AGENT 聊天 <消息>\n"
        "AGENT 脚本 <消息>\n"
        "AGENT 保存\n"
        "!stream 2\n"
        "!stream-cancel 2\n"
    )
    payloads = mid.chunk_scriptevent(help_text)
    assert len(payloads) >= 2
    contents = [_scriptevent_content(payload) for payload in payloads]
    assert "".join(contents) == help_text
    for token in ("AGENT 聊天", "AGENT 脚本", "AGENT 保存", "!stream 2", "!stream-cancel 2"):
        assert any(token in content for content in contents)


def test_bang_command_token_is_never_split_at_a_chunk_boundary() -> None:
    mid = FlowControlMiddleware(
        FlowControlSettings(chunk_sentence_mode=True, max_chunk_content_length=1)
    )
    with pytest.raises(FrameTooLargeError):
        mid.chunk_scriptevent("!stream 2")
