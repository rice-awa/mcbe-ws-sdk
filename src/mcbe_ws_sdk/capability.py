"""Name-based capability registration.

Maps a capability name to one validator and one handler. Dispatch captures the
current entry, runs the validator before the handler, and returns structured
errors for unknown names, invalid input, and handler failures.

Inbound addon capability frames are not routed here yet; that wire path is R12.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias

from mcbe_ws_sdk.errors import CapabilityAlreadyRegisteredError

UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
INVALID_REQUEST = "INVALID_REQUEST"
CAPABILITY_FAILED = "CAPABILITY_FAILED"


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    """Call context passed to capability validators and handlers."""

    caller: str = "internal"
    request_version: int = 1


CapabilityPayload: TypeAlias = Mapping[str, Any]
CapabilityResult: TypeAlias = Mapping[str, Any]
CapabilityHandler: TypeAlias = Callable[
    [str, CapabilityPayload, CapabilityContext],
    CapabilityResult | Awaitable[CapabilityResult],
]
CapabilityValidator: TypeAlias = Callable[[CapabilityPayload, CapabilityContext], str | None]


@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    """Handle returned by :meth:`CapabilityRegistry.register`.

    Pass the exact object back to :meth:`CapabilityRegistry.unregister`. A
    handle from a replaced registration is stale and cannot remove its
    replacement.
    """

    name: str


@dataclass(frozen=True, slots=True)
class _Entry:
    registration: CapabilityRegistration
    handler: CapabilityHandler
    validator: CapabilityValidator | None


def _failure(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


class CapabilityRegistry:
    """Register capability handlers by name and dispatch validated calls."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._entries

    @property
    def names(self) -> tuple[str, ...]:
        """Registered names in registration order."""

        return tuple(self._entries)

    def register(
        self,
        name: str,
        handler: CapabilityHandler,
        *,
        validate: CapabilityValidator | None = None,
        replace: bool = False,
    ) -> CapabilityRegistration:
        """Register *handler* under *name*.

        Raises:
            ValueError: If *name* is empty.
            TypeError: If *handler* is not callable.
            CapabilityAlreadyRegisteredError: If *name* is taken and *replace*
                is false.
        """

        if not isinstance(name, str) or not name.strip():
            raise ValueError("capability name must be a non-empty string")
        if not callable(handler):
            raise TypeError("capability handler must be callable")
        if name in self._entries and not replace:
            raise CapabilityAlreadyRegisteredError(name)
        registration = CapabilityRegistration(name=name)
        self._entries[name] = _Entry(
            registration=registration,
            handler=handler,
            validator=validate,
        )
        return registration

    def unregister(self, registration: CapabilityRegistration) -> bool:
        """Remove *registration* if it is still the current entry for its name."""

        entry = self._entries.get(registration.name)
        if entry is None or entry.registration is not registration:
            return False
        del self._entries[registration.name]
        return True

    async def dispatch(
        self,
        name: str,
        payload: CapabilityPayload,
        *,
        context: CapabilityContext | None = None,
    ) -> dict[str, Any]:
        """Validate and invoke the current handler registered under *name*.

        Returns the handler's mapping on success, or a structured
        ``{"ok": False, "error": {"code": ..., "message": ...}}`` result for an
        unknown name, invalid input, or a validator/handler failure.
        """

        entry = self._entries.get(name)
        if entry is None:
            return _failure(UNSUPPORTED_CAPABILITY, f"unsupported capability: {name}")
        call_context = context or CapabilityContext()
        try:
            if entry.validator is not None:
                message = entry.validator(payload, call_context)
                if message:
                    return _failure(INVALID_REQUEST, message)
            result = entry.handler(name, payload, call_context)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return _failure(CAPABILITY_FAILED, "capability handler failed")
        if not isinstance(result, Mapping):
            return _failure(CAPABILITY_FAILED, "capability handler returned a non-object result")
        return dict(result)


__all__ = [
    "CAPABILITY_FAILED",
    "INVALID_REQUEST",
    "UNSUPPORTED_CAPABILITY",
    "CapabilityContext",
    "CapabilityHandler",
    "CapabilityPayload",
    "CapabilityRegistration",
    "CapabilityRegistry",
    "CapabilityResult",
    "CapabilityValidator",
]
