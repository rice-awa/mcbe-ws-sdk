"""R01: bounded business-task dispatch.

These tests drive the dispatcher directly with controllable events. No timing
guesses decide the outcome: every ordering assertion is gated on an event that
only fires after the previous step actually ran.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from mcbe_ws_sdk.gateway.dispatch import BusinessDispatcher


def _dispatcher(
    *,
    max_queue: int = 8,
    max_concurrent: int = 8,
) -> BusinessDispatcher:
    return BusinessDispatcher(
        connection_id=UUID(int=1),
        max_queue=max_queue,
        max_concurrent=max_concurrent,
        name="test",
    )


async def test_same_lane_runs_in_fifo_order() -> None:
    dispatcher = _dispatcher()
    order: list[int] = []
    release = asyncio.Event()

    async def first() -> None:
        order.append(1)
        await release.wait()
        order.append(2)

    async def second() -> None:
        order.append(3)

    assert dispatcher.submit(first, lane="player:Alice", label="first") is True
    assert dispatcher.submit(second, lane="player:Alice", label="second") is True
    # Give the first task a chance to start; second must not have run yet.
    for _ in range(20):
        await asyncio.sleep(0)
        if order == [1]:
            break
    assert order == [1]

    release.set()
    await asyncio.wait_for(_wait_idle(dispatcher), timeout=2)
    assert order == [1, 2, 3]
    await dispatcher.close()


async def test_different_lanes_run_concurrently() -> None:
    dispatcher = _dispatcher()
    a_started = asyncio.Event()
    b_started = asyncio.Event()
    release_a = asyncio.Event()
    completed: list[str] = []

    async def player_a() -> None:
        a_started.set()
        await release_a.wait()
        completed.append("a")

    async def player_b() -> None:
        b_started.set()
        completed.append("b")

    dispatcher.submit(player_a, lane="player:Alice", label="a")
    dispatcher.submit(player_b, lane="player:Bob", label="b")

    await asyncio.wait_for(a_started.wait(), timeout=2)
    await asyncio.wait_for(b_started.wait(), timeout=2)
    assert completed == ["b"]

    release_a.set()
    await asyncio.wait_for(_wait_idle(dispatcher), timeout=2)
    assert completed == ["b", "a"]
    await dispatcher.close()


async def test_paused_lane_does_not_consume_concurrency_slot() -> None:
    """Queued work for a paused lane must not starve a different runnable lane.

    With ``max_concurrent=2`` Alice's first task holds one slot while her second
    task waits for her lane. Bob's task must still get the second slot instead
    of Alice's *queued* task taking it just to block on her lane.
    """
    dispatcher = _dispatcher(max_queue=8, max_concurrent=2)
    a_started = asyncio.Event()
    a_release = asyncio.Event()
    a_second_ran = asyncio.Event()
    b_ran = asyncio.Event()

    async def first_a() -> None:
        a_started.set()
        await a_release.wait()

    async def second_a() -> None:
        a_second_ran.set()

    async def first_b() -> None:
        b_ran.set()

    assert dispatcher.submit(first_a, lane="player:Alice", label="a1") is True
    await asyncio.wait_for(a_started.wait(), timeout=2)

    assert dispatcher.submit(second_a, lane="player:Alice", label="a2") is True
    assert dispatcher.submit(first_b, lane="player:Bob", label="b1") is True

    # Bob runs although Alice's lane is still blocked and her queued item is
    # older: the queued item is not holding a concurrency slot.
    await asyncio.wait_for(b_ran.wait(), timeout=2)
    assert not a_second_ran.is_set()

    a_release.set()
    await asyncio.wait_for(_wait_idle(dispatcher), timeout=2)
    assert a_second_ran.is_set()
    await dispatcher.close()


async def test_ready_lanes_run_round_robin() -> None:
    """Lanes waiting for a single slot are served in submission order."""
    dispatcher = _dispatcher(max_queue=8, max_concurrent=1)
    order: list[str] = []
    gate = asyncio.Event()
    first_started = asyncio.Event()

    def make(name: str, *, block: bool = False):
        async def run() -> None:
            order.append(name)
            if block:
                first_started.set()
                await gate.wait()

        return run

    dispatcher.submit(make("a", block=True), lane="lane:a")
    await asyncio.wait_for(first_started.wait(), timeout=2)
    dispatcher.submit(make("b"), lane="lane:b")
    dispatcher.submit(make("c"), lane="lane:c")

    gate.set()
    await asyncio.wait_for(_wait_idle(dispatcher), timeout=2)
    assert order == ["a", "b", "c"]
    await dispatcher.close()


async def test_queue_full_rejects_without_blocking() -> None:
    dispatcher = _dispatcher(max_queue=1, max_concurrent=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocker() -> None:
        started.set()
        await release.wait()

    assert dispatcher.submit(blocker, lane="player:Alice") is True
    await asyncio.wait_for(started.wait(), timeout=2)
    # The supervisor has dequeued the blocker; one slot remains in the queue.
    assert dispatcher.submit(blocker, lane="player:Bob") is True
    # The queue is full now: rejection must be immediate and explicit.
    assert dispatcher.submit(blocker, lane="player:Carol") is False
    assert dispatcher.rejected_count == 1

    release.set()
    await asyncio.wait_for(_wait_idle(dispatcher), timeout=2)
    await dispatcher.close()


async def test_submit_after_close_is_rejected() -> None:
    dispatcher = _dispatcher()
    await dispatcher.close()

    async def work() -> None:
        raise AssertionError("must not run")

    assert dispatcher.submit(work) is False
    assert dispatcher.is_closed
    assert dispatcher.rejected_count == 1


async def test_close_cancels_tasks_and_clears_state() -> None:
    dispatcher = _dispatcher()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    dispatcher.submit(blocker, lane="player:Alice")
    await asyncio.wait_for(started.wait(), timeout=2)

    await dispatcher.close()

    assert cancelled.is_set()
    assert dispatcher.is_closed
    assert dispatcher.task_count == 0
    assert dispatcher.active_count == 0
    assert dispatcher.pending_count == 0
    assert dispatcher.lane_count == 0


async def test_task_failure_is_isolated_and_counted() -> None:
    dispatcher = _dispatcher()
    done = asyncio.Event()

    async def boom() -> None:
        raise LookupError("boom")

    async def later() -> None:
        done.set()

    dispatcher.submit(boom, lane="player:Alice")
    dispatcher.submit(later, lane="player:Bob")
    await asyncio.wait_for(done.wait(), timeout=2)
    await asyncio.wait_for(_wait_idle(dispatcher), timeout=2)

    assert dispatcher.failed_count == 1
    assert dispatcher.completed_count == 1
    await dispatcher.close()


async def test_max_concurrent_bounds_running_tasks() -> None:
    dispatcher = _dispatcher(max_queue=8, max_concurrent=1)
    first_started = asyncio.Event()
    release = asyncio.Event()
    running_peak = 0
    running = 0

    async def tracked() -> None:
        nonlocal running, running_peak
        running += 1
        running_peak = max(running_peak, running)
        try:
            if not first_started.is_set():
                first_started.set()
                await release.wait()
        finally:
            running -= 1

    for index in range(3):
        dispatcher.submit(tracked, lane=f"player:{index}")

    await asyncio.wait_for(first_started.wait(), timeout=2)
    await asyncio.sleep(0.02)
    assert running_peak == 1

    release.set()
    await asyncio.wait_for(_wait_idle(dispatcher), timeout=2)
    assert running_peak == 1
    await dispatcher.close()


async def _wait_idle(dispatcher: BusinessDispatcher) -> None:
    while dispatcher.pending_count or dispatcher.active_count:
        await asyncio.sleep(0)


@pytest.mark.parametrize("value", [0, -1])
def test_invalid_capacity_rejected(value: int) -> None:
    with pytest.raises(ValueError):
        BusinessDispatcher(connection_id=UUID(int=1), max_queue=value)
    with pytest.raises(ValueError):
        BusinessDispatcher(connection_id=UUID(int=1), max_concurrent=value)
