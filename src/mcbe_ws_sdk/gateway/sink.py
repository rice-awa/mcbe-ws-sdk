"""Response routing sink + ``RouteEnvelope`` value object + ``DefaultResponseSink``.

The response sender coroutine never builds Minecraft commands itself. Instead it
asks a :class:`ResponseSink` to deliver a :class:`RouteEnvelope`, pushing the
application-specific mapping (``game_message`` → broker, ``run_command`` →
``send_raw_command``, ``ai_response_sync`` → reassemble) entirely onto the host.

:meth:`DefaultResponseSink` is the gateway's built-in base — it routes
``STREAM_CHUNK`` and ``SYSTEM_NOTIFICATION`` to on-screen rendering (tellraw /
scriptevent / AI reassembly) and raises :class:`NotImplementedError` on the
three command-style routes so the host overrides them via ``HostSink``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from mcbe_ws_sdk._logging import get_logger
from mcbe_ws_sdk.gateway.messages import StreamChunk, SystemNotification

if TYPE_CHECKING:
    from mcbe_ws_sdk.gateway.connection import ConnectionState

logger = get_logger(__name__)


class ResponseKind(Enum):
    """Message categories the response sender can route."""

    STREAM_CHUNK = "stream_chunk"
    SYSTEM_NOTIFICATION = "system_notification"
    GAME_MESSAGE = "game_message"
    RUN_COMMAND = "run_command"
    AI_RESPONSE_SYNC = "ai_response_sync"


@dataclass(frozen=True)
class RouteEnvelope:
    """A response message the response sender routes to a sink method."""

    kind: ResponseKind
    payload: Any

    @classmethod
    def from_message(cls, msg: Any) -> RouteEnvelope:
        """Classify an opaque host response into a :class:`ResponseKind`.

        Recognises the gateway's own value objects (StreamChunk,
        SystemNotification) by type, and the host's plain-dict envelope by its
        ``"type"`` field. Anything else is rejected — the response loop should
        never silently drop an unroutable message.
        """
        if isinstance(msg, StreamChunk):
            return cls(ResponseKind.STREAM_CHUNK, msg)
        if isinstance(msg, SystemNotification):
            return cls(ResponseKind.SYSTEM_NOTIFICATION, msg)
        if isinstance(msg, dict):
            msg_type = msg.get("type")
            if msg_type == "game_message":
                return cls(ResponseKind.GAME_MESSAGE, msg)
            if msg_type == "run_command":
                return cls(ResponseKind.RUN_COMMAND, msg)
            if msg_type == "ai_response_sync":
                return cls(ResponseKind.AI_RESPONSE_SYNC, msg)
        raise TypeError(f"Unroutable response message: {type(msg).__name__}")


@runtime_checkable
class ResponseSink(Protocol):
    """The five outbound delivery routes the response sender dispatches."""

    async def on_stream_chunk(self, state: ConnectionState, chunk: StreamChunk) -> None:
        ...

    async def on_system_notification(
        self,
        state: ConnectionState,
        note: SystemNotification,
    ) -> None:
        ...

    async def on_game_message(
        self,
        state: ConnectionState,
        payload: dict[str, Any],
    ) -> None:
        ...

    async def on_run_command(
        self,
        state: ConnectionState,
        payload: dict[str, Any],
    ) -> None:
        ...

    async def on_ai_response_sync(
        self,
        state: ConnectionState,
        payload: dict[str, Any],
    ) -> None:
        ...

    async def dispatch(self, state: ConnectionState, envelope: RouteEnvelope) -> None:
        """Route ``envelope`` to the matching ``on_*`` method."""
        ...


class DefaultResponseSink:
    """Gateway default sink: renders stream/system, rejects command routes.

    ``on_game_message`` / ``on_run_command`` / ``on_ai_response_sync`` raise
    :class:`NotImplementedError` so the host's :class:`HostSink` subclasses this
    override them. The two render routes are intentionally thin (logging only at
    this layer) — a real host wires a
    :class:`~mcbe_ws_sdk.delivery.outbound.McbeOutboundDelivery` here. They are
    defined, not left abstract, so a default facade produces visible behaviour
    and the protocol is exercised end-to-end in tests.
    """

    async def on_stream_chunk(self, state: ConnectionState, chunk: StreamChunk) -> None:
        logger.debug(
            "sink_stream_chunk",
            connection_id=str(state.id),
            chunk_type=chunk.chunk_type,
            length=len(chunk.content),
        )

    async def on_system_notification(
        self, state: ConnectionState, note: SystemNotification
    ) -> None:
        logger.info(
            "sink_system_notification",
            connection_id=str(state.id),
            level=note.level,
            message=note.message,
        )

    async def on_game_message(self, state: ConnectionState, payload: dict[str, Any]) -> None:
        raise NotImplementedError(
            "game_message routing is application-specific; override on_game_message "
            "in a ResponseSink subclass (e.g. McbeHost.HostSink)"
        )

    async def on_run_command(self, state: ConnectionState, payload: dict[str, Any]) -> None:
        raise NotImplementedError(
            "run_command routing is application-specific; override on_run_command "
            "in a ResponseSink subclass (e.g. McbeHost.HostSink)"
        )

    async def on_ai_response_sync(self, state: ConnectionState, payload: dict[str, Any]) -> None:
        raise NotImplementedError(
            "ai_response_sync routing is application-specific; override on_ai_response_sync "
            "in a ResponseSink subclass (e.g. McbeHost.HostSink)"
        )

    async def dispatch(self, state: ConnectionState, envelope: RouteEnvelope) -> None:
        match envelope.kind:
            case ResponseKind.STREAM_CHUNK:
                await self.on_stream_chunk(state, envelope.payload)
            case ResponseKind.SYSTEM_NOTIFICATION:
                await self.on_system_notification(state, envelope.payload)
            case ResponseKind.GAME_MESSAGE:
                await self.on_game_message(state, envelope.payload)
            case ResponseKind.RUN_COMMAND:
                await self.on_run_command(state, envelope.payload)
            case ResponseKind.AI_RESPONSE_SYNC:
                await self.on_ai_response_sync(state, envelope.payload)
class SilentResponseSink(DefaultResponseSink):
    """Facade default sink: logs + no-ops on the three command routes.

    A host that injects only a :class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook`
    (and relies on the gateway default ``sink``) must never crash on outbound
    command messages — but :class:`DefaultResponseSink` raises
    :class:`NotImplementedError` on ``GAME_MESSAGE`` / ``RUN_COMMAND`` /
    ``AI_RESPONSE_SYNC``. This subclass mirrors :class:`NoOpHook`: it logs a
    warning (once per call, with the kind) and returns ``None`` instead of
    raising, so a naive host gets visible-but-safe behaviour. The two render
    routes are inherited unchanged.
    """

    async def on_game_message(self, state: ConnectionState, payload: dict[str, Any]) -> None:
        logger.warning(
            "silent_sink_game_message_unhandled",
            connection_id=str(state.id),
        )

    async def on_run_command(self, state: ConnectionState, payload: dict[str, Any]) -> None:
        logger.warning(
            "silent_sink_run_command_unhandled",
            connection_id=str(state.id),
        )

    async def on_ai_response_sync(self, state: ConnectionState, payload: dict[str, Any]) -> None:
        logger.warning(
            "silent_sink_ai_response_sync_unhandled",
            connection_id=str(state.id),
        )
