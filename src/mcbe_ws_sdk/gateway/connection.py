"""Connection state + connection manager owned by the gateway.

Extends the minimal :class:`ConnectionState` foundation with the lifecycle the
host drives through the facade: per-connection response queues, an outbound
``send_payload`` callable (the transport's frame send, e.g. ``websocket.send``),
and a :class:`ConnectionManager` that owns the response-sender coroutine and
emits ``CONNECTED`` / ``DISCONNECTED`` on the
:class:`~mcbe_ws_sdk.gateway.events.EventBus`.

The response-sender loop never builds Minecraft commands itself. It classifies
each queued message with
:meth:`~mcbe_ws_sdk.gateway.sink.RouteEnvelope.from_message` and forwards it to
the shared :class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`, pushing the
application-specific mapping entirely onto the host's ``HostSink``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import structlog

from mcbe_ws_sdk.gateway.events import EventBus, WsEventType
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink, ResponseSink, RouteEnvelope

logger = structlog.get_logger(__name__)

SendPayload = Callable[[str], Awaitable[None]]


@dataclass
class ConnectionState:
    """Transport-agnostic connection identity.

    Host-specific / transport wiring lives on the state but typed as opaque
    callables so the gateway never imports ``websockets`` itself: the facade or
    a host transport adapter sets ``send_payload``. ``response_queue`` is the
    inbound message stream the response sender drains.
    """

    id: UUID = field(default_factory=uuid4)
    _player_name: str | None = field(default=None, repr=False)
    send_payload: SendPayload | None = None
    response_queue: asyncio.Queue[object] | None = None

    @property
    def player_name(self) -> str | None:
        """Most-recent speaker convenience pointer only.

        .. deprecated::
            Use :attr:`PlayerMessageEvent.sender` for authoritative player identity.
            This attribute is retained for backwards compatibility only and will
            emit a :class:`DeprecationWarning` on access.
        """
        import warnings

        warnings.warn(
            "ConnectionState.player_name is a convenience pointer only; "
            "use PlayerMessageEvent.sender for authoritative player identity",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._player_name


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
        """Cancel the sender coroutine, drop the connection, emit DISCONNECTED."""
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
        logger.debug("response_sender_started", connection_id=str(state.id))
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
            logger.debug("response_sender_cancelled", connection_id=str(state.id))
            raise
        except Exception:
            logger.exception("response_sender_error", connection_id=str(state.id))
        logger.debug("response_sender_stopped", connection_id=str(state.id))
