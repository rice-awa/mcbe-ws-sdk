"""Tests for CommandRegistry command/alias parsing and whole-word matching."""

from mcbe_ws_sdk.command import CommandRegistry, ParsedCommand


def _registry() -> CommandRegistry:
    cfg = {
        "#登录": {
            "type": "登录",
            "aliases": ["#登"],
            "description": "登录",
            "usage": "#登录 <密码>",
        }
    }
    return CommandRegistry(cfg)


def test_registered_prefix_resolves_type_and_content():
    reg = _registry()

    parsed = reg.resolve_parsed("#登录 123456")

    assert isinstance(parsed, ParsedCommand)
    assert parsed is not None
    assert parsed.type == "登录"
    assert parsed.content == "123456"
    assert parsed.prefix == "#登录"
    assert parsed.raw == "#登录 123456"
    assert parsed.matched_alias is None
    # the prefix command must actually be registered
    assert reg.get_command_config("#登录") is not None
    assert reg.get_command_prefix("登录") == "#登录"


def test_prefix_is_whole_word_run_on_does_not_match():
    reg = _registry()

    # must be whitespace (or exact) after the prefix
    assert reg.resolve_parsed("#登录xxx") is None
    assert reg.resolve_parsed("#登录xxx 123") is None
    # the bare prefix still matches (exact-token case)
    parsed = reg.resolve_parsed("#登录")
    assert parsed is not None
    assert parsed.content == ""


def test_alias_resolves_same_type():
    reg = _registry()

    parsed = reg.resolve_parsed("#登 123456")

    assert parsed is not None
    assert parsed.type == "登录"
    assert parsed.content == "123456"
    assert parsed.prefix == "#登录"
    assert parsed.matched_alias == "#登"


def test_unknown_prefix_returns_no_match():
    reg = _registry()

    assert reg.resolve_parsed("纯属闲聊") is None
    assert reg.resolve_parsed("#不存在 1") is None

    # resolve() falls back to (None, original message)
    assert reg.resolve("纯属闲聊") == (None, "纯属闲聊")
