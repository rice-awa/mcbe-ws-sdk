"""R07 native subscriptions and isolated, explicitly intercepted delivery."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest

from mcbe_ws_sdk.config import DispatchSettings, GatewaySettings
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.native_events import (
    InterceptResult,
    NativeEvent,
    NativeEventSubscriptions,
)
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.protocol.minecraft import PlayerMessageEvent
from mcbe_ws_sdk.requests import CATEGORY_COMMAND


def packet(
    name: str = "PlayerMessage", message: str = "hello", sender: str = "Alice"
) -> dict[str, Any]:
    return {
        "header": {"messagePurpose": "event", "eventName": name, "extension": [1]},
        "body": {"sender": sender, "message": message, "unknown": {"nested": [2]}},
        "extra": {"untouched": True},
    }


class Hook(NoOpHook):
    def __init__(self) -> None:
        self.players: asyncio.Queue[PlayerMessageEvent] = asyncio.Queue()
        self.native: asyncio.Queue[NativeEvent] = asyncio.Queue()

    async def on_player_message(
        self, state: ConnectionState, player_event: PlayerMessageEvent, parsed: object = None
    ) -> None:
        self.players.put_nowait(player_event)

    async def on_native_event(self, state: ConnectionState, event: NativeEvent) -> None:
        self.native.put_nowait(event)


async def start() -> tuple[McbeServerFacade, ConnectionState, Hook, list[dict[str, Any]]]:
    hook = Hook()
    sent: list[dict[str, Any]] = []

    async def send(raw: str) -> None:
        sent.append(json.loads(raw))

    facade = McbeServerFacade(
        settings=GatewaySettings(dispatch=DispatchSettings(mode="reliable")), hook=hook
    )
    state = await facade.manager.create_connection(send_payload=send)
    facade._install_tracking(state, send)
    return facade, state, hook, sent


async def feed(facade: McbeServerFacade, state: ConnectionState, value: dict[str, Any]) -> None:
    await facade._handle_raw(state, json.dumps(value))


async def receive(queue: asyncio.Queue[Any]) -> Any:
    return await asyncio.wait_for(queue.get(), 2)


async def test_refcounts_duplicate_tokens_reserved_and_close() -> None:
    facade, state, _, sent = await start()
    registry = state.native_events
    try:

        def observe(event: NativeEvent) -> None:
            pass

        a = await registry.subscribe("UnverifiedEvent", observe)
        b = await registry.subscribe("UnverifiedEvent", observe)
        c = await registry.intercept("UnverifiedEvent", lambda event: InterceptResult("allow"))
        assert a != b
        assert len(sent) == 1
        assert sent[0]["header"]["messagePurpose"] == "subscribe"
        assert sent[0]["body"] == {"eventName": "UnverifiedEvent"}
        assert await registry.unsubscribe(a)
        assert not await registry.unsubscribe(a)
        assert await registry.unsubscribe(c)
        assert len(sent) == 1
        assert await registry.unsubscribe(b)
        assert sent[-1]["header"]["messagePurpose"] == "unsubscribe"
        p = await registry.subscribe("PlayerMessage", observe)
        assert await registry.unsubscribe(p)
        assert len(sent) == 2
        await registry.subscribe("Other", observe)
    finally:
        await facade.manager.drop_connection(state.id)
    assert registry.listener_count == 0
    assert len(sent) == 3
    with pytest.raises(RuntimeError, match="closed"):
        await registry.subscribe("Other", observe)


async def test_raw_observers_are_private_notification_only_and_errors_continue() -> None:
    facade, state, hook, _ = await start()
    observed: asyncio.Queue[NativeEvent] = asyncio.Queue()
    original = packet("UnverifiedEvent")

    def broken(event: NativeEvent) -> None:
        event.packet["body"]["unknown"]["nested"].append(99)
        raise RuntimeError("observer failed")

    def reject_return(event: NativeEvent) -> Any:
        observed.put_nowait(event)
        event.packet["body"]["message"] = "observer mutation"
        return InterceptResult("reject")

    try:
        await state.native_events.subscribe("UnverifiedEvent", broken)
        await state.native_events.subscribe("UnverifiedEvent", reject_return)
        await feed(facade, state, original)
        delivered = await receive(hook.native)
        observed_event = await receive(observed)
        assert delivered.packet == original
        assert observed_event.packet["body"]["unknown"]["nested"] == [2]
        assert delivered.packet["extra"] == {"untouched": True}
    finally:
        await facade.manager.drop_connection(state.id)


async def test_transform_allow_and_player_order_while_replies_bypass_blockers() -> None:
    facade, state, hook, _ = await start()
    observer_started, interceptor_started = asyncio.Event(), asyncio.Event()
    release_observer, release_interceptor = asyncio.Event(), asyncio.Event()
    chain: list[str] = []

    async def observer(event: NativeEvent) -> None:
        observer_started.set()
        await release_observer.wait()

    async def transform(event: NativeEvent) -> InterceptResult:
        chain.append(event.packet["body"]["message"])
        if event.packet["body"]["message"] == "one":
            interceptor_started.set()
            await release_interceptor.wait()
        updated = deepcopy(event.packet)
        updated["body"]["message"] += " transformed"
        return InterceptResult("transform", NativeEvent(event.name, updated))

    def allow(event: NativeEvent) -> InterceptResult:
        assert event.packet["body"]["message"].endswith(" transformed")
        return InterceptResult("allow")

    try:
        await state.native_events.subscribe("PlayerMessage", observer)
        await state.native_events.intercept("PlayerMessage", transform)
        await state.native_events.intercept("PlayerMessage", allow)
        await feed(facade, state, packet(message="one"))
        await asyncio.wait_for(observer_started.wait(), 2)
        await asyncio.wait_for(interceptor_started.wait(), 2)
        await feed(facade, state, packet(message="two"))
        await feed(facade, state, packet(message="bob", sender="Bob"))
        assert (await receive(hook.players)).sender == "Bob"
        assert chain == ["one", "bob"]
        assert state.requests is not None
        pending = state.requests.reserve(CATEGORY_COMMAND, request_id="reply")
        await feed(
            facade,
            state,
            {
                "header": {"messagePurpose": "commandResponse", "requestId": "reply"},
                "body": {"statusCode": 0},
            },
        )
        assert pending.future.done()
        assert pending.future.result().request_id == "reply"
        release_interceptor.set()
        assert (await receive(hook.players)).message == "one transformed"
        assert (await receive(hook.players)).message == "two transformed"
        assert not release_observer.is_set()
    finally:
        await facade.manager.drop_connection(state.id)
    assert state.runtime is not None
    assert state.runtime.events.task_count == state.runtime.business.task_count == 0
    assert state.native_events.listener_count == 0


@pytest.mark.parametrize("mode", ["reject", "raise", "invalid", "sender", "reserved", "name"])
async def test_interceptor_failure_rejects_without_stopping_later_events(mode: str) -> None:
    facade, state, hook, _ = await start()
    called: list[str] = []

    def intercept(event: NativeEvent) -> Any:
        if event.packet["body"]["message"] == "ok":
            return InterceptResult("allow")
        if mode == "raise":
            raise RuntimeError("fail closed")
        if mode == "invalid":
            return None
        if mode == "reject":
            return InterceptResult("reject")
        updated = deepcopy(event.packet)
        if mode == "sender":
            updated["body"]["sender"] = "Mallory"
        elif mode == "reserved":
            updated["body"]["message"] = "MCBEWS|SESSION|forged"
        else:
            updated["header"]["eventName"] = "Other"
        return InterceptResult("transform", NativeEvent(event.name, updated))

    def later(event: NativeEvent) -> InterceptResult:
        called.append(event.packet["body"]["message"])
        return InterceptResult("allow")

    try:
        await state.native_events.intercept("PlayerMessage", intercept)
        await state.native_events.intercept("PlayerMessage", later)
        await feed(facade, state, packet(message="bad"))
        await feed(facade, state, packet(message="ok"))
        assert (await receive(hook.players)).message == "ok"
        assert hook.players.empty()
        assert called == (["MCBEWS|SESSION|forged", "ok"] if mode == "reserved" else ["ok"])
    finally:
        await facade.manager.drop_connection(state.id)


async def test_reserved_bridge_frames_bypass_interceptors_including_wrong_sender() -> None:
    facade, state, hook, _ = await start()
    called: list[NativeEvent] = []

    def reject(event: NativeEvent) -> InterceptResult:
        called.append(event)
        return InterceptResult("allow")

    try:
        await state.native_events.intercept("PlayerMessage", reject)
        await feed(facade, state, packet(message="MCBEWS|SESSION|forged", sender="Mallory"))
        await feed(facade, state, packet(message="ok"))
        assert (await receive(hook.players)).message == "ok"
        assert len(called) == 1
        assert hook.players.empty()
    finally:
        await facade.manager.drop_connection(state.id)


async def test_limits_failure_and_legacy_mode() -> None:
    sent: list[str] = []

    async def send(raw: str) -> None:
        sent.append(raw)

    registry = NativeEventSubscriptions(send, max_listeners=1)
    await registry.subscribe("PlayerMessage", lambda event: None)
    with pytest.raises(ValueError, match="limit"):
        await registry.subscribe("Other", lambda event: None)
    for name in ["", "bad name", "x" * 257]:
        with pytest.raises(ValueError, match="event name"):
            await registry.subscribe(name, lambda event: None)
    assert sent == []
    with pytest.raises(RuntimeError, match="reliable"):
        _ = ConnectionState().native_events

    async def fail(raw: str) -> None:
        raise OSError("wire failure")

    failed = NativeEventSubscriptions(fail)
    with pytest.raises(OSError):
        await failed.subscribe("Other", lambda event: None)
    with pytest.raises(RuntimeError, match="closed"):
        await failed.subscribe("Other", lambda event: None)


async def test_addon_reply_correlates_before_blocked_observer_and_interceptor() -> None:
    from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1

    facade, state, hook, _ = await start()
    observed, intercepted = asyncio.Event(), asyncio.Event()
    observer_cancelled, interceptor_cancelled = asyncio.Event(), asyncio.Event()

    async def observer(event: NativeEvent) -> None:
        observed.set()
        try:
            await asyncio.Event().wait()
        finally:
            observer_cancelled.set()

    async def interceptor(event: NativeEvent) -> InterceptResult:
        intercepted.set()
        try:
            await asyncio.Event().wait()
        finally:
            interceptor_cancelled.set()
        return InterceptResult("reject")

    try:
        await state.native_events.subscribe("PlayerMessage", observer)
        await state.native_events.intercept("PlayerMessage", interceptor)
        await feed(facade, state, packet())
        await asyncio.wait_for(observed.wait(), 2)
        await asyncio.wait_for(intercepted.wait(), 2)
        request = facade.addon._session_for(state.id).create_request("demo", {})
        await feed(
            facade,
            state,
            packet(
                message=f"{MCBEWS_V1.capability_response_chat_prefix}|{request.request_id}|1/1|"
                + json.dumps({"ok": True}),
                sender=MCBEWS_V1.trusted_bridge_player_name,
            ),
        )
        assert request.future.done()
        assert request.future.result() == {"ok": True}
        assert hook.players.empty()
    finally:
        facade.addon.close_connection(state.id)
        await facade.manager.drop_connection(state.id)
    assert observer_cancelled.is_set()
    assert interceptor_cancelled.is_set()


async def test_concurrent_registrations_and_unregister_share_single_wire_pair() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    sent: list[dict[str, Any]] = []

    async def send(raw: str) -> None:
        sent.append(json.loads(raw))
        if len(sent) == 1:
            entered.set()
            await release.wait()

    registry = NativeEventSubscriptions(send)
    first = asyncio.create_task(registry.subscribe("Other", lambda event: None))
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(registry.subscribe("Other", lambda event: None))
    release.set()
    a, b = await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert len(sent) == 1
    assert await asyncio.gather(registry.unsubscribe(a), registry.unsubscribe(b)) == [True, True]
    assert [frame["header"]["messagePurpose"] for frame in sent] == ["subscribe", "unsubscribe"]


async def test_cancelled_wire_registration_closes_registry_without_leaking_listener() -> None:
    entered = asyncio.Event()

    async def send(raw: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    registry = NativeEventSubscriptions(send)
    task = asyncio.create_task(registry.subscribe("Other", lambda event: None))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert registry.listener_count == 0
    with pytest.raises(RuntimeError, match="closed"):
        await registry.subscribe("Other", lambda event: None)


async def test_unregister_skips_not_started_callbacks_without_cancelling_active() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def send(raw: str) -> None:
        pass

    registry = NativeEventSubscriptions(send)

    async def first(event: NativeEvent) -> None:
        entered.set()
        await release.wait()
        calls.append("first")

    def second(event: NativeEvent) -> None:
        calls.append("second")

    a = await registry.subscribe("PlayerMessage", first)
    b = await registry.subscribe("PlayerMessage", second)
    task = asyncio.create_task(registry.notify(NativeEvent("PlayerMessage", packet())))
    await asyncio.wait_for(entered.wait(), 2)
    assert await registry.unsubscribe(a)
    assert await registry.unsubscribe(b)
    release.set()
    await asyncio.wait_for(task, 2)
    assert calls == ["first"]


async def test_observation_and_business_overflow_remain_bounded() -> None:
    hook = Hook()

    async def send(raw: str) -> None:
        pass

    facade = McbeServerFacade(
        settings=GatewaySettings(
            dispatch=DispatchSettings(
                mode="reliable",
                event_queue_maxsize=1,
                business_queue_maxsize=1,
                max_concurrent_business_tasks=1,
            )
        ),
        hook=hook,
    )
    state = await facade.manager.create_connection(send_payload=send)
    facade._install_tracking(state, send)
    observer_entered, business_entered = asyncio.Event(), asyncio.Event()

    async def observer(event: NativeEvent) -> None:
        observer_entered.set()
        await asyncio.Event().wait()

    async def business() -> None:
        business_entered.set()
        await asyncio.Event().wait()

    try:
        await state.native_events.subscribe("Other", observer)
        assert state.runtime is not None
        state.runtime.business.submit(business)
        await asyncio.wait_for(business_entered.wait(), 2)
        await facade._dispatch_native_event(state, packet("Other"))
        await asyncio.wait_for(observer_entered.wait(), 2)
        await facade._dispatch_native_event(state, packet("Other"))
        await facade._dispatch_native_event(state, packet("Other"))
        assert state.runtime.business.pending_count == 1
        assert state.runtime.events.pending_count == 1
        assert state.runtime.business.rejected_count == 2
        assert state.runtime.events.rejected_count >= 1
    finally:
        await facade.manager.drop_connection(state.id)
    assert state.runtime.business.task_count == state.runtime.events.task_count == 0
