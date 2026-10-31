"""Behaviour tests for the name-based capability registry (R05)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from mcbe_ws_sdk.capability import (
    CAPABILITY_FAILED,
    INVALID_REQUEST,
    UNSUPPORTED_CAPABILITY,
    CapabilityRegistry,
)
from mcbe_ws_sdk.errors import CapabilityAlreadyRegisteredError


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, "payload": payload}


async def test_custom_capability_coexists_with_existing_registrations() -> None:
    registry = CapabilityRegistry()
    registry.register("builtin", lambda name, payload, context: _ok(name=name))
    registry.register("custom", lambda name, payload, context: _ok(value=payload["value"]))

    builtin = await registry.dispatch("builtin", {})
    custom = await registry.dispatch("custom", {"value": 7})

    assert builtin == {"ok": True, "payload": {"name": "builtin"}}
    assert custom == {"ok": True, "payload": {"value": 7}}
    assert registry.names == ("builtin", "custom")


async def test_duplicate_name_rejects_unless_replaced() -> None:
    registry = CapabilityRegistry()
    original = registry.register("demo", lambda name, payload, context: _ok(source="original"))

    with pytest.raises(CapabilityAlreadyRegisteredError):
        registry.register("demo", lambda name, payload, context: _ok(source="duplicate"))

    replacement = registry.register(
        "demo",
        lambda name, payload, context: _ok(source="replacement"),
        replace=True,
    )
    assert original is not replacement
    assert await registry.dispatch("demo", {}) == {"ok": True, "payload": {"source": "replacement"}}


async def test_stale_handle_cannot_remove_replacement() -> None:
    registry = CapabilityRegistry()
    stale = registry.register("demo", lambda name, payload, context: _ok(source="first"))
    current = registry.register(
        "demo",
        lambda name, payload, context: _ok(source="second"),
        replace=True,
    )

    assert registry.unregister(stale) is False
    assert await registry.dispatch("demo", {}) == {"ok": True, "payload": {"source": "second"}}
    assert registry.unregister(current) is True
    assert "demo" not in registry


async def test_unregister_during_inflight_dispatch_is_deterministic() -> None:
    registry = CapabilityRegistry()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(name: str, payload: Mapping[str, Any], context: Any) -> dict[str, Any]:
        started.set()
        await release.wait()
        return _ok(finished=True)

    registration = registry.register("slow", slow)
    task = asyncio.create_task(registry.dispatch("slow", {}))
    await started.wait()

    assert registry.unregister(registration) is True
    release.set()

    assert await task == {"ok": True, "payload": {"finished": True}}
    assert await registry.dispatch("slow", {}) == {
        "ok": False,
        "error": {"code": UNSUPPORTED_CAPABILITY, "message": "unsupported capability: slow"},
    }


async def test_unknown_invalid_and_failing_handlers_are_isolated() -> None:
    registry = CapabilityRegistry()

    def validate(payload: Mapping[str, Any], context: Any) -> str | None:
        return None if isinstance(payload.get("count"), int) else "count must be an integer"

    async def failing(name: str, payload: Mapping[str, Any], context: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    registry.register(
        "validated",
        lambda name, payload, context: _ok(count=payload["count"]),
        validate=validate,
    )
    registry.register("failing", failing)
    registry.register("healthy", lambda name, payload, context: _ok(alive=True))

    assert await registry.dispatch("missing", {}) == {
        "ok": False,
        "error": {"code": UNSUPPORTED_CAPABILITY, "message": "unsupported capability: missing"},
    }
    assert await registry.dispatch("validated", {"count": "nope"}) == {
        "ok": False,
        "error": {"code": INVALID_REQUEST, "message": "count must be an integer"},
    }
    assert await registry.dispatch("failing", {}) == {
        "ok": False,
        "error": {"code": CAPABILITY_FAILED, "message": "capability handler failed"},
    }
    assert await registry.dispatch("healthy", {}) == {"ok": True, "payload": {"alive": True}}
    assert await registry.dispatch("validated", {"count": 2}) == {
        "ok": True,
        "payload": {"count": 2},
    }
