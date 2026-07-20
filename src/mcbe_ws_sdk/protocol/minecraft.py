"""Minecraft 协议消息模型"""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Selectors stay unquoted: @a, @p[r=3], @e[type=player,c=1], ...
_TELLRAW_SELECTOR_RE = re.compile(r"^@[a-z](?:\[[A-Za-z0-9_.,=!:-]*\])?$")
# Characters that force a player name to be double-quoted on the command line.
# Anything else — including non-ASCII names like "玩家" — stays unquoted.
# Quoting Chinese / multi-byte names makes Bedrock report
# "没有与选择器匹配的目标" even when the player is online.
_TELLRAW_TARGET_QUOTE_CHARS = frozenset(' \t"\\')
_SCRIPT_EVENT_MESSAGE_ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


def sanitize_tellraw_target(target: str) -> str:
    """Return a command-safe tellraw target without allowing command injection.

    Player names are left unquoted whenever they contain no whitespace, quotes,
    or backslashes. This is required for non-ASCII Bedrock names (e.g. ``玩家``):
    wrapping them in double quotes makes the selector fail to match.
    """
    target = target.strip() or "@a"
    if any(ord(char) < 0x20 or char == "\x7f" for char in target):
        raise ValueError("tellraw target contains control characters")
    if _TELLRAW_SELECTOR_RE.fullmatch(target):
        return target
    if not any(char in _TELLRAW_TARGET_QUOTE_CHARS for char in target):
        return target
    escaped = target.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def validate_scriptevent_message_id(message_id: str) -> str:
    """Validate a scriptevent message id before embedding it in commandLine."""
    if not _SCRIPT_EVENT_MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError(
            "invalid scriptevent message_id; expected namespace:path matching "
            "^[a-z0-9_.-]+:[a-z0-9_./-]+$"
        )
    return message_id


def _sanitize_tellraw_target(target: str) -> str:
    return sanitize_tellraw_target(target)


def sanitize_tellraw_text(message: str) -> str:
    """Escape tellraw text before embedding in the rawtext JSON value.

    MCBE treats ``%`` as a format placeholder inside tellraw text; double it so
    literal percents render. Quote/backslash escaping is left to ``json.dumps``.
    """
    return message.replace("%", "%%")


class MinecraftHeader(BaseModel):
    """Minecraft WebSocket 消息头"""

    model_config = ConfigDict(extra="allow")

    requestId: str = Field(default_factory=lambda: str(uuid4()))
    messagePurpose: Literal[
        "subscribe", "commandRequest", "commandResponse", "event", "error"
    ] = "commandRequest"
    version: int = 1
    EventName: str | None = None
    eventName: str | None = None  # 事件名称（小写）


class MinecraftOrigin(BaseModel):
    """命令来源"""

    model_config = ConfigDict(extra="allow")

    type: Literal["player", "say"] = "player"


class MinecraftCommandBody(BaseModel):
    """命令请求体"""

    model_config = ConfigDict(extra="allow")

    origin: MinecraftOrigin = Field(default_factory=MinecraftOrigin)
    commandLine: str
    version: int = 17039360


class MinecraftSubscribeBody(BaseModel):
    """订阅请求体"""

    model_config = ConfigDict(extra="allow")

    eventName: str


class MinecraftMessage(BaseModel):
    """通用 Minecraft WebSocket 消息"""

    model_config = ConfigDict(extra="allow")

    header: MinecraftHeader
    body: dict[str, Any]


class MinecraftCommand(BaseModel):
    """Minecraft 命令消息"""

    model_config = ConfigDict(extra="allow")

    header: MinecraftHeader = Field(
        default_factory=lambda: MinecraftHeader(
            messagePurpose="commandRequest",
            EventName="commandRequest",
        )
    )
    body: MinecraftCommandBody

    @classmethod
    def create_tellraw(
        cls,
        message: str,
        color: str = "§a",
        target: str = "@a",
    ) -> MinecraftCommand:
        """创建 tellraw 命令"""
        safe_message = sanitize_tellraw_text(message)
        rawtext = json.dumps(
            {"rawtext": [{"text": f"{color}{safe_message}"}]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        safe_target = sanitize_tellraw_target(target)
        command_line = f"tellraw {safe_target} {rawtext}"
        return cls(
            body=MinecraftCommandBody(
                origin=MinecraftOrigin(type="say"),
                commandLine=command_line,
            )
        )

    @classmethod
    def create_scriptevent(cls, content: str, message_id: str = "server:data") -> MinecraftCommand:
        """创建 scriptevent 命令"""
        safe_message_id = validate_scriptevent_message_id(message_id)
        return cls(
            body=MinecraftCommandBody(
                commandLine=f"scriptevent {safe_message_id} {content}",
            )
        )

    @classmethod
    def create_raw(cls, command: str) -> MinecraftCommand:
        """创建原始命令"""
        return cls(
            body=MinecraftCommandBody(commandLine=command)
        )


class MinecraftSubscribe(BaseModel):
    """Minecraft 事件订阅消息"""

    model_config = ConfigDict(extra="allow")

    header: MinecraftHeader = Field(
        default_factory=lambda: MinecraftHeader(
            messagePurpose="subscribe",
            EventName="commandRequest",
        )
    )
    body: MinecraftSubscribeBody

    @classmethod
    def player_message(cls) -> MinecraftSubscribe:
        """订阅玩家消息事件"""
        return cls(body=MinecraftSubscribeBody(eventName="PlayerMessage"))


class PlayerMessageEvent(BaseModel):
    """玩家消息事件"""

    model_config = ConfigDict(extra="allow")

    sender: str
    message: str
    type: str | None = None
    receiver: str | None = None

    @classmethod
    def from_event_body(cls, body: dict[str, Any]) -> PlayerMessageEvent:
        """从事件体解析"""
        return cls(
            sender=body.get("sender", ""),
            message=body.get("message", ""),
            type=body.get("type"),
            receiver=body.get("receiver"),
        )


class MinecraftCommandResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str = ""
    header: dict[str, Any]
    body: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def _populate_request_id(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("request_id"):
            return data
        header = data.get("header")
        if not isinstance(header, dict):
            return data
        populated = dict(data)
        populated["request_id"] = str(header.get("requestId", ""))
        return populated


class MinecraftErrorFrame(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str = ""
    header: dict[str, Any]
    body: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def _populate_request_id(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("request_id"):
            return data
        header = data.get("header")
        if not isinstance(header, dict):
            return data
        populated = dict(data)
        populated["request_id"] = str(header.get("requestId", ""))
        return populated
