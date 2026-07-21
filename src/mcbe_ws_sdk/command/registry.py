"""Command registry + parsed-command value object, relocated from the host app.

The registry turns a ``{prefix: type | config}`` mapping into a matcher that
resolves an inbound message to a typed command. Matching is *whole-word*: a
prefix/alias matches only when it is the entire message or is followed by
whitespace, so ``#prefix_xyz`` does NOT match the ``#prefix`` token.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MinecraftCommandConfig:
    """A loaded command definition (prefix, type, aliases, description, usage)."""

    prefix: str
    type: str
    description: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    usage: str | None = None


@dataclass(frozen=True)
class ParsedCommand:
    type: str
    content: str
    prefix: str
    raw: str
    matched_alias: str | None = None


class CommandRegistry:
    """Registry of command prefixes, types, and aliases."""

    def __init__(
        self,
        commands_config: Mapping[str, str | Mapping[str, Any]] | None = None,
    ) -> None:
        self._commands: dict[str, MinecraftCommandConfig] = {}
        self._alias_map: dict[str, str] = {}  # alias -> primary prefix
        self._type_to_prefix: dict[str, str] = {}  # command type -> primary prefix
        self._load_commands(commands_config or {})

    def _load_commands(self, config: Mapping[str, str | Mapping[str, Any]]) -> None:
        """Load command config and build the alias map."""
        for prefix, cmd in config.items():
            if isinstance(cmd, str):
                # Legacy format: {prefix: type}
                cmd_config = MinecraftCommandConfig(
                    prefix=prefix,
                    type=cmd,
                    aliases=(),
                    description="",
                    usage=None,
                )
            elif isinstance(cmd, dict):
                # Full format: {prefix: {type, aliases, description, usage}}
                cmd_config = MinecraftCommandConfig(
                    prefix=prefix,
                    type=cmd.get("type", ""),
                    aliases=tuple(cmd.get("aliases", [])),
                    description=cmd.get("description", ""),
                    usage=cmd.get("usage"),
                )
            else:
                continue

            self._commands[prefix] = cmd_config
            self._type_to_prefix[cmd_config.type] = prefix

            for alias in cmd_config.aliases:
                self._alias_map[alias] = prefix

        logger.debug(
            "command_registry_loaded",
            command_count=len(self._commands),
            alias_count=len(self._alias_map),
        )

    def resolve(self, message: str) -> tuple[str | None, str]:
        """Resolve a message to ``(command_type, content)``."""
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
        """Resolve a message to a typed command, including which token matched."""
        for prefix, cmd_config in self._commands.items():
            if self._matches_token(message, prefix):
                content = message[len(prefix) :].strip()
                return ParsedCommand(
                    type=cmd_config.type,
                    content=content,
                    prefix=prefix,
                    raw=message,
                )

        for alias, main_prefix in self._alias_map.items():
            if self._matches_token(message, alias):
                content = message[len(alias) :].strip()
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
        """Register a dynamic alias for an existing command prefix."""
        if command_prefix not in self._commands:
            logger.warning("add_alias_command_not_found", prefix=command_prefix)
            return False

        if alias in self._alias_map:
            logger.warning("add_alias_already_exists", alias=alias)
            return False

        self._alias_map[alias] = command_prefix
        old = self._commands[command_prefix]
        self._commands[command_prefix] = replace(old, aliases=old.aliases + (alias,))

        logger.info("alias_added", prefix=command_prefix, alias=alias)
        return True

    def remove_alias(self, alias: str) -> bool:
        """Remove a previously registered alias."""
        if alias not in self._alias_map:
            logger.warning("remove_alias_not_found", alias=alias)
            return False

        main_prefix = self._alias_map.pop(alias)
        old = self._commands[main_prefix]
        self._commands[main_prefix] = replace(
            old, aliases=tuple(a for a in old.aliases if a != alias)
        )

        logger.info("alias_removed", prefix=main_prefix, alias=alias)
        return True

    def get_command_config(self, prefix: str) -> MinecraftCommandConfig | None:
        """Return the loaded config for a primary command prefix."""
        return self._commands.get(prefix)

    def get_aliases(self, command_prefix: str) -> tuple[str, ...]:
        """Return all aliases for a command prefix."""
        cmd = self._commands.get(command_prefix)
        return cmd.aliases if cmd else ()

    def list_all_commands(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """List all commands as ``(prefix, type, aliases)`` tuples."""
        return [(prefix, config.type, config.aliases) for prefix, config in self._commands.items()]

    def get_command_prefix(self, cmd_type: str) -> str | None:
        """Return the primary prefix for a command type, if registered."""
        return self._type_to_prefix.get(cmd_type)
