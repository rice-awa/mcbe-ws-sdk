"""WebSocket gateway message value objects.

These are the display / delivery contract types the gateway produces on its
event bus and routes through :class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`.
They are intentionally lean: they carry only the fields a gateway renderer or
delivery path needs (what to say, to whom, how). They do NOT carry the agent's
conversation framing (``connection_id`` / ``BaseMessage.id`` / timestamp) — that
host-specific metadata stays on the :class:`ConnectionState` the event is paired
with. Keeping these types dependency-free is what lets the gateway layer type a
sink without importing the agent's ``models.messages`` / ``core.queue``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

StreamChunkType = Literal[
    "reasoning",
    "content",
    "error",
    "thinking_start",
    "thinking_end",
    "tool_call",
    "tool_result",
]

DeliveryMode = Literal["tellraw", "scriptevent"]


@dataclass
class StreamChunk:
    """A single outbound stream render unit (one "sentence/event" of a reply)."""

    chunk_type: StreamChunkType
    content: str
    sequence: int = 0
    delivery: DeliveryMode = "tellraw"
    player_name: str | None = None
    target: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result_preview: str | None = None
    id: UUID = field(default_factory=uuid4)


NotificationLevel = Literal["info", "warning", "error"]


@dataclass
class SystemNotification:
    """A host/system status line surfaced to a player (or all players)."""

    level: NotificationLevel
    message: str
    player_name: str | None = None
    id: UUID = field(default_factory=uuid4)
