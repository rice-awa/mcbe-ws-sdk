"""R04: every runtime close call observes the same completion.

These tests use explicit events rather than sleeps: the business task only
finishes cancelling when the test releases it, so "the first close is still in
progress" is an observable state rather than a timing guess.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID

import pytest

from mcbe_ws_sdk.config import (
    DispatchSettings,
    FlowControlSettings,
    SendSchedulerSettings,
)
from mcbe_ws_sdk.gateway.runtime import ConnectionRuntime


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    iterations = int(timeout / 0.005)
    for _ in range(iterations):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached before the timeout")


def _runtime(*, max_concurrent: int = 1) -> ConnectionRuntime:
    async def transport(payload: str) -> None:
        return None

    return ConnectionRuntime(
        connection_id=UUID(int=7),
        dispatch=DispatchSettings(
            mode="reliable",
            max_concurrent_business_tasks=max_concurrent,
        ),
        send=SendSchedulerSettings(),
        flow=FlowControlSettings(),
        transport=transport,
    )


def _submit_slow_cancellation(
    runtime: ConnectionRuntime,
    *,
    started: asyncio.Event,
    release: asyncio.Event,
) -> None:
    async def blocker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Hold the cancellation open so the test can observe a close that
            # is genuinely in progress.
            await release.wait()
            raise

    assert runtime.business.submit(blocker, lane="player:Alice", label="blocker") is True


def _assert_fully_reaped(runtime: ConnectionRuntime) -> None:
    assert runtime.is_closed
    assert runtime.business.task_count == 0
    assert runtime.business.active_count == 0
    assert runtime.business.pending_count == 0
    assert runtime.business.lane_count == 0
    assert runtime.events.task_count == 0
    assert runtime.events.pending_count == 0
    assert runtime.requests.pending_count == 0
    assert runtime.scheduler.is_closed
    assert runtime.scheduler.pending_count == 0
    assert runtime._lanes == {}


@pytest.mark.asyncio
async def test_concurrent_close_calls_wait_for_the_same_completion() -> None:
    runtime = _runtime()
    started = asyncio.Event()
    release = asyncio.Event()
    _submit_slow_cancellation(runtime, started=started, release=release)
    await asyncio.wait_for(started.wait(), timeout=2)

    first = asyncio.create_task(runtime.close(reason="first"))
    await _wait_until(lambda: runtime.business.is_closed)

    second = asyncio.create_task(runtime.close(reason="second"))
    for _ in range(5):
        await asyncio.sleep(0)
    # The first close is still waiting on the cancelled business task, so the
    # second close must not report completion yet.
    assert not first.done()
    assert not second.done()
    assert runtime.business.task_count == 1

    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
    _assert_fully_reaped(runtime)


@pytest.mark.asyncio
async def test_cancelling_one_close_caller_does_not_stop_cleanup() -> None:
    runtime = _runtime()
    started = asyncio.Event()
    release = asyncio.Event()
    _submit_slow_cancellation(runtime, started=started, release=release)
    await asyncio.wait_for(started.wait(), timeout=2)

    caller = asyncio.create_task(runtime.close(reason="cancelled caller"))
    await _wait_until(lambda: runtime.business.is_closed)

    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    # Cleanup continues independently of the cancelled caller.
    assert runtime.business.task_count == 1
    assert not runtime.scheduler.is_closed

    release.set()
    await asyncio.wait_for(runtime.close(reason="later caller"), timeout=2)
    _assert_fully_reaped(runtime)
