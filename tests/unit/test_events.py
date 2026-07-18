"""Tests for the new gateway event bus (WsEventType + EventBus)."""

from __future__ import annotations

import asyncio
import gc

import pytest

from mcbe_ws_sdk.gateway.events import EventBus, WsEventType


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def test_ws_event_type_has_core_events() -> None:
    # PRD §4 lists nine event types (plus CONNECTED/DISCONNECTED).
    names = {member.value for member in WsEventType}
    for required in (
        "connected",
        "disconnected",
        "player_message",
        "bridge_chunk",
        "ui_chat_chunk",
        "ui_chat_reassembled",
        "command_response",
        "raw_inbound",
        "raw_outbound",
    ):
        assert required in names


@pytest.mark.asyncio
async def test_emit_invokes_subscribed_handler(bus: EventBus) -> None:
    seen: list[str] = []

    async def _append(msg: str) -> None:
        seen.append(msg)

    bus.subscribe(WsEventType.PLAYER_MESSAGE, _append, weak=False)
    await bus.emit(WsEventType.PLAYER_MESSAGE, "hi")
    assert seen == ["hi"]


@pytest.mark.asyncio
async def test_emit_concurrent_isolation(bus: EventBus) -> None:
    first: list[str] = []
    second: list[str] = []

    async def a(msg: str) -> None:
        first.append(msg)

    async def b(msg: str) -> None:
        # Fail the second handler and confirm the first still ran.
        second.append(msg)
        raise RuntimeError("boom")

    bus.subscribe(WsEventType.RAW_INBOUND, a, weak=False)
    bus.subscribe(WsEventType.RAW_INBOUND, b, weak=False)

    await bus.emit(WsEventType.RAW_INBOUND, "tick")
    assert first == ["tick"]
    assert second == ["tick"]


@pytest.mark.asyncio
async def test_unsubscribe_removes_handler(bus: EventBus) -> None:
    calls: list[str] = []

    async def h(msg: str) -> None:
        calls.append(msg)

    bus.subscribe(WsEventType.CONNECTED, h, weak=False)
    bus.unsubscribe(WsEventType.CONNECTED, h)
    await bus.emit(WsEventType.CONNECTED, "x")
    assert calls == []


@pytest.mark.asyncio
async def test_weak_subscription_dropped_after_gc(bus: EventBus) -> None:
    calls: list[str] = []

    class Holder:
        async def on(self, msg: str) -> None:
            calls.append(msg)

    holder = Holder()
    bus.subscribe(WsEventType.DISCONNECTED, holder.on)  # weak by default
    await bus.emit(WsEventType.DISCONNECTED, "a")
    assert calls == ["a"]

    del holder
    gc.collect()
    await bus.emit(WsEventType.DISCONNECTED, "b")
    # The weak-ref wrapper silently skips a collected handler.
    assert calls == ["a"]


def test_handler_count(bus: EventBus) -> None:
    async def h() -> None:
        return None

    assert bus.handler_count(WsEventType.UI_CHAT_CHUNK) == 0
    bus.subscribe(WsEventType.UI_CHAT_CHUNK, h, weak=False)
    assert bus.handler_count(WsEventType.UI_CHAT_CHUNK) == 1


@pytest.mark.asyncio
async def test_emit_no_subscribers_is_noop(bus: EventBus) -> None:
    await asyncio.wait_for(bus.emit(WsEventType.RAW_OUTBOUND, "x"), timeout=1.0)
