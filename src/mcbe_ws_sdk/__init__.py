"""Generic WebSocket gateway SDK for Minecraft Bedrock Edition.

Public surface
--------------
The SDK exposes a dual-layer interface:

  * Low-level: subscribe to an :class:`~mcbe_ws_sdk.gateway.events.EventBus`
    keyed by :class:`~mcbe_ws_sdk.gateway.events.WsEventType`.
  * High-level: implement :class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook`
    and :class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`, then drive the stack
    through :class:`~mcbe_ws_sdk.server.McbeServer`.

Connections are admitted only through MCBEWS/2 negotiation. The full
connection lifetime, packet request abstraction and byte-safe command chunking
are provided; the agent's LLM / message-broker concerns are the host's.
"""

from __future__ import annotations

import importlib.metadata

from mcbe_ws_sdk.capability import (
    CapabilityContext,
    CapabilityHandler,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilityValidator,
)
from mcbe_ws_sdk.command import CommandRegistry
from mcbe_ws_sdk.command.registry import ParsedCommand
from mcbe_ws_sdk.config import DispatchSettings, SendSchedulerSettings
from mcbe_ws_sdk.delivery import McbeOutboundDelivery
from mcbe_ws_sdk.diagnostics import DiagnosticObserver, DiagnosticSettings, is_external_echo
from mcbe_ws_sdk.errors import (
    CapabilityAlreadyRegisteredError,
    CapabilityError,
    ConfigurationError,
    FrameTooLargeError,
    McbeWsSdkError,
    ProtocolError,
    RemoteCallError,
    RequestClosedError,
    RequestDuplicateError,
    RequestError,
    RequestLimitError,
    RequestTimeoutError,
    SendClosedError,
    SendError,
    SendQueueFullError,
    ServerLifecycleError,
)
from mcbe_ws_sdk.flow import FlowControlMiddleware, FlowControlSettings
from mcbe_ws_sdk.gateway import (
    BusinessDispatcher,
    ConnectionHook,
    ConnectionManager,
    ConnectionRuntime,
    ConnectionState,
    DefaultResponseSink,
    EventBus,
    GatewaySettings,
    InterceptResult,
    MessageSurfaceConfig,
    MinecraftProtocolHandler,
    NativeCommandClient,
    NativeEvent,
    NativeEventHook,
    NativeEventSubscriptions,
    NativeInterceptor,
    NativeObserver,
    NativeSubscriptionToken,
    NoOpHook,
    OutboundScheduler,
    OutboundText,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
    SendPriority,
    SubscriptionToken,
    SystemNotification,
    WebsocketTransportConfig,
    WsEventType,
    enqueue_response,
)
from mcbe_ws_sdk.logging import configure_logging
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import (
    NegotiatedConnection,
    NegotiationError,
    V2Bridge,
    V2Endpoint,
)
from mcbe_ws_sdk.profiles.mcbews_v2.streams import (
    IncomingStream,
    StreamError,
    StreamHandler,
    StreamRegistration,
    StreamRegistry,
)
from mcbe_ws_sdk.protocol import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    MinecraftUnsubscribe,
    PlayerMessageEvent,
)
from mcbe_ws_sdk.requests import CATEGORY_COMMAND, PendingRequest, RequestTable
from mcbe_ws_sdk.server import McbeServer
from mcbe_ws_sdk.tracing import TraceContext, current_trace, trace_scope

try:
    __version__ = importlib.metadata.version("mcbe-ws-sdk")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = (
    "__version__",
    "McbeServer",
    "ParsedCommand",
    "V2Bridge",
    "V2Endpoint",
    "NegotiatedConnection",
    "NegotiationError",
    "IncomingStream",
    "StreamError",
    "StreamRegistry",
    "StreamRegistration",
    "StreamHandler",
    "BusinessDispatcher",
    "CapabilityAlreadyRegisteredError",
    "CapabilityContext",
    "CapabilityError",
    "CapabilityHandler",
    "CapabilityRegistration",
    "CapabilityRegistry",
    "CapabilityValidator",
    "CATEGORY_COMMAND",
    "CommandRegistry",
    "ConfigurationError",
    "ConnectionHook",
    "ConnectionManager",
    "ConnectionRuntime",
    "ConnectionState",
    "DefaultResponseSink",
    "DiagnosticObserver",
    "DiagnosticSettings",
    "DispatchSettings",
    "EventBus",
    "FlowControlMiddleware",
    "FlowControlSettings",
    "FrameTooLargeError",
    "GatewaySettings",
    "InterceptResult",
    "McbeOutboundDelivery",
    "McbeWsSdkError",
    "MessageSurfaceConfig",
    "MinecraftCommandResponse",
    "MinecraftErrorFrame",
    "MinecraftProtocolHandler",
    "MinecraftUnsubscribe",
    "NativeCommandClient",
    "NativeEvent",
    "NativeEventHook",
    "NativeEventSubscriptions",
    "NativeInterceptor",
    "NativeObserver",
    "NativeSubscriptionToken",
    "NoOpHook",
    "OutboundScheduler",
    "OutboundText",
    "PendingRequest",
    "PlayerMessageEvent",
    "ProtocolError",
    "RemoteCallError",
    "RequestClosedError",
    "RequestDuplicateError",
    "RequestError",
    "RequestLimitError",
    "RequestTable",
    "RequestTimeoutError",
    "ResponseKind",
    "ResponseSink",
    "RouteEnvelope",
    "SendClosedError",
    "SendError",
    "SendPriority",
    "SendQueueFullError",
    "SendSchedulerSettings",
    "ServerLifecycleError",
    "SubscriptionToken",
    "SystemNotification",
    "TraceContext",
    "WebsocketTransportConfig",
    "WsEventType",
    "configure_logging",
    "current_trace",
    "enqueue_response",
    "is_external_echo",
    "trace_scope",
)
