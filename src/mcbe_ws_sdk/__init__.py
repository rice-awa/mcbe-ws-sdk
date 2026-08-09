"""Generic WebSocket gateway SDK for Minecraft Bedrock Edition.

Public surface
--------------
The gateway SDK exposes a dual-layer interface:

  * Low-level: subscribe to an :class:`~mcbe_ws_sdk.gateway.events.EventBus`
    keyed by :class:`~mcbe_ws_sdk.gateway.events.WsEventType`.
  * High-level: implement :class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook`
    and :class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`, then drive the stack
    through :class:`~mcbe_ws_sdk.gateway.server_facade.McbeServerFacade`.

The full connection lifetime, packet request abstraction and byte-safe command
chunking are provided; the agent's LLM / message-broker concerns are the host's.
"""

from __future__ import annotations

import importlib.metadata

from mcbe_ws_sdk.addon import (
    AddonBridgeClient,
    AddonBridgeService,
    AddonBridgeSettings,
    AddonMessageResult,
    ConnectionAddonBridgeClient,
    LegacyUiChatCallbackAdapter,
)
from mcbe_ws_sdk.command import CommandRegistry
from mcbe_ws_sdk.delivery import McbeOutboundDelivery
from mcbe_ws_sdk.errors import (
    BridgeClosedError,
    BridgeError,
    BridgeLimitError,
    BridgeTimeoutError,
    ConfigurationError,
    FacadeLifecycleError,
    FrameTooLargeError,
    McbeWsSdkError,
    ProtocolError,
)
from mcbe_ws_sdk.flow import FlowControlMiddleware, FlowControlSettings
from mcbe_ws_sdk.gateway import (
    AddonControlHook,
    ConnectionHook,
    ConnectionManager,
    ConnectionState,
    DefaultResponseSink,
    EventBus,
    GatewaySettings,
    LegacyUiChatHook,
    LegacyUiChatHookAdapter,
    McbeServerFacade,
    MessageSurfaceConfig,
    MinecraftProtocolHandler,
    NoOpHook,
    OutboundText,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
    SubscriptionToken,
    SystemNotification,
    WebsocketTransportConfig,
    WsEventType,
    enqueue_response,
)
from mcbe_ws_sdk.logging import configure_logging
from mcbe_ws_sdk.profiles import (
    MCBEWS_V1,
    AddonBridgeProfile,
    McbewsV1Profile,
    McbewsV1Protocol,
)
from mcbe_ws_sdk.profiles.mcbews_v1.classifier import (
    ToolPlayerChannel,
    ToolPlayerMessage,
    classify_tool_player_message,
    is_tool_player_channel_message,
)
from mcbe_ws_sdk.profiles.mcbews_v1.codec import (
    decode_approval_decision,
    decode_session_request_chat,
    decode_session_response_message,
    decode_text_response_frame,
    encode_approval_decision,
    encode_session_request_chat,
    encode_session_response_command,
    encode_text_response_commands,
)
from mcbe_ws_sdk.profiles.mcbews_v1.delivery import McbewsV1Delivery
from mcbe_ws_sdk.profiles.mcbews_v1.manifest import (
    MCBEWS_V1_MANIFEST,
    MCBEWS_V1_WIRE_VECTORS,
    load_manifest,
    load_wire_vectors,
)
from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    ApprovalDecision,
    SessionError,
    SessionRequest,
    SessionResponse,
    TextResponseChunk,
    TextResponseMessage,
    TokenUsage,
    UiChatMessage,
)
from mcbe_ws_sdk.protocol import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)

try:
    __version__ = importlib.metadata.version("mcbe-ws-sdk")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = (
    "__version__",
    "AddonBridgeClient",
    "AddonBridgeProfile",
    "AddonBridgeService",
    "AddonBridgeSettings",
    "AddonMessageResult",
    "LegacyUiChatCallbackAdapter",
    "AddonControlHook",
    "BridgeClosedError",
    "BridgeError",
    "BridgeLimitError",
    "BridgeTimeoutError",
    "CommandRegistry",
    "ConfigurationError",
    "ConnectionAddonBridgeClient",
    "ConnectionHook",
    "ConnectionManager",
    "ConnectionState",
    "DefaultResponseSink",
    "EventBus",
    "FacadeLifecycleError",
    "FlowControlMiddleware",
    "FlowControlSettings",
    "FrameTooLargeError",
    "GatewaySettings",
    "MCBEWS_V1",
    "McbeOutboundDelivery",
    "McbeServerFacade",
    "McbeWsSdkError",
    "McbewsV1Delivery",
    "McbewsV1Profile",
    "McbewsV1Protocol",
    "MCBEWS_V1_MANIFEST",
    "MCBEWS_V1_WIRE_VECTORS",
    "MessageSurfaceConfig",
    "MinecraftCommandResponse",
    "MinecraftErrorFrame",
    "MinecraftProtocolHandler",
    "NoOpHook",
    "LegacyUiChatHook",
    "LegacyUiChatHookAdapter",
    "OutboundText",
    "PlayerMessageEvent",
    "ProtocolError",
    "ResponseKind",
    "ResponseSink",
    "RouteEnvelope",
    "SubscriptionToken",
    "SystemNotification",
    "ToolPlayerChannel",
    "ToolPlayerMessage",
    "ApprovalDecision",
    "SessionError",
    "SessionRequest",
    "SessionResponse",
    "TextResponseChunk",
    "TextResponseMessage",
    "TokenUsage",
    "UiChatMessage",
    "WebsocketTransportConfig",
    "WsEventType",
    "classify_tool_player_message",
    "configure_logging",
    "decode_approval_decision",
    "decode_session_request_chat",
    "decode_session_response_message",
    "decode_text_response_frame",
    "encode_approval_decision",
    "encode_text_response_commands",
    "encode_session_request_chat",
    "encode_session_response_command",
    "enqueue_response",
    "is_tool_player_channel_message",
    "load_manifest",
    "load_wire_vectors",
)
