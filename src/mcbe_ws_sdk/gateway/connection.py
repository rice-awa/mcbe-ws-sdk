"""Connection state + connection manager owned by the gateway.

Extends the minimal :class:`ConnectionState` foundation with the lifecycle the
host drives through the facade: per-connection response queues, an outbound
``send_payload`` callable (the transport's frame send, e.g. ``websocket.send``),
and a :class:`ConnectionManager` that owns the response-sender coroutine and
emits ``CONNECTED`` / ``DISCONNECTED`` on the
:class:`~mcbe_ws_sdk.gateway.events.EventBus`.

Multi-player isolation: a single WebSocket connection is shared by many players
in the MCBE server model, so every per-player setting is bucketed by
``player_name`` via :meth:`ConnectionState.get_player_session`. The top-level
``player_name`` is ONLY a convenience pointer to "most recent speaker" and MUST
NOT be read for routing decisions — always pull the bucket from ``player_event.sender``.

The response-sender loop never builds Minecraft commands itself. It classifies
each queued message with
:meth:`~mcbe_ws_sdk.gateway.sink.RouteEnvelope.from_message` and forwards it to
the shared :class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`, pushing the
application-specific mapping (game_message / run_command / ai_response_sync)
entirely onto the host's ``HostSink``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from mcbe_ws_sdk._logging import get_logger
from mcbe_ws_sdk.gateway.events import EventBus, WsEventType
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink, ResponseSink, RouteEnvelope

logger = get_logger(__name__)

SendPayload = Callable[[str], Awaitable[None]]

DEFAULT_PLAYER_KEY = "__anonymous__"


@dataclass
class PlayerSession:
    """Per-player, per-connection mutable settings (multiplayer isolation bucket)."""

    player_name: str
    context_enabled: bool = True
    custom_variables: dict[str, str] = field(default_factory=dict)


@dataclass
class ConnectionState:
    """Transport-agnostic connection identity + per-player session buckets.

    Host-specific / transport wiring lives on the state but typed as opaque
    callables so the gateway never imports ``websockets`` itself: the facade or
    a host transport adapter sets ``send_payload``. ``response_queue`` is the
    inbound message stream the response sender drains.
    """

    id: UUID = field(default_factory=uuid4)
    authenticated: bool = False
    player_name: str | None = None  # most-recent speaker convenience pointer only
    send_payload: SendPayload | None = None
    response_queue: asyncio.Queue[object] | None = None
    _player_sessions: dict[str, PlayerSession] = field(default_factory=dict)

    def get_player_session(self, player_name: str | None = DEFAULT_PLAYER_KEY) -> PlayerSession:
        """Return the per-player bucket, creating a default one if missing."""
        key = player_name or DEFAULT_PLAYER_KEY
        session = self._player_sessions.get(key)
        if session is None:
            session = PlayerSession(player_name=key)
            self._player_sessions[key] = session
        return session

    def clear_player_sessions(self) -> None:
        """Drop every player session bucket (called on disconnect)."""
        self._player_sessions.clear()

    def all_player_sessions(self) -> list[PlayerSession]:
        return list(self._player_sessions.values())


class ConnectionManager:
    """Owns active connections and their response-sender coroutines.

    Construction takes injectable collaborators (sink / event bus) so the facade
    can supply host-backed variants; both fall back to gateway defaults.
    """

    def __init__(
        self,
        *,
        sink: ResponseSink | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._sink = sink or DefaultResponseSink()
        self._bus = event_bus or EventBus()
        self._connections: dict[UUID, ConnectionState] = {}
        self._sender_tasks: dict[UUID, asyncio.Task[None]] = {}

    @property
    def sink(self) -> ResponseSink:
        return self._sink

    @property
    def event_bus(self) -> EventBus:
        return self._bus

    async def create_connection(
        self,
        *,
        send_payload: SendPayload,
        connection_id: UUID | None = None,
    ) -> ConnectionState:
        """Register a new connection and start its response-sender coroutine.

        The returned state carries a fresh ``response_queue``; the host posts
        response messages to it. ``send_payload`` is the transport frame-send the
        sink's command routes ultimately deliver through.
        """
        state = ConnectionState(id=connection_id or uuid4(), send_payload=send_payload)
        state.response_queue = asyncio.Queue()
        self._connections[state.id] = state
        self._sender_tasks[state.id] = asyncio.create_task(self._response_sender(state))
        await self._bus.emit(WsEventType.CONNECTED, state)
        logger.info("connection_created", connection_id=str(state.id))
        return state

    async def drop_connection(self, connection_id: UUID) -> None:
        """Cancel the sender coroutine, drop the connection, emit DISCONNECTED.

        Per-player session cleanup is the host's responsibility (see
        :class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook.on_disconnected`).
        """
        state = self._connections.pop(connection_id, None)
        await self._cancel_sender(connection_id)
        if state is not None:
            await self._bus.emit(WsEventType.DISCONNECTED, state)
            logger.info("connection_dropped", connection_id=str(connection_id))

    def get_connection(self, connection_id: UUID) -> ConnectionState | None:
        return self._connections.get(connection_id)

    def all_connections(self) -> list[ConnectionState]:
        return list(self._connections.values())

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def shutdown_all(self) -> None:
        """Drop every connection (on server stop)."""
        for connection_id in list(self._connections):
            await self.drop_connection(connection_id)

    async def _cancel_sender(self, connection_id: UUID) -> None:
        task = self._sender_tasks.pop(connection_id, None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _response_sender(self, state: ConnectionState) -> None:
        """Drain ``state.response_queue`` and route each message via the sink."""
        queue = state.response_queue
        if queue is None:
            return
        logger.info("response_sender_started", connection_id=str(state.id))
        try:
            while state.id in self._connections:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=0.5)
                except TimeoutError:
                    continue
                try:
                    envelope = RouteEnvelope.from_message(message)
                except TypeError:
                    logger.warning(
                        "unroutable_response_dropped",
                        connection_id=str(state.id),
                        message_type=type(message).__name__,
                    )
                    continue
                try:
                    await self._sink.dispatch(state, envelope)
                except Exception:
                    logger.exception(
                        "response_sink_dispatch_failed",
                        connection_id=str(state.id),
                        kind=envelope.kind.value if envelope else None,
                    )
        except asyncio.CancelledError:
            logger.info("response_sender_cancelled", connection_id=str(state.id))
            raise
        except Exception:
            logger.exception("response_sender_error", connection_id=str(state.id))
        logger.info("response_sender_stopped", connection_id=str(state.id))
