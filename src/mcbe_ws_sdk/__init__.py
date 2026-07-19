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

from mcbe_ws_sdk.addon import AddonBridgeClient, AddonBridgeService
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
from mcbe_ws_sdk.gateway import (
    DEFAULT_PLAYER_KEY,
    ConnectionHook,
    ConnectionManager,
    ConnectionState,
    DefaultResponseSink,
    EventBus,
    McbeServerFacade,
    MessageSurfaceConfig,
    MinecraftProtocolHandler,
    NoOpHook,
    PlayerSession,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
    SendPayload,
    SilentResponseSink,
    StreamChunk,
    SubscriptionToken,
    SystemNotification,
    TellrawMessage,
    WsEventType,
)
from mcbe_ws_sdk.protocol.addon import AddonBridgeResponse
from mcbe_ws_sdk.protocol.minecraft import MCColor, MCPrefix, PlayerMessageEvent

__version__ = "0.1.0"

__all__ = [
    "addon",
    "gateway",
    "protocol",
    # protocol
    "AddonBridgeResponse",
    "MCColor",
    "MCPrefix",
    "PlayerMessageEvent",
    # errors
    "BridgeClosedError",
    "BridgeError",
    "BridgeLimitError",
    "BridgeTimeoutError",
    "ConfigurationError",
    "FacadeLifecycleError",
    "FrameTooLargeError",
    "McbeWsSdkError",
    "ProtocolError",
    # addon
    "AddonBridgeClient",
    "AddonBridgeService",
    # gateway
    "DEFAULT_PLAYER_KEY",
    "ConnectionHook",
    "ConnectionManager",
    "ConnectionState",
    "DefaultResponseSink",
    "EventBus",
    "McbeServerFacade",
    "MessageSurfaceConfig",
    "MinecraftProtocolHandler",
    "NoOpHook",
    "PlayerSession",
    "ResponseKind",
    "ResponseSink",
    "RouteEnvelope",
    "SendPayload",
    "SilentResponseSink",
    "SubscriptionToken",
    "StreamChunk",
    "SystemNotification",
    "TellrawMessage",
    "WsEventType",
]
