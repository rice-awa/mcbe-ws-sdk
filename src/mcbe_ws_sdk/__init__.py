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
    ConnectionHook,
    ConnectionManager,
    ConnectionState,
    DefaultResponseSink,
    EventBus,
    GatewaySettings,
    McbeServerFacade,
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
)
from mcbe_ws_sdk.profiles import LEGACY_MCBEAI_V1, LegacyMcbeAiV1Profile
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.codec import encode_legacy_response_commands
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.delivery import LegacyMcbeAiV1Delivery
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
    "AddonBridgeService",
    "AddonBridgeSettings",
    "AddonMessageResult",
    "BridgeClosedError",
    "BridgeError",
    "BridgeLimitError",
    "BridgeTimeoutError",
    "CommandRegistry",
    "ConfigurationError",
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
    "LEGACY_MCBEAI_V1",
    "LegacyMcbeAiV1Delivery",
    "LegacyMcbeAiV1Profile",
    "McbeOutboundDelivery",
    "McbeServerFacade",
    "McbeWsSdkError",
    "MinecraftCommandResponse",
    "MinecraftErrorFrame",
    "MinecraftProtocolHandler",
    "NoOpHook",
    "OutboundText",
    "PlayerMessageEvent",
    "ProtocolError",
    "ResponseKind",
    "ResponseSink",
    "RouteEnvelope",
    "SubscriptionToken",
    "SystemNotification",
    "WebsocketTransportConfig",
    "WsEventType",
    "encode_legacy_response_commands",
)
