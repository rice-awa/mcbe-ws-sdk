"""WebSocket gateway event bus."""

from __future__ import annotations

import inspect
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

Handler = Callable[..., Awaitable[None] | None]


class WsEventType(Enum):
    """Events the connection lifetime and protocol handler can emit."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    PLAYER_MESSAGE = "player_message"
    BRIDGE_CHUNK = "bridge_chunk"
    UI_CHAT_CHUNK = "ui_chat_chunk"
    UI_CHAT_REASSEMBLED = "ui_chat_reassembled"
    COMMAND_RESPONSE = "command_response"
    ERROR = "error"
    RAW_INBOUND = "raw_inbound"
    RAW_OUTBOUND = "raw_outbound"


@dataclass(frozen=True, slots=True)
class SubscriptionToken:
    """An opaque handle for one event-bus registration."""

    event: WsEventType
    id: UUID


@dataclass(slots=True)
class _Subscription:
    token: SubscriptionToken
    weak_handler: weakref.ReferenceType[Handler] | weakref.WeakMethod[Handler] | None
    strong_handler: Handler | None

    @classmethod
    def from_handler(
        cls,
        token: SubscriptionToken,
        handler: Handler,
        weak: bool,
    ) -> _Subscription:
        if not weak:
            return cls(token=token, weak_handler=None, strong_handler=handler)
        if hasattr(handler, "__self__") and hasattr(handler, "__func__"):
            return cls(
                token=token,
                weak_handler=weakref.WeakMethod(handler),
                strong_handler=None,
            )
        return cls(token=token, weak_handler=weakref.ref(handler), strong_handler=None)

    def resolve(self) -> Handler | None:
        if self.strong_handler is not None:
            return self.strong_handler
        if self.weak_handler is None:
            return None
        return self.weak_handler()


class EventBus:
    """A typed in-process event bus keyed by :class:`WsEventType`."""

    def __init__(self) -> None:
        self._subscribers: dict[WsEventType, dict[UUID, _Subscription]] = {
            event: {} for event in WsEventType
        }

    def subscribe(
        self,
        event: WsEventType,
        handler: Handler,
        *,
        weak: bool = True,
    ) -> SubscriptionToken:
        """Register ``handler`` and return a token for this registration."""
        token = SubscriptionToken(event=event, id=uuid4())
        self._subscribers[event][token.id] = _Subscription.from_handler(token, handler, weak)
        return token

    def unsubscribe(self, token: SubscriptionToken) -> bool:
        """Remove exactly the registration identified by ``token``."""
        return self._subscribers[token.event].pop(token.id, None) is not None

    def handler_count(self, event: WsEventType) -> int:
        """Return the number of live registrations for ``event``."""
        self._prune_dead(event)
        return len(self._subscribers[event])

    async def emit(self, event: WsEventType, *args: Any, **kwargs: Any) -> None:
        """Await live handlers in subscription order."""
        subscribers = self._subscribers[event]
        for token_id in list(subscribers):
            subscription = subscribers.get(token_id)
            if subscription is None:
                continue
            handler = subscription.resolve()
            if handler is None:
                subscribers.pop(token_id, None)
                continue
            result = handler(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
            elif result is not None:
                handler_name = getattr(handler, "__qualname__", repr(handler))
                raise TypeError(
                    f"Event handler {handler_name} for {event.value!r} must return None "
                    f"or an awaitable, got {type(result).__name__}"
                )

    def _prune_dead(self, event: WsEventType) -> None:
        subscribers = self._subscribers[event]
        for token_id, subscription in list(subscribers.items()):
            if subscription.resolve() is None:
                subscribers.pop(token_id, None)
