"""MCBE WebSocket gateway SDK.

The gateway package owns the connection lifecycle, event bus, hook/sink
protocols and the response routing machinery. The host application (the main
repo) injects behaviour by implementing
:class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook` and
:class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`, then drives the stack through
:class:`~mcbe_ws_sdk.gateway.server_facade.McbeServerFacade`.
"""

from mcbe_ws_sdk.config import GatewaySettings, WebsocketTransportConfig
from mcbe_ws_sdk.gateway.connection import ConnectionManager, ConnectionState
from mcbe_ws_sdk.gateway.events import EventBus, SubscriptionToken, WsEventType
from mcbe_ws_sdk.gateway.handler import MessageSurfaceConfig, MinecraftProtocolHandler
from mcbe_ws_sdk.gateway.hook import ConnectionHook, NoOpHook
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
)

__all__ = [
    "ConnectionHook",
    "ConnectionManager",
    "ConnectionState",
    "DefaultResponseSink",
    "EventBus",
    "GatewaySettings",
    "McbeServerFacade",
    "MessageSurfaceConfig",
    "MinecraftProtocolHandler",
    "NoOpHook",
    "OutboundText",
    "ResponseKind",
    "ResponseSink",
    "RouteEnvelope",
    "SubscriptionToken",
    "SystemNotification",
    "WebsocketTransportConfig",
    "WsEventType",
]
