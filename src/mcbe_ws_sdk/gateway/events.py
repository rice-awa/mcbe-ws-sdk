"""WebSocket gateway event bus.

Replaces the main repo's hard-coded ``if/elif`` response dispatch with a typed
protocol: events are identified by :class:`WsEventType` and delivered to
subscribers via :class:`EventBus`. Subscriptions default to weak references so a
handler whose only reachable reference is the bus does not keep it alive; pass
``weak=False`` for plain functions/lambdas (which cannot be weakly referenced).
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from mcbe_ws_sdk._logging import get_logger

logger = get_logger(__name__)

Handler = Callable[..., Awaitable[None]]


class WsEventType(Enum):
    """Events the connection lifetime and protocol handler can emit."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    PLAYER_MESSAGE = "player_message"
    BRIDGE_CHUNK = "bridge_chunk"
    UI_CHAT_CHUNK = "ui_chat_chunk"
    UI_CHAT_REASSEMBLED = "ui_chat_reassembled"
    COMMAND_RESPONSE = "command_response"
    RAW_INBOUND = "raw_inbound"
    RAW_OUTBOUND = "raw_outbound"


class EventBus:
    """A typed in-process event bus keyed by :class:`WsEventType`."""

    def __init__(self) -> None:
        self._subscribers: dict[WsEventType, list[Handler]] = {event: [] for event in WsEventType}

    def subscribe(self, event: WsEventType, handler: Handler, *, weak: bool = True) -> None:
        """Register ``handler`` for ``event``.

        By default the reference is held weakly so a subscriber cannot outlive
        its owning object through the bus alone. When ``weak=False`` (e.g. for a
        module-level or ``lambda`` handler) the strong reference is kept.
        """
        wrapped = self._wrap(handler) if weak else handler
        self._subscribers[event].append(wrapped)

    def unsubscribe(self, event: WsEventType, handler: Handler) -> int:
        """Drop every registration (weak or strong) matching ``handler``.

        Returns the number of registrations removed.
        """
        original = self._subscribers[event]
        cleaned: list[Handler] = []
        removed = 0
        for wrapped in original:
            if self._matches(wrapped, handler):
                removed += 1
                continue
            cleaned.append(wrapped)
        self._subscribers[event] = cleaned
        return removed

    def handler_count(self, event: WsEventType) -> int:
        return len(self._subscribers.get(event, ()))

    async def emit(self, event: WsEventType, *args: Any, **kwargs: Any) -> None:
        """Await every subscribed handler for ``event`` concurrently.

        Handlers are snapshotted first so mutations during dispatch don't affect
        the current round, and each handler is isolated: one raising won't
        prevent the others from running.
        """
        handlers = list(self._subscribers.get(event, ()))
        if not handlers:
            return
        results = await asyncio.gather(
            *(self._invoke(h, event, *args, **kwargs) for h in handlers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.error(
                    "event_handler_failed",
                    event_type=event.value,
                    error=result,
                    exc_info=result,
                )

    def _wrap(self, handler: Handler) -> Handler:
        ref: weakref.ReferenceType[Handler] | None

        if hasattr(handler, "__self__") and hasattr(handler, "__func__"):
            obj = weakref.ref(handler.__self__)
            func = handler.__func__

            async def bound(*args: Any, **kwargs: Any) -> None:
                resolved = obj()
                if resolved is None:
                    return
                await getattr(resolved, func.__name__)(*args, **kwargs)

            return bound

        ref = weakref.ref(handler)

        async def free(*args: Any, **kwargs: Any) -> None:
            resolved = ref()
            if resolved is None:
                return
            await resolved(*args, **kwargs)

        return free

    def _matches(self, wrapped: Handler, target: Handler) -> bool:
        return getattr(wrapped, "__wrapped__", None) is target or wrapped is target

    async def _invoke(
        self,
        handler: Handler,
        event: WsEventType,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await handler(*args, **kwargs)
