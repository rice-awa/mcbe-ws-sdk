"""MCBE WebSocket gateway SDK.

The gateway package owns the connection lifecycle, event bus, hook/sink
protocols and the response routing machinery. The host application (the main
repo) injects behaviour by implementing
:class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook` and
:class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`, then drives the stack through
:class:`~mcbe_ws_sdk.gateway.server_facade.McbeServerFacade`.
"""

from mcbe_ws_sdk.gateway.connection import DEFAULT_PLAYER_KEY, ConnectionState, PlayerSession
from mcbe_ws_sdk.gateway.events import EventBus, WsEventType
from mcbe_ws_sdk.gateway.hook import ConnectionHook, NoOpHook
from mcbe_ws_sdk.gateway.messages import StreamChunk, SystemNotification
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
)

__all__ = [
    "DEFAULT_PLAYER_KEY",
    "ConnectionHook",
    "ConnectionState",
    "DefaultResponseSink",
    "EventBus",
    "NoOpHook",
    "PlayerSession",
    "ResponseKind",
    "ResponseSink",
    "RouteEnvelope",
    "StreamChunk",
    "SystemNotification",
    "WsEventType",
]
