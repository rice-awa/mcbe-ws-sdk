"""MCBE WebSocket protocol handler.

Relocated from the main repo ``services/websocket/minecraft.py``. Responsible
for constructing subscribe messages, parsing inbound ``PlayerMessageEvent``,
resolving typed commands, and rendering the small set of on-screen status
messages the connection lifecycle needs (info / success / error).

Deliberately *host-config-agnostic*: presentation strings (welcome template,
status prefixes, colors) come in through a frozen
:class:`MessageSurfaceConfig` value object instead of the host's
``MinecraftConfig``. Command resolution is delegated to an injected
:class:`~mcbe_ws_sdk.command.registry.CommandRegistry`.

``create_chat_request`` was deliberately removed — it constructs the host's
``models.messages.ChatRequest`` and reads per-player provider/template state,
which is now the host's concern (built inside its ``ConnectionHook`` /
command dispatcher). The renderer methods like
:meth:`create_info_message` remain because the host still needs to surface
status lines; the host's ``HostSink`` delivers the resulting
:class:`TellrawMessage` over the outbound delivery adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from mcbe_ws_sdk._logging import get_logger
from mcbe_ws_sdk.command import CommandRegistry, ParsedCommand
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftSubscribe,
    PlayerMessageEvent,
)

logger = get_logger(__name__)


Level = Literal["info", "error", "success"]


@dataclass(frozen=True)
class TellrawMessage:
    """A structured outbound tellraw line.

    Carries the plain text, the MC color, and the tellraw target so the
    delivery layer can chunk + serialize without re-parsing its own output.
    """

    text: str
    color: str
    target: str = "@a"


@dataclass(frozen=True)
class MessageSurfaceConfig:
    """Presentation strings the protocol handler renders status messages with.

    A frozen value object so a host can supply any wording/colors via
    config without the handler importing the host's settings model.
    """

    welcome_message_template: str = (
        "-----------\n"
        "已连接到 MCBE WebSocket 网关\n"
        "连接 ID: {connection_id}...\n"
        "-----------"
    )
    error_prefix: str = "❌ 错误: "
    info_prefix: str = "ℹ "
    success_prefix: str = "✅ "
    error_color: str = "§c"
    info_color: str = "§b"
    success_color: str = "§a"


class MinecraftProtocolHandler:
    """Constructs / parses MCBE connection-lifecycle protocol messages.

    ``__init__`` takes the collaborators it needs (a command registry and a
    presentation surface) and never imports anything host-specific.
    """

    def __init__(
        self,
        command_registry: CommandRegistry,
        surface: MessageSurfaceConfig | None = None,
    ) -> None:
        self.command_registry = command_registry
        self.surface = surface or MessageSurfaceConfig()

    @staticmethod
    def create_subscribe_message() -> str:
        """Build the ``subscribe`` payload string for PlayerMessage events."""
        subscribe = MinecraftSubscribe.player_message()
        return subscribe.model_dump_json(exclude_none=True)

    def create_welcome_message(
        self,
        *,
        connection_id: str,
    ) -> str:
        """Render the post-connect welcome banner (plain text; wrap a message)."""
        return self.surface.welcome_message_template.format(
            connection_id=connection_id[:8],
        )

    @staticmethod
    def parse_player_message(data: dict[str, Any]) -> PlayerMessageEvent | None:
        """Parse an inbound ``PlayerMessage`` event out of a WS frame body."""
        try:
            header = data.get("header", {})
            event_name = header.get("eventName")
            if event_name != "PlayerMessage":
                return None
            body = data.get("body", {})
            return PlayerMessageEvent.from_event_body(body)
        except Exception:
            logger.warning(
                "parse_player_message_error",
                error_type="protocol_parse_failed",
            )
            return None

    def parse_command(self, message: str) -> tuple[str | None, str]:
        """Resolve ``message`` to ``(command_type, content)`` via the registry."""
        return self.command_registry.resolve(message)

    def parse_typed_command(self, message: str) -> ParsedCommand | None:
        """Resolve ``message`` to a typed :class:`ParsedCommand`, or ``None``."""
        return self.command_registry.resolve_parsed(message)

    def get_help_text(self) -> str:
        """Render the in-game help listing from the command registry."""
        lines = ["可用命令:"]
        for prefix, _cmd_type, _aliases in self.command_registry.list_all_commands():
            cmd_config = self.command_registry.get_command_config(prefix)
            if cmd_config is None:
                continue
            desc = cmd_config.description
            usage = cmd_config.usage
            if usage:
                lines.append(f"• {prefix} {usage} - {desc}")
            else:
                lines.append(f"• {prefix} - {desc}")
        return "\n".join(lines)

    def _render(
        self,
        text: str,
        *,
        level: Level,
        target: str = "@a",
    ) -> TellrawMessage:
        if level == "error":
            return TellrawMessage(
                text=f"{self.surface.error_prefix}{text}",
                color=self.surface.error_color,
                target=target,
            )
        if level == "success":
            return TellrawMessage(
                text=f"{self.surface.success_prefix}{text}",
                color=self.surface.success_color,
                target=target,
            )
        return TellrawMessage(
            text=f"{self.surface.info_prefix}{text}",
            color=self.surface.info_color,
            target=target,
        )

    def create_error_message(self, error: str, target: str = "@a") -> TellrawMessage:
        return self._render(error, level="error", target=target)

    def create_info_message(self, info: str, target: str = "@a") -> TellrawMessage:
        return self._render(info, level="info", target=target)

    def create_success_message(self, message: str, target: str = "@a") -> TellrawMessage:
        return self._render(message, level="success", target=target)
