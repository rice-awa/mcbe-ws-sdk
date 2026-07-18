"""Inbound addon capability registry seam.

The gateway asks a :class:`CapabilityRegistry` to resolve an inbound
``scriptevent mcbeai:bridge_request`` to a :class:`CapabilityHandler`, pushing
the application-specific capability behaviour entirely onto the host — precisely
the inversion used by :class:`~mcbe_ws_sdk.gateway.sink.DefaultResponseSink` and
:class:`~mcbe_ws_sdk.gateway.hook.NoOpHook`.

:meth:`CapabilityRegistry.handle` returns the dict that becomes the matching
:class:`~mcbe_ws_sdk.protocol.addon.AddonBridgeResponse` ``payload``; shipping
the framed response back to the client is the *host's* responsibility, not the
registry's. A handler MAY use :attr:`CapabilityContext.send` for side-channel
frames, but the built-in :class:`LoggingStubHandler` deliberately does NOT — it
only reports unsupported capabilities so an unconfigured facade behaves (logs and
returns a safe error payload) instead of raising ``KeyError``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from mcbe_ws_sdk._logging import get_logger

logger = get_logger(__name__)

SendPayload = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class CapabilityContext:
    """All the host needs to service a single capability call.

    ``send`` is the transport frame-send back-reference (mirrors
    :attr:`~mcbe_ws_sdk.gateway.connection.ConnectionState.send_payload`); a
    handler may emit side-channel frames through it, but the default
    :class:`LoggingStubHandler` does NOT auto-ship the response — shipping is the
    host's job.
    """

    connection_id: UUID
    player_name: str | None
    capability: str
    payload: dict[str, Any]
    request_id: str
    send: SendPayload


@runtime_checkable
class CapabilityHandler(Protocol):
    """A handler services one :class:`CapabilityContext` and returns a payload dict."""

    async def handle(self, ctx: CapabilityContext) -> dict[str, Any]: ...


class LoggingStubHandler:
    """Gateway default capability handler — warns and returns a safe error payload.

    Used for any capability with no registered handler so an unconfigured facade
    behaves instead of raising ``KeyError``. It does NOT call ``ctx.send`` —
    shipping a response is the host's responsibility.
    """

    async def handle(self, ctx: CapabilityContext) -> dict[str, Any]:
        logger.warning(
            "capability_unsupported",
            connection_id=str(ctx.connection_id),
            request_id=ctx.request_id,
            capability=ctx.capability,
        )
        return {"ok": False, "error": f"unsupported capability: {ctx.capability}"}


class CapabilityRegistry:
    """Override point that resolves an inbound capability name to a handler.

    * Handlers are registered per capability name via :meth:`register`.
    * :meth:`handle` dispatches by :attr:`CapabilityContext.capability`, falling
      back to the ``default`` handler (a :class:`LoggingStubHandler` unless
      overridden) when nothing is registered.
    * The returned dict is the ``payload`` of the ``AddonBridgeResponse`` — the
      host ships the framed response.
    """

    def __init__(self, default: CapabilityHandler | None = None) -> None:
        self._handlers: dict[str, CapabilityHandler] = {}
        self._default: CapabilityHandler = default if default is not None else LoggingStubHandler()

    def register(self, capability: str, handler: CapabilityHandler) -> None:
        """Register/override the handler for ``capability``."""
        self._handlers[capability] = handler

    async def handle(self, ctx: CapabilityContext) -> dict[str, Any]:
        """Resolve the handler for ``ctx.capability`` and return its payload dict."""
        handler = self._handlers.get(ctx.capability, self._default)
        return await handler.handle(ctx)

    def registered_capabilities(self) -> list[str]:
        """List currently registered capability names (handy for tests/docs)."""
        return sorted(self._handlers.keys())
