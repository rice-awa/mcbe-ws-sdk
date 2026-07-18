"""Command registry + parsed-command value object, relocated from the host app.

The registry turns a ``{prefix: type | config}`` mapping into a matcher that
resolves an inbound message to a typed command. Matching is *whole-word*: a
prefix/alias matches only when it is the entire message or is followed by
whitespace, so ``#登录xxx`` does NOT match the ``#登录`` prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


#: Canonical command table the facade loads into its default
#: :class:`CommandRegistry`. Whole-word prefixes (the registry matches a prefix
#: only when it is the entire message or followed by whitespace).
#:
#: This is intentionally the *neutral, transport-grade* command set — connection
#: handshake (``#登录``, hidden from help) and the two gateway utilities the host
#: drives via its :class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook`
#: (``运行命令`` to run a minecraft command, ``帮助`` for help). AI-specific
#: commands (chat / script / context / model-switch) live in the *host*
#: application's own registry, not the SDK default.
DEFAULT_COMMANDS: dict[str, str | dict[str, Any]] = {
    "#登录": {"type": "login", "description": "登录"},
    "运行命令": {
        "type": "run_command",
        "aliases": [],
        "description": "执行 Minecraft 命令",
        "usage": "<命令>",
    },
    "帮助": {
        "type": "help",
        "aliases": ["?"],
        "description": "显示帮助",
        "usage": None,
    },
}


@dataclass(frozen=True)
class MinecraftCommandConfig:
    """A loaded command definition (prefix, type, aliases, description, usage)."""

    prefix: str
    type: str
    description: str
    aliases: list[str] = field(default_factory=list)
    usage: str | None = None


@dataclass(frozen=True)
class ParsedCommand:
    type: str
    content: str
    prefix: str
    raw: str
    matched_alias: str | None = None


class CommandRegistry:
    """命令注册表 - 管理命令和别名"""

    def __init__(self, commands_config: dict[str, str | dict[str, Any]]) -> None:
        self._commands: dict[str, MinecraftCommandConfig] = {}
        self._alias_map: dict[str, str] = {}  # 别名 -> 主命令前缀
        self._type_to_prefix: dict[str, str] = {}  # 命令类型 -> 主命令前缀
        self._load_commands(commands_config)

    def _load_commands(self, config: dict[str, str | dict[str, Any]]) -> None:
        """加载命令配置并构建别名映射"""
        for prefix, cmd in config.items():
            if isinstance(cmd, str):
                # 兼容旧格式: {prefix: type}
                cmd_config = MinecraftCommandConfig(
                    prefix=prefix,
                    type=cmd,
                    aliases=[],
                    description="",
                    usage=None,
                )
            elif isinstance(cmd, dict):
                # 新格式: {prefix: {type, aliases, description, usage}}
                cmd_config = MinecraftCommandConfig(
                    prefix=prefix,
                    type=cmd.get("type", ""),
                    aliases=list(cmd.get("aliases", [])),
                    description=cmd.get("description", ""),
                    usage=cmd.get("usage"),
                )
            else:
                continue

            self._commands[prefix] = cmd_config
            self._type_to_prefix[cmd_config.type] = prefix

            # 构建别名映射
            for alias in cmd_config.aliases:
                self._alias_map[alias] = prefix

        logger.info(
            "command_registry_loaded",
            command_count=len(self._commands),
            alias_count=len(self._alias_map),
        )

    def resolve(self, message: str) -> tuple[str | None, str]:
        """解析消息，返回 (命令类型, 内容)"""
        parsed = self.resolve_parsed(message)
        if parsed is None:
            return None, message
        return parsed.type, parsed.content

    @staticmethod
    def _matches_token(message: str, token: str) -> bool:
        if message == token:
            return True
        if not message.startswith(token):
            return False
        return len(message) > len(token) and message[len(token)].isspace()

    def resolve_parsed(self, message: str) -> ParsedCommand | None:
        """解析消息，返回带匹配来源的 typed command。"""
        for prefix, cmd_config in self._commands.items():
            if self._matches_token(message, prefix):
                content = message[len(prefix):].strip()
                return ParsedCommand(
                    type=cmd_config.type,
                    content=content,
                    prefix=prefix,
                    raw=message,
                )

        for alias, main_prefix in self._alias_map.items():
            if self._matches_token(message, alias):
                content = message[len(alias):].strip()
                cmd_config = self._commands[main_prefix]
                return ParsedCommand(
                    type=cmd_config.type,
                    content=content,
                    prefix=main_prefix,
                    raw=message,
                    matched_alias=alias,
                )

        return None

    def add_alias(self, command_prefix: str, alias: str) -> bool:
        """动态添加别名"""
        if command_prefix not in self._commands:
            logger.warning("add_alias_command_not_found", prefix=command_prefix)
            return False

        if alias in self._alias_map:
            logger.warning("add_alias_already_exists", alias=alias)
            return False

        self._alias_map[alias] = command_prefix
        self._commands[command_prefix].aliases.append(alias)

        logger.info("alias_added", prefix=command_prefix, alias=alias)
        return True

    def remove_alias(self, alias: str) -> bool:
        """动态删除别名"""
        if alias not in self._alias_map:
            logger.warning("remove_alias_not_found", alias=alias)
            return False

        main_prefix = self._alias_map.pop(alias)
        self._commands[main_prefix].aliases.remove(alias)

        logger.info("alias_removed", prefix=main_prefix, alias=alias)
        return True

    def get_command_config(self, prefix: str) -> MinecraftCommandConfig | None:
        """获取命令配置"""
        return self._commands.get(prefix)

    def get_aliases(self, command_prefix: str) -> list[str]:
        """获取命令的所有别名"""
        cmd = self._commands.get(command_prefix)
        return list(cmd.aliases) if cmd else []

    def list_all_commands(self) -> list[tuple[str, str, list[str]]]:
        """列出所有命令 (前缀, 类型, 别名列表)"""
        return [
            (prefix, config.type, list(config.aliases))
            for prefix, config in self._commands.items()
        ]

    def get_command_prefix(self, cmd_type: str) -> str | None:
        """根据命令类型获取主命令前缀"""
        return self._type_to_prefix.get(cmd_type)
