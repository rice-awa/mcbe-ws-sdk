"""Response routing sink + ``RouteEnvelope`` value object + ``DefaultResponseSink``.

The response sender coroutine never builds Minecraft commands itself. Instead it
asks a :class:`ResponseSink` to deliver a :class:`RouteEnvelope`, pushing the
application-specific mapping entirely onto the host.

:class:`DefaultResponseSink` is the gateway's built-in base — it logs
``OUTBOUND_TEXT`` and ``SYSTEM_NOTIFICATION`` messages (metadata only, no player
text) and does nothing else. A host wires an
:class:`~mcbe_ws_sdk.delivery.outbound.McbeOutboundDelivery` here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from mcbe_ws_sdk._logging import get_logger
from mcbe_ws_sdk.gateway.messages import OutboundText, SystemNotification

if TYPE_CHECKING:
    from mcbe_ws_sdk.gateway.connection import ConnectionState

logger = get_logger(__name__)


class ResponseKind(Enum):
    """Message categories the response sender can route."""

    OUTBOUND_TEXT = "outbound_text"
    SYSTEM_NOTIFICATION = "system_notification"


@dataclass(frozen=True, slots=True)
class RouteEnvelope:
    """A response message the response sender routes to a sink method."""

    kind: ResponseKind
    payload: OutboundText | SystemNotification

    @classmethod
    def from_message(cls, message: object) -> RouteEnvelope:
        """Classify a host response into a :class:`RouteEnvelope`.

        Accepts only the gateway's own value objects (OutboundText,
        SystemNotification) by type. Anything else is rejected — the response
        loop should never silently drop an unroutable message.
        """
        if isinstance(message, OutboundText):
            return cls(ResponseKind.OUTBOUND_TEXT, message)
        if isinstance(message, SystemNotification):
            return cls(ResponseKind.SYSTEM_NOTIFICATION, message)
        raise TypeError(f"Unroutable response message: {type(message).__name__}")


@runtime_checkable
class ResponseSink(Protocol):
    """The two outbound delivery routes the response sender dispatches."""

    async def on_outbound_text(
        self, state: ConnectionState, message: OutboundText
    ) -> None:
        ...

    async def on_system_notification(
        self,
        state: ConnectionState,
        message: SystemNotification,
    ) -> None:
        ...

    async def dispatch(self, state: ConnectionState, envelope: RouteEnvelope) -> None:
        """Route ``envelope`` to the matching ``on_*`` method."""
        ...


class DefaultResponseSink:
    """Gateway default sink: logs metadata, delivers nothing to game.

    ``on_outbound_text`` and ``on_system_notification`` log metadata (no player
    text, no command lines). A real host wires a
    :class:`~mcbe_ws_sdk.delivery.outbound.McbeOutboundDelivery` here.
    """

    async def on_outbound_text(
        self, state: ConnectionState, message: OutboundText
    ) -> None:
        logger.debug(
            "sink_outbound_text",
            connection_id=str(state.id),
            message_id=str(message.id),
            channel=message.channel,
            length=len(message.content),
            bytes=len(message.content.encode("utf-8")),
        )

    async def on_system_notification(
        self, state: ConnectionState, message: SystemNotification
    ) -> None:
        logger.info(
            "sink_system_notification",
            connection_id=str(state.id),
            message_id=str(message.id),
            level=message.level,
            length=len(message.message),
        )

    async def dispatch(self, state: ConnectionState, envelope: RouteEnvelope) -> None:
        if envelope.kind is ResponseKind.OUTBOUND_TEXT:
            assert isinstance(envelope.payload, OutboundText)
            await self.on_outbound_text(state, envelope.payload)
            return
        assert isinstance(envelope.payload, SystemNotification)
        await self.on_system_notification(state, envelope.payload)
