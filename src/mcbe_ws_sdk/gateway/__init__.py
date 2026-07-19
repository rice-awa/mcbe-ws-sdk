"""MCBE WebSocket gateway SDK.

The gateway package owns the connection lifecycle, event bus, hook/sink
protocols and the response routing machinery. The host application (the main
repo) injects behaviour by implementing
:class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook` and
:class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`, then drives the stack through
:class:`~mcbe_ws_sdk.gateway.server_facade.McbeServerFacade`.
"""

from mcbe_ws_sdk.gateway.connection import (
    DEFAULT_PLAYER_KEY,
    ConnectionManager,
    ConnectionState,
    PlayerSession,
    SendPayload,
)
from mcbe_ws_sdk.gateway.events import EventBus, SubscriptionToken, WsEventType
from mcbe_ws_sdk.gateway.handler import (
    MessageSurfaceConfig,
    MinecraftProtocolHandler,
    TellrawMessage,
)
from mcbe_ws_sdk.gateway.hook import ConnectionHook, NoOpHook
from mcbe_ws_sdk.gateway.messages import StreamChunk, SystemNotification
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
    SilentResponseSink,
)

__all__ = [
    "DEFAULT_PLAYER_KEY",
    "ConnectionManager",
    "ConnectionHook",
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
