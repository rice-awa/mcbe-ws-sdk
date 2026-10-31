"""R03: per-connection outbound scheduler (single worker, priority, capacity)."""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

import pytest

from mcbe_ws_sdk.errors import SendClosedError, SendQueueFullError
from mcbe_ws_sdk.gateway.sender import OutboundScheduler, SendPriority


async def _wait_until(predicate: object, timeout: float = 1.0) -> None:
    """Yield the event loop until ``predicate()`` is true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while not predicate():  # type: ignore[operator]
        if time.monotonic() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0)


async def _drain(*tasks: asyncio.Task[object]) -> list[object]:
    return list(await asyncio.gather(*tasks, return_exceptions=True))


@pytest.mark.asyncio
async def test_r03_1_single_worker_is_only_transport_caller() -> None:
    """Concurrent sends never overlap inside the transport callable."""
    active = 0
    max_active = 0
    calls: list[str] = []

    async def transport(payload: str) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append(payload)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        active -= 1

    scheduler = OutboundScheduler(connection_id=UUID(int=1), transport=transport)
    payloads = [f"frame-{index}" for index in range(8)]
    try:
        await asyncio.gather(*(scheduler.send(payload) for payload in payloads))
        await scheduler.send_from_host("host-frame")
    finally:
        await scheduler.close()

    assert max_active == 1
    assert sorted(calls) == sorted([*payloads, "host-frame"])
    assert scheduler.sent_count == 9
    assert scheduler.pending_count == 0


@pytest.mark.asyncio
async def test_r03_2_aggregate_rate_limit_uses_injected_clock() -> None:
    """Three sequential sends are spaced by ``min_interval`` on the fake clock."""
    now = [0.0]
    observed: list[float] = []

    async def fake_sleep(delay: float) -> None:
        now[0] += delay
        await asyncio.sleep(0)

    async def transport(payload: str) -> None:
        observed.append(now[0])

    scheduler = OutboundScheduler(
        connection_id=UUID(int=2),
        transport=transport,
        min_interval=0.1,
        clock=lambda: now[0],
        sleep=fake_sleep,
    )
    try:
        await scheduler.send("a")
        await scheduler.send("b")
        await scheduler.send("c")
    finally:
        await scheduler.close()

    assert observed == pytest.approx([0.0, 0.1, 0.2])
    assert scheduler.sent_count == 3


@pytest.mark.asyncio
async def test_r03_3_bulk_and_request_rejected_when_full() -> None:
    """A full queue rejects BULK at once and REQUEST after its capacity timeout."""
    started = asyncio.Event()
    gate = asyncio.Event()

    async def transport(payload: str) -> None:
        started.set()
        await gate.wait()

    scheduler = OutboundScheduler(
        connection_id=UUID(int=3),
        transport=transport,
        max_queue=1,
        capacity_timeout=0.01,
    )
    first = asyncio.create_task(scheduler.send("in-flight", priority=SendPriority.BULK))
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)

        with pytest.raises(SendQueueFullError):
            await scheduler.send("bulk-extra", priority=SendPriority.BULK)

        with pytest.raises(SendQueueFullError):
            await scheduler.send("request-extra", priority=SendPriority.REQUEST)

        gate.set()
        errors = await _drain(first)
    finally:
        await scheduler.close()

    assert errors == [None]
    assert scheduler.rejected_count == 2


@pytest.mark.asyncio
async def test_r03_3_close_releases_capacity_waiter() -> None:
    """Senders waiting for capacity are released with SendClosedError on close."""
    started = asyncio.Event()
    gate = asyncio.Event()

    async def transport(payload: str) -> None:
        started.set()
        await gate.wait()

    scheduler = OutboundScheduler(
        connection_id=UUID(int=31),
        transport=transport,
        max_queue=1,
        capacity_timeout=10.0,
    )
    blocker = asyncio.create_task(scheduler.send("in-flight", priority=SendPriority.BULK))
    await asyncio.wait_for(started.wait(), timeout=1.0)

    waiter = asyncio.create_task(scheduler.send("waiting", priority=SendPriority.REQUEST))
    for _ in range(3):
        await asyncio.sleep(0)
    assert not waiter.done()
    assert scheduler.pending_count == 1

    await scheduler.close()
    results = await _drain(blocker, waiter)

    assert len(results) == 2
    assert all(isinstance(result, SendClosedError) for result in results)
    assert scheduler.is_closed


@pytest.mark.asyncio
async def test_r03_4_control_overtakes_queued_bulk_frames() -> None:
    """A CONTROL frame admitted after BULK frames is transmitted first."""
    gate = asyncio.Event()
    calls: list[str] = []

    async def transport(payload: str) -> None:
        calls.append(payload)
        if payload == "first-bulk":
            await gate.wait()

    scheduler = OutboundScheduler(connection_id=UUID(int=4), transport=transport)
    first = asyncio.create_task(scheduler.send("first-bulk", priority=SendPriority.BULK))
    try:
        await _wait_until(lambda: scheduler.pending_count == 1)
        await _wait_until(lambda: calls == ["first-bulk"])

        bulk_a = asyncio.create_task(scheduler.send("bulk-a", priority=SendPriority.BULK))
        bulk_b = asyncio.create_task(scheduler.send("bulk-b", priority=SendPriority.BULK))
        control = asyncio.create_task(scheduler.send("control", priority=SendPriority.CONTROL))
        await _wait_until(lambda: scheduler.pending_count == 4)

        gate.set()
        results = await _drain(first, bulk_a, bulk_b, control)
    finally:
        await scheduler.close()

    assert results == [None, None, None, None]
    assert calls == ["first-bulk", "control", "bulk-a", "bulk-b"]
    assert scheduler.sent_count == 4


@pytest.mark.asyncio
async def test_r03_5_transport_failure_is_propagated_and_isolated() -> None:
    """The owning frame, queued frames and later sends see the failure."""
    started = asyncio.Event()
    gate = asyncio.Event()
    failure = RuntimeError("transport exploded")

    async def broken_transport(payload: str) -> None:
        started.set()
        await gate.wait()
        raise failure

    scheduler = OutboundScheduler(connection_id=UUID(int=5), transport=broken_transport)
    owner = asyncio.create_task(scheduler.send("owner"))
    await asyncio.wait_for(started.wait(), timeout=1.0)

    queued_a = asyncio.create_task(scheduler.send("queued-a", priority=SendPriority.BULK))
    queued_b = asyncio.create_task(scheduler.send("queued-b", priority=SendPriority.BULK))
    await _wait_until(lambda: scheduler.pending_count == 3)

    gate.set()
    results = await _drain(owner, queued_a, queued_b)
    assert len(results) == 3
    assert all(result is failure for result in results)
    assert scheduler.is_closed
    assert scheduler.pending_count == 0

    with pytest.raises(RuntimeError) as caught:
        await scheduler.send("later")
    assert caught.value is failure

    healthy_calls: list[str] = []

    async def healthy_transport(payload: str) -> None:
        healthy_calls.append(payload)

    other = OutboundScheduler(connection_id=UUID(int=51), transport=healthy_transport)
    try:
        await other.send("still-works")
    finally:
        await other.close()

    assert healthy_calls == ["still-works"]
    assert other.sent_count == 1


@pytest.mark.asyncio
async def test_sequential_and_chunked_sends_preserve_order() -> None:
    """Sequential sends and a chunked logical message keep their order."""
    calls: list[str] = []

    async def transport(payload: str) -> None:
        calls.append(payload)

    scheduler = OutboundScheduler(connection_id=UUID(int=6), transport=transport)
    try:
        await scheduler.send("one")
        await scheduler.send("two")
        await scheduler.send("three")
        for chunk in ("chunk-1", "chunk-2", "chunk-3"):
            await scheduler.send(chunk, priority=SendPriority.BULK)
    finally:
        await scheduler.close()

    assert calls == ["one", "two", "three", "chunk-1", "chunk-2", "chunk-3"]


@pytest.mark.asyncio
async def test_byte_capacity_rejects_oversized_frame() -> None:
    """A frame larger than ``max_bytes`` is never admitted."""
    scheduler = OutboundScheduler(
        connection_id=UUID(int=7),
        transport=lambda payload: asyncio.sleep(0),
        max_bytes=10,
        capacity_timeout=0.01,
    )
    try:
        with pytest.raises(SendQueueFullError):
            await scheduler.send("x" * 20, priority=SendPriority.BULK)

        with pytest.raises(SendQueueFullError):
            await scheduler.send("x" * 20, priority=SendPriority.REQUEST)
    finally:
        await scheduler.close()

    assert scheduler.rejected_count == 2
    assert scheduler.pending_bytes == 0
    assert scheduler.pending_count == 0
