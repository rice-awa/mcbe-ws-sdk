"""Connection state + connection manager owned by the gateway.

Extends the minimal :class:`ConnectionState` foundation with the lifecycle the
host drives through the facade: per-connection response queues, an outbound
``send_payload`` callable (the transport's frame send, e.g. ``websocket.send``),
and a :class:`ConnectionManager` that owns the response-sender coroutine and
emits ``DISCONNECTED`` on the
:class:`~mcbe_ws_sdk.gateway.events.EventBus`.

``CONNECTED`` is intentionally **not** emitted here — the facade emits it after
a successful handshake + subscribe, at the same moment as ``hook.on_connected``.

The response-sender loop never builds Minecraft commands itself. It classifies
each queued message with
:meth:`~mcbe_ws_sdk.gateway.sink.RouteEnvelope.from_message` and routes it
inline to the sink's ``on_*`` methods, pushing the application-specific mapping
entirely onto the host's ``HostSink``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import structlog

from mcbe_ws_sdk.gateway.events import EventBus, WsEventType
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification
from mcbe_ws_sdk.gateway.sink import (
    DefaultResponseSink,
    ResponseKind,
    ResponseSink,
    RouteEnvelope,
)

logger = structlog.get_logger(__name__)

SendPayload = Callable[[str], Awaitable[None]]

# Default matches WebsocketTransportConfig.response_queue_maxsize.
_DEFAULT_RESPONSE_QUEUE_MAXSIZE = 256


def enqueue_response(state: ConnectionState, message: object) -> None:
    """Put ``message`` onto ``state.response_queue``, dropping the oldest if full.

    No-ops when the connection has no queue. On overflow the oldest item is
    discarded and a warning is logged so a slow consumer cannot back-pressure
    the host forever.
    """
    queue = state.response_queue
    if queue is None:
        return
    if queue.full():
        try:
            dropped = queue.get_nowait()
        except asyncio.QueueEmpty:
            dropped = None
        logger.warning(
            "response_queue_overflow_drop_oldest",
            connection_id=str(state.id),
            dropped_type=type(dropped).__name__ if dropped is not None else None,
            maxsize=queue.maxsize,
        )
    queue.put_nowait(message)


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
        response_queue_maxsize: int = _DEFAULT_RESPONSE_QUEUE_MAXSIZE,
    ) -> None:
        if type(response_queue_maxsize) is not int or response_queue_maxsize <= 0:
            raise ValueError("response_queue_maxsize must be a positive integer")
        self._sink = sink or DefaultResponseSink()
        self._bus = event_bus or EventBus()
        self._response_queue_maxsize = response_queue_maxsize
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

        The returned state carries a fresh bounded ``response_queue``; the host
        posts response messages to it (prefer :func:`enqueue_response` so a full
        queue drops the oldest item instead of blocking). ``send_payload`` is the
        transport frame-send the sink's command routes ultimately deliver through.

        Does **not** emit ``WsEventType.CONNECTED`` — that is the facade's job
        after handshake + subscribe succeed.
        """
        state = ConnectionState(id=connection_id or uuid4(), send_payload=send_payload)
        state.response_queue = asyncio.Queue(maxsize=self._response_queue_maxsize)
        self._connections[state.id] = state
        self._sender_tasks[state.id] = asyncio.create_task(self._response_sender(state))
        logger.info("connection_created", connection_id=str(state.id))
        return state

    async def drop_connection(self, connection_id: UUID) -> None:
        """Cancel the sender coroutine, drop the connection, emit DISCONNECTED."""
        state = self._connections.pop(connection_id, None)
        if state is not None and state.response_queue is not None:
            discarded = state.response_queue.qsize()
            if discarded:
                logger.warning(
                    "response_queue_discarded",
                    connection_id=str(connection_id),
                    discarded=discarded,
                )
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
            while True:
                message = await queue.get()
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
                    await self._route_envelope(state, envelope)
                except Exception:
                    logger.exception(
                        "response_sink_dispatch_failed",
                        connection_id=str(state.id),
                        kind=envelope.kind.value,
                    )
        except asyncio.CancelledError:
            logger.debug("response_sender_cancelled", connection_id=str(state.id))
            raise
        except Exception:
            logger.exception("response_sender_error", connection_id=str(state.id))
        logger.debug("response_sender_stopped", connection_id=str(state.id))

    async def _route_envelope(self, state: ConnectionState, envelope: RouteEnvelope) -> None:
        """Inline envelope routing — never requires ``ResponseSink.dispatch``."""
        if envelope.kind is ResponseKind.OUTBOUND_TEXT:
            if not isinstance(envelope.payload, OutboundText):
                raise TypeError(
                    "Expected OutboundText for OUTBOUND_TEXT kind, "
                    f"got {type(envelope.payload).__name__}"
                )
            await self._sink.on_outbound_text(state, envelope.payload)
            return
        if envelope.kind is ResponseKind.SYSTEM_NOTIFICATION:
            if not isinstance(envelope.payload, SystemNotification):
                raise TypeError(
                    "Expected SystemNotification for SYSTEM_NOTIFICATION kind, "
                    f"got {type(envelope.payload).__name__}"
                )
            await self._sink.on_system_notification(state, envelope.payload)
            return
        raise TypeError(f"Unsupported response kind: {envelope.kind!r}")
