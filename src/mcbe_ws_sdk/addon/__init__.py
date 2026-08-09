"""Addon bridge capability for the MCBE WebSocket SDK."""

from mcbe_ws_sdk.addon.service import (
    AddonBridgeClient,
    AddonBridgeService,
    AddonMessageResult,
    ConnectionAddonBridgeClient,
    LegacyUiChatCallbackAdapter,
)
from mcbe_ws_sdk.config import AddonBridgeSettings
from mcbe_ws_sdk.profiles.mcbews_v1.classifier import ToolPlayerMessage
from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    ApprovalDecision,
    SessionRequest,
    SessionResponse,
    TextResponseChunk,
    TextResponseMessage,
    UiChatMessage,
)

__all__ = [
    "AddonBridgeClient",
    "AddonBridgeService",
    "AddonBridgeSettings",
    "AddonMessageResult",
    "ConnectionAddonBridgeClient",
    "LegacyUiChatCallbackAdapter",
    "ToolPlayerMessage",
    "ApprovalDecision",
    "SessionRequest",
    "SessionResponse",
    "TextResponseChunk",
    "TextResponseMessage",
    "UiChatMessage",
]
