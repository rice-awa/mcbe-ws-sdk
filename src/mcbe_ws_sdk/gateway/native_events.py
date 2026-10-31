"""Bounded native event subscriptions and explicit business interception."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog

from mcbe_ws_sdk.protocol.minecraft import MinecraftSubscribe, MinecraftUnsubscribe

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NativeEvent:
    """Opaque native event name and complete JSON envelope.

    Each callback receives a private copy. Only PlayerMessage is verified;
    other event names depend on the connected game version.
    """

    name: str
    packet: dict[str, Any]


@dataclass(frozen=True, slots=True)
class InterceptResult:
    """Explicit allow/reject/transform decision for business delivery."""

    action: Literal["allow", "reject", "transform"]
    event: NativeEvent | None = None


@dataclass(frozen=True, slots=True)
class NativeSubscriptionToken:
    """Handle for exactly one connection-local registration."""

    id: UUID


NativeObserver = Callable[[NativeEvent], Awaitable[None] | None]
NativeInterceptor = Callable[[NativeEvent], Awaitable[InterceptResult] | InterceptResult]


@dataclass(slots=True)
class _Listener:
    name: str
    callback: NativeObserver | NativeInterceptor
    interceptor: bool


class NativeEventSubscriptions:
    """Refcount wire event names; never unsubscribe reserved PlayerMessage.

    Duplicate callbacks get distinct tokens. First/last registration sends
    subscribe/unsubscribe. Close discards registrations without wire traffic.
    Send failure or cancellation closes the registry: wire state is uncertain.
    """

    def __init__(
        self,
        send: Callable[[str], Awaitable[None]],
        *,
        max_listeners: int = 256,
        timeout_seconds: float = 30,
    ) -> None:
        if type(max_listeners) is not int or max_listeners <= 0:
            raise ValueError("max_listeners must be a positive integer")
        if not 0 < timeout_seconds < float("inf"):
            raise ValueError("timeout_seconds must be finite and positive")
        self._send = send
        self._max_listeners = max_listeners
        self._timeout_seconds = timeout_seconds
        self._listeners: dict[UUID, _Listener] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def listener_count(self) -> int:
        """Return the number of local registrations."""
        return len(self._listeners)

    async def subscribe(self, event_name: str, observer: NativeObserver) -> NativeSubscriptionToken:
        """Register a notification-only observer, independently of duplicates."""
        return await self._register(event_name, observer, interceptor=False)

    async def intercept(
        self, event_name: str, interceptor: NativeInterceptor
    ) -> NativeSubscriptionToken:
        """Register an ordered business interceptor; replies bypass this chain."""
        return await self._register(event_name, interceptor, interceptor=True)

    async def _register(
        self,
        name: str,
        callback: NativeObserver | NativeInterceptor,
        *,
        interceptor: bool,
    ) -> NativeSubscriptionToken:
        if (
            not name
            or len(name.encode("utf-8")) > 256
            or any(c.isspace() or not c.isprintable() for c in name)
        ):
            raise ValueError("event name must be nonempty, whitespace-free and at most 256 bytes")
        if not callable(callback):
            raise TypeError("callback must be callable")
        async with asyncio.timeout(self._timeout_seconds), self._lock:
            self._ensure_open()
            if len(self._listeners) >= self._max_listeners:
                raise ValueError("native event listener limit reached")
            first = not any(item.name == name for item in self._listeners.values())
            if first and name != "PlayerMessage":
                await self._send_subscription(name, subscribe=True)
            self._ensure_open()
            token = NativeSubscriptionToken(uuid4())
            self._listeners[token.id] = _Listener(name, callback, interceptor)
            return token

    async def unsubscribe(self, token: NativeSubscriptionToken) -> bool:
        """Remove one registration; an unknown or closed token returns False."""
        async with asyncio.timeout(self._timeout_seconds), self._lock:
            item = self._listeners.get(token.id)
            if item is None:
                return False
            last = sum(entry.name == item.name for entry in self._listeners.values()) == 1
            if last and item.name != "PlayerMessage":
                await self._send_subscription(item.name, subscribe=False)
            self._listeners.pop(token.id, None)
            return True

    async def _send_subscription(self, name: str, *, subscribe: bool) -> None:
        frame = MinecraftSubscribe.event(name) if subscribe else MinecraftUnsubscribe.event(name)
        try:
            await self._send(frame.model_dump_json(exclude_none=True))
        except BaseException:
            self.close()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("native event subscriptions closed")

    def close(self) -> None:
        """Release registrations; connection teardown owns wire cleanup."""
        self._closed = True
        self._listeners.clear()

    async def notify(self, event: NativeEvent) -> None:
        """Run observers in registration order; log failures and ignore returns."""
        for key, item in list(self._listeners.items()):
            if item.name != event.name or item.interceptor or key not in self._listeners:
                continue
            try:
                result = item.callback(deepcopy(event))
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("native_event_observer_failed", event_name=event.name)

    async def apply(self, event: NativeEvent) -> NativeEvent | None:
        """Apply ordered interceptors; exceptions/invalid decisions reject delivery."""
        current = deepcopy(event)
        for key, item in list(self._listeners.items()):
            if item.name != event.name or not item.interceptor or key not in self._listeners:
                continue
            try:
                result = item.callback(deepcopy(current))
                if inspect.isawaitable(result):
                    result = await result
                if not isinstance(result, InterceptResult):
                    raise TypeError("interceptor must return InterceptResult")
                if result.action == "reject":
                    return None
                if result.action == "allow" and result.event is None:
                    continue
                if result.action != "transform" or result.event is None:
                    raise ValueError("invalid interceptor decision")
                replacement = result.event
                header = replacement.packet.get("header", {})
                if (
                    replacement.name != event.name
                    or not isinstance(header, dict)
                    or header.get("eventName") != event.name
                    or header.get("messagePurpose") != "event"
                    or not isinstance(replacement.packet.get("body"), dict)
                ):
                    raise ValueError("transform must preserve event envelope and name")
                if event.name == "PlayerMessage" and (
                    replacement.packet["body"].get("sender") != event.packet["body"].get("sender")
                ):
                    raise ValueError("transform must preserve player sender")
                current = deepcopy(replacement)
            except Exception:
                logger.exception("native_event_interceptor_failed", event_name=event.name)
                return None
        return current
