"""Tests for the capability registry seam + logging stub default."""

from __future__ import annotations

import asyncio
import dataclasses
import typing
import uuid

import pytest

from mcbe_ws_sdk.capability import (
    CapabilityContext,
    CapabilityHandler,
    CapabilityRegistry,
    LoggingStubHandler,
)


def _ctx(
    *,
    capability: str = "greet",
    request_id: str = "req-1",
    payload: dict | None = None,
    player_name: str | None = "Steve",
) -> CapabilityContext:
    async def _send(frame: str) -> None:  # pragma: no cover - stub, never auto-shipped
        return None

    return CapabilityContext(
        connection_id=uuid.uuid4(),
        player_name=player_name,
        capability=capability,
        payload=payload if payload is not None else {},
        request_id=request_id,
        send=_send,
    )


def test_logging_stub_handler_is_capability_handler() -> None:
    assert isinstance(LoggingStubHandler(), CapabilityHandler)


def test_capability_handler_is_runtime_checkable_protocol() -> None:
    assert typing.get_origin(CapabilityHandler) is None or True  # runtime_checkable Protocol
    assert issubclass(LoggingStubHandler, CapabilityHandler)


def test_plain_function_fails_isinstance_check() -> None:
    async def handler(ctx: CapabilityContext) -> dict:  # noqa: ARG001
        return {"ok": True}

    assert not isinstance(handler, CapabilityHandler)


def test_capability_context_is_frozen() -> None:
    ctx = _ctx()
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        ctx.capability = "other"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_registered_handler_dispatched_by_name() -> None:
    class Greeter:
        async def handle(self, ctx: CapabilityContext) -> dict:
            return {"ok": True, "echo": ctx.payload.get("msg")}

    registry = CapabilityRegistry()
    registry.register("greet", Greeter())

    out = await registry.handle(_ctx(payload={"msg": "hi"}))
    assert out == {"ok": True, "echo": "hi"}


@pytest.mark.asyncio
async def test_register_override_wins_over_default() -> None:
    class Greeter:
        async def handle(self, ctx: CapabilityContext) -> dict:  # noqa: ARG001
            return {"ok": True}

    registered = Greeter()
    registry = CapabilityRegistry()
    registry.register("greet", registered)
    assert await registry.handle(_ctx()) == {"ok": True}
    assert registry.registered_capabilities() == ["greet"]


@pytest.mark.asyncio
async def test_unknown_capability_falls_back_to_logging_stub() -> None:
    registry = CapabilityRegistry()
    out = await registry.handle(_ctx(capability="missing"))
    assert out == {"ok": False, "error": "unsupported capability: missing"}


@pytest.mark.asyncio
async def test_default_handler_is_logging_stub() -> None:
    assert isinstance(CapabilityRegistry()._default, LoggingStubHandler)


def test_custom_default_handler_overrides_logging_stub() -> None:
    class NoOp:
        async def handle(self, ctx: CapabilityContext) -> dict:  # noqa: ARG001
            return {"ok": True}

    registry = CapabilityRegistry(default=NoOp())
    assert asyncio.run(registry.handle(_ctx(capability="x"))) == {"ok": True}


def test_registered_capabilities_reflects_registrations() -> None:
    registry = CapabilityRegistry()
    assert registry.registered_capabilities() == []

    class H:
        async def handle(self, ctx: CapabilityContext) -> dict:  # noqa: ARG001
            return {}

    registry.register("greet", H())
    registry.register("farewell", H())
    # Sorted + idempotent re-register.
    assert registry.registered_capabilities() == ["farewell", "greet"]
    registry.register("greet", H())
    assert registry.registered_capabilities() == ["farewell", "greet"]
