"""Tests for the relocated MCBE protocol handler (handler.py)."""

from __future__ import annotations

import pytest

from mcbe_ws_sdk.command import CommandRegistry
from mcbe_ws_sdk.gateway.handler import (
    MessageSurfaceConfig,
    MinecraftProtocolHandler,
    TellrawMessage,
)


def _registry() -> CommandRegistry:
    return CommandRegistry(
        {
            "#登录": "login",
            "运行命令": {
                "type": "run_command",
                "aliases": [],
                "description": "执行 Minecraft 命令",
                "usage": "<命令>",
            },
            "帮助": {"type": "help", "aliases": ["?"], "description": "显示帮助", "usage": None},
        }
    )


def test_create_subscribe_message_is_player_message() -> None:
    payload = MinecraftProtocolHandler.create_subscribe_message()
    assert '"messagePurpose":"subscribe"' in payload.replace(" ", "")
    assert '"eventName":"PlayerMessage"' in payload.replace(" ", "")


def test_parse_player_message_happy_path() -> None:
    data = {
        "header": {"requestId": "x", "messagePurpose": "event", "eventName": "PlayerMessage"},
        "body": {"sender": "Steve", "message": "hi", "type": "chat"},
    }
    event = MinecraftProtocolHandler.parse_player_message(data)
    assert event is not None
    assert event.sender == "Steve"
    assert event.message == "hi"


def test_parse_player_message_ignores_non_player_message() -> None:
    data = {"header": {"eventName": "CommandResponse"}, "body": {}}
    assert MinecraftProtocolHandler.parse_player_message(data) is None


def test_create_welcome_message_no_longer_references_help() -> None:
    handler = MinecraftProtocolHandler(_registry())
    welcome = handler.create_welcome_message(
        connection_id="abc12345",
    )
    assert "abc1234" in welcome  # truncated id
    assert "帮助" not in welcome  # help command reference removed from template


def test_parse_typed_command_whole_word_matching() -> None:
    handler = MinecraftProtocolHandler(_registry())
    # Prefix alone resolves; prefix without trailing space on a longer message
    # must NOT resolve (whole-word rule is the registry's job).
    assert handler.parse_typed_command("运行命令 help") is not None
    assert handler.parse_typed_command("#登录密码123") is None  # no following space


def test_get_help_text_lists_all_commands_no_hiding() -> None:
    handler = MinecraftProtocolHandler(_registry())
    help_text = handler.get_help_text()
    assert "显示帮助" in help_text
    assert "执行 Minecraft 命令" in help_text
    # login commands are no longer hidden (login hiding removed)
    assert "#登录" in help_text


def test_message_renderers_use_surface_prefix_and_color() -> None:
    handler = MinecraftProtocolHandler(_registry())
    assert handler.create_error_message("boom") == TellrawMessage(
        text="❌ 错误: boom", color="§c"
    )
    assert handler.create_success_message("ok") == TellrawMessage(text="✅ ok", color="§a")
    assert handler.create_info_message("hi") == TellrawMessage(text="ℹ hi", color="§b")


def test_surface_is_frozen() -> None:
    with pytest.raises(AttributeError):
        MessageSurfaceConfig().error_color = "§0"  # type: ignore[misc]
