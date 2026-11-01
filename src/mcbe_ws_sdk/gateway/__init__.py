"""MCBE WebSocket gateway internals.

The gateway package owns the connection lifecycle, event bus, hook/sink
protocols and the response routing machinery. The host application injects
behaviour by implementing
:class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook` and
:class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`, then drives the stack through
:class:`~mcbe_ws_sdk.server.McbeServer`.
"""

from mcbe_ws_sdk.config import (
    DispatchSettings,
    GatewaySettings,
    SendSchedulerSettings,
    WebsocketTransportConfig,
)
from mcbe_ws_sdk.gateway.commands import NativeCommandClient
from mcbe_ws_sdk.gateway.connection import (
    ConnectionManager,
    ConnectionState,
    enqueue_response,
)
from mcbe_ws_sdk.gateway.dispatch import BusinessDispatcher
from mcbe_ws_sdk.gateway.events import EventBus, SubscriptionToken, WsEventType
from mcbe_ws_sdk.gateway.handler import MessageSurfaceConfig, MinecraftProtocolHandler
from mcbe_ws_sdk.gateway.hook import ConnectionHook, NativeEventHook, NoOpHook
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification
from mcbe_ws_sdk.gateway.native_events import (
    InterceptResult,
    NativeEvent,
    NativeEventSubscriptions,
    NativeInterceptor,
    NativeObserver,
    NativeSubscriptionToken,
)
from mcbe_ws_sdk.gateway.runtime import ConnectionRuntime
from mcbe_ws_sdk.gateway.sender import OutboundScheduler, SendPriority
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
)

__all__ = [
    "NativeEvent",
    "NativeEventSubscriptions",
    "NativeSubscriptionToken",
    "InterceptResult",
    "NativeObserver",
    "NativeInterceptor",
    "NativeEventHook",
    "BusinessDispatcher",
    "ConnectionHook",
    "ConnectionManager",
    "ConnectionRuntime",
    "ConnectionState",
    "DefaultResponseSink",
    "DispatchSettings",
    "EventBus",
    "GatewaySettings",
    "MessageSurfaceConfig",
    "MinecraftProtocolHandler",
    "NativeCommandClient",
    "NoOpHook",
    "OutboundScheduler",
    "OutboundText",
    "ResponseKind",
    "ResponseSink",
    "RouteEnvelope",
    "SendPriority",
    "SendSchedulerSettings",
    "SubscriptionToken",
    "SystemNotification",
    "WebsocketTransportConfig",
    "WsEventType",
    "enqueue_response",
]
