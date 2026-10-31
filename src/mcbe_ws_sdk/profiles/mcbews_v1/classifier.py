"""Compatibility exports for the optional MCBEWS/1 adapter."""

from mcbe_ws_sdk.extensions.mcbews_v1.classifier import (
    ToolPlayerChannel as ToolPlayerChannel,
)
from mcbe_ws_sdk.extensions.mcbews_v1.classifier import (
    ToolPlayerMessage as ToolPlayerMessage,
)
from mcbe_ws_sdk.extensions.mcbews_v1.classifier import (
    classify_tool_player_message as classify_tool_player_message,
)
from mcbe_ws_sdk.extensions.mcbews_v1.classifier import (
    is_tool_player_channel_message as is_tool_player_channel_message,
)

__all__ = [
    "ToolPlayerChannel",
    "ToolPlayerMessage",
    "classify_tool_player_message",
    "is_tool_player_channel_message",
]
