#!/usr/bin/env python3
"""Measure bounded v2 streams with real codecs/carriers and an in-memory link.

Run from an editable SDK checkout. Results describe this synthetic link, not
Bedrock tick throughput. Each scenario uses the same payload and seven repeats.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import time
import tracemalloc
from collections.abc import AsyncIterator
from typing import Any

from mcbe_ws_sdk.capability import CapabilityContext
from mcbe_ws_sdk.profiles.mcbews_v2.streams import StreamError, StreamManager, StreamRegistry
from mcbe_ws_sdk.profiles.mcbews_v2.transport import ReassemblyPool, fragment


async def sample(scenario: str) -> dict[str, Any]:
    registry = StreamRegistry()
    count = 8 if scenario == "burst_large" else 100
    value = {"text": "玩家😀" * (2000 if scenario == "burst_large" else 4)}

    async def produce(_name: str, _params: Any, _context: Any) -> AsyncIterator[Any]:
        for _ in range(count):
            yield value

    registry.register("load", produce)
    frames = {"down": 0, "up": 0}
    logical = {"down": 0, "up": 0}
    peak_items = peak_bytes = 0
    overloaded = asyncio.Event()
    pools = {"down": ReassemblyPool(), "up": ReassemblyPool()}
    endpoints: dict[str, StreamManager] = {}
    sequence = {"down": 0, "up": 0}

    async def deliver(message: dict[str, Any], direction: str) -> None:
        nonlocal peak_items, peak_bytes
        sequence[direction] += 1
        origin = "p" if direction == "down" else "a"
        target = endpoints["producer" if direction == "down" else "consumer"]
        logical[direction] += 1
        for frame in fragment(message, f"{origin}.measure.{sequence[direction]}", direction):
            # Model a yielding transport; this is not consumer flow control.
            await asyncio.sleep(0)
            frames[direction] += 1
            decoded = pools[direction].receive("measure", "source", frame, direction)
            if decoded is not None:
                target.receive(decoded, CapabilityContext(request_version=2))
                if (
                    direction == "up"
                    and decoded["kind"] == "stream_item"
                    and not endpoints["consumer"].incoming
                ):
                    overloaded.set()
                peak_bytes = max(peak_bytes, endpoints["consumer"].buffered_bytes)
                peak_items = max(
                    peak_items, sum(len(s._items) for s in endpoints["consumer"].incoming.values())
                )

    async def down(message: dict[str, Any]) -> None:
        await deliver(message, "down")

    async def up(message: dict[str, Any]) -> None:
        await deliver(message, "up")

    endpoints["consumer"] = StreamManager(down, StreamRegistry())
    endpoints["producer"] = StreamManager(up, registry)
    tracemalloc.start()
    started = time.perf_counter()
    stream = await endpoints["consumer"].open(
        {
            "v": "MCBEWS/2",
            "kind": "stream_open",
            "id": "p.measure.1",
            "method": "load",
            "params": {},
        }
    )
    if scenario == "paused_consumer":
        # Keep the consumer paused until the bound is crossed, independent of CPU speed.
        await asyncio.wait_for(overloaded.wait(), 5)
    received = 0
    first_ms = None
    termination = "end"
    try:
        async for _item in stream:
            if first_ms is None:
                first_ms = (time.perf_counter() - started) * 1000
            received += 1
    except StreamError as exc:
        termination = exc.code
    # Let cancellation reach the producer before closing the synthetic link.
    await endpoints["consumer"].wait_closed()
    await endpoints["producer"].wait_closed()
    for endpoint in endpoints.values():
        endpoint.close()
    await asyncio.gather(*(endpoint.wait_closed() for endpoint in endpoints.values()))
    elapsed_ms = (time.perf_counter() - started) * 1000
    _, memory_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert all(endpoint.buffered_bytes == 0 for endpoint in endpoints.values())
    assert all(not endpoint.producers and not endpoint.controls for endpoint in endpoints.values())
    return {
        "elapsed_ms": elapsed_ms,
        "first_ms": first_ms,
        "received": received,
        "termination": termination,
        "peak_items": peak_items,
        "peak_bytes": peak_bytes,
        "memory_peak_bytes": memory_peak,
        "frames": frames,
        "logical_attempts": logical,
        "extra_credit_commands": 0,
    }


def distribution(values: list[float]) -> dict[str, float]:
    return {
        "median": round(statistics.median(values), 3),
        "p95": round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 3),
    }


async def main(repeats: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repeats": repeats,
        "scenarios": {},
    }
    for scenario in ("continuous", "burst_large", "paused_consumer"):
        samples = [await sample(scenario) for _ in range(repeats)]
        first = [s["first_ms"] for s in samples if s["first_ms"] is not None]
        result["scenarios"][scenario] = {
            "elapsed_ms": distribution([s["elapsed_ms"] for s in samples]),
            "first_ms": distribution(first) if first else None,
            "peak_items": max(s["peak_items"] for s in samples),
            "peak_bytes": max(s["peak_bytes"] for s in samples),
            "memory_peak_bytes": max(s["memory_peak_bytes"] for s in samples),
            "terminations": sum(s["termination"] != "end" for s in samples),
            "samples": samples,
        }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args.repeats)), ensure_ascii=False, indent=2))
