"""Tests for the new gateway event bus (WsEventType + EventBus)."""

from __future__ import annotations

import asyncio
import gc

import pytest

from mcbe_ws_sdk.gateway.events import EventBus, SubscriptionToken, WsEventType


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
async def test_emit_invokes_strong_synchronous_handler(bus: EventBus) -> None:
    seen: list[str] = []

    def _append(msg: str) -> None:
        seen.append(msg)

    bus.subscribe(WsEventType.PLAYER_MESSAGE, _append, weak=False)
    await bus.emit(WsEventType.PLAYER_MESSAGE, "hi")
    assert seen == ["hi"]


@pytest.mark.asyncio
async def test_emit_rejects_synchronous_handler_return_value(bus: EventBus) -> None:
    def _invalid_handler() -> int:
        return 123

    bus.subscribe(WsEventType.PLAYER_MESSAGE, _invalid_handler, weak=False)

    with pytest.raises(TypeError, match="must return None or an awaitable"):
        await bus.emit(WsEventType.PLAYER_MESSAGE)


@pytest.mark.asyncio
async def test_emit_propagates_handler_exception_after_prior_handlers_run(bus: EventBus) -> None:
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

    with pytest.raises(RuntimeError, match="boom"):
        await bus.emit(WsEventType.RAW_INBOUND, "tick")
    assert first == ["tick"]
    assert second == ["tick"]


@pytest.mark.asyncio
async def test_unsubscribe_removes_exact_subscription(bus: EventBus) -> None:
    calls: list[str] = []

    async def h(msg: str) -> None:
        calls.append(msg)

    token = bus.subscribe(WsEventType.CONNECTED, h, weak=False)
    assert bus.unsubscribe(token) is True
    assert bus.unsubscribe(token) is False
    await bus.emit(WsEventType.CONNECTED, "x")
    assert calls == []


@pytest.mark.asyncio
async def test_weak_subscription_dropped_after_gc(bus: EventBus) -> None:
    calls: list[str] = []

    class Holder:
        async def on(self, msg: str) -> None:
            calls.append(msg)

    holder = Holder()
    token = bus.subscribe(WsEventType.DISCONNECTED, holder.on)  # weak by default
    assert isinstance(token, SubscriptionToken)
    await bus.emit(WsEventType.DISCONNECTED, "a")
    assert calls == ["a"]

    del holder
    gc.collect()
    await bus.emit(WsEventType.DISCONNECTED, "b")
    # The weak-ref wrapper silently skips a collected handler.
    assert calls == ["a"]
    assert bus.handler_count(WsEventType.DISCONNECTED) == 0


@pytest.mark.asyncio
async def test_weak_bound_handler_returns_subscription_token(bus: EventBus) -> None:
    calls: list[str] = []

    class Holder:
        async def on(self, message: str) -> None:
            calls.append(message)

    holder = Holder()
    token = bus.subscribe(WsEventType.CONNECTED, holder.on)

    assert token.event is WsEventType.CONNECTED
    assert bus.unsubscribe(token) is True
    assert bus.unsubscribe(token) is False
    assert bus.handler_count(WsEventType.CONNECTED) == 0

    await bus.emit(WsEventType.CONNECTED, "ignored")
    assert calls == []


def test_same_strong_handler_gets_distinct_tokens(bus: EventBus) -> None:
    async def handler() -> None:
        return None

    first = bus.subscribe(WsEventType.UI_CHAT_CHUNK, handler, weak=False)
    second = bus.subscribe(WsEventType.UI_CHAT_CHUNK, handler, weak=False)

    assert first != second
    assert bus.unsubscribe(first) is True
    assert bus.handler_count(WsEventType.UI_CHAT_CHUNK) == 1


def test_handler_count(bus: EventBus) -> None:
    async def h() -> None:
        return None

    assert bus.handler_count(WsEventType.UI_CHAT_CHUNK) == 0
    bus.subscribe(WsEventType.UI_CHAT_CHUNK, h, weak=False)
    assert bus.handler_count(WsEventType.UI_CHAT_CHUNK) == 1


@pytest.mark.asyncio
async def test_emit_no_subscribers_is_noop(bus: EventBus) -> None:
    await asyncio.wait_for(bus.emit(WsEventType.RAW_OUTBOUND, "x"), timeout=1.0)
