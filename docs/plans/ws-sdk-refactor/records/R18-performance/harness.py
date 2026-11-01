"""Repeatable pre-refactor baseline measurements for R00.

This tool intentionally measures the *current* implementation so later tasks
can compare equal workloads on equal environments. It does not assert that any
result is good; it records numbers.

Scenarios
---------
``chunking``
    Encode one large CJK/emoji text response into ``mcbews:text_resp`` command
    frames with :class:`FlowControlMiddleware`. Reports command packets, bytes
    and per-call latency for the configured 461-byte empirical budget.
``inbound``
    Drive N ``PlayerMessage`` frames through the facade's real ``_handle_raw``
    routing path on a simulated connection with a no-op hook. Reports
    throughput, per-message latency (p50/p95) and memory peak.
``outbound``
    Enqueue N ``OutboundText`` messages on a real ``ConnectionManager`` and
    measure queue-to-sink dispatch latency, throughput and memory peak.

Every scenario is repeated ``--runs`` times; the report includes each run plus
the median and p95 of the aggregate metrics, and the observed run-to-run
variation. Run it on an idle machine and keep the environment block with the
numbers.

Usage::

    python tools/benchmark_baseline.py --runs 5 --out /tmp/r00-baseline.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pydantic
import structlog
import websockets

import mcbe_ws_sdk
from mcbe_ws_sdk.config import (
    AddonBridgeSettings,
    FlowControlSettings,
    GatewaySettings,
    WebsocketTransportConfig,
)
from mcbe_ws_sdk.flow import FlowControlMiddleware
from mcbe_ws_sdk.gateway.connection import ConnectionManager, ConnectionState, enqueue_response
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.messages import OutboundText
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink
from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1

_SAMPLE_GLYPH = "重构基线🙂"


# --------------------------------------------------------------------------- #
# Reusable simulated connection / load samples
# --------------------------------------------------------------------------- #


def player_message_frame(sender: str, message: str, *, request_id: str = "bench-1") -> str:
    """Build a ``PlayerMessage`` event frame accepted by the facade."""

    return json.dumps(
        {
            "header": {"eventName": "PlayerMessage", "requestId": request_id},
            "body": {"sender": sender, "message": message},
        }
    )


def sample_text(target_utf8_bytes: int) -> str:
    """Return a CJK + emoji sample whose UTF-8 encoding is >= ``target_utf8_bytes``."""

    unit = _SAMPLE_GLYPH * max(1, target_utf8_bytes // len(_SAMPLE_GLYPH.encode("utf-8")))
    return unit


class SimulatedConnection:
    """A reusable in-memory connection for inbound load measurement."""

    def __init__(self, facade: McbeServerFacade) -> None:
        self.facade = facade
        self.state: ConnectionState | None = None

    async def open(self) -> ConnectionState:
        async def _noop_send(_payload: str) -> None:
            return None

        self.state = await self.facade.manager.create_connection(
            connection_id=uuid4(),
            send_payload=_noop_send,
        )
        return self.state

    async def close(self) -> None:
        if self.state is not None:
            await self.facade.manager.drop_connection(self.state.id)


class _CountingSink(DefaultResponseSink):
    def __init__(self) -> None:
        self.received_at: list[float] = []
        self.done = asyncio.Event()
        self._expected = 0

    async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
        self.received_at.append(time.perf_counter())
        if len(self.received_at) >= self._expected:
            self.done.set()

    def expect(self, count: int) -> None:
        self._expected = count


@dataclass(frozen=True)
class Observation:
    name: str
    count: int
    elapsed_s: float
    latency_ms: list[float]
    extra: dict[str, Any]

    @property
    def throughput_per_s(self) -> float:
        return self.count / self.elapsed_s if self.elapsed_s > 0 else float("inf")


def percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile for a non-empty sample."""

    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #


def _encode_frame(content: str, index: int, total: int) -> str:
    return json.dumps(
        {
            "id": "bench-msg",
            "i": index,
            "n": total,
            "p": "Steve",
            "r": "assistant",
            "c": content,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def measure_chunking(
    *,
    text_bytes: int,
    iterations: int,
    command_budget: int,
) -> Observation:
    flow = FlowControlMiddleware(FlowControlSettings(command_line_byte_budget=command_budget))
    text = sample_text(text_bytes)
    latencies: list[float] = []
    commands = 0
    total_bytes = 0
    for _ in range(iterations):
        started = time.perf_counter()
        payloads = flow.chunk_framed_scriptevent(
            text,
            message_id=MCBEWS_V1.text_response_script_event_id,
            encode_frame=_encode_frame,
            emit_empty=False,
        )
        latencies.append((time.perf_counter() - started) * 1000.0)
        commands += len(payloads)
        total_bytes += sum(len(payload.encode("utf-8")) for payload in payloads)
    elapsed = sum(latencies) / 1000.0
    return Observation(
        name="chunking",
        count=iterations,
        elapsed_s=elapsed,
        latency_ms=latencies,
        extra={
            "input_utf8_bytes": len(text.encode("utf-8")),
            "commands_total": commands,
            "commands_per_message": commands / iterations,
            "output_utf8_bytes": total_bytes,
            "bytes_per_message": total_bytes / iterations,
            "command_line_byte_budget": command_budget,
        },
    )


async def measure_inbound(*, messages: int, text_bytes: int) -> Observation:
    facade = McbeServerFacade(
        settings=GatewaySettings(
            addon=AddonBridgeSettings(),
            websocket=WebsocketTransportConfig(response_queue_maxsize=256),
        ),
        hook=NoOpHook(),
    )
    connection = SimulatedConnection(facade)
    state = await connection.open()
    frame = player_message_frame("Steve", sample_text(text_bytes))
    latencies: list[float] = []
    started = time.perf_counter()
    try:
        for _ in range(messages):
            frame_started = time.perf_counter()
            await facade._handle_raw(state, frame)
            latencies.append((time.perf_counter() - frame_started) * 1000.0)
    finally:
        await connection.close()
    elapsed = time.perf_counter() - started
    return Observation(
        name="inbound",
        count=messages,
        elapsed_s=elapsed,
        latency_ms=latencies,
        extra={"payload_utf8_bytes": len(frame.encode("utf-8"))},
    )


async def measure_outbound(*, messages: int) -> Observation:
    sink = _CountingSink()
    sink.expect(messages)
    manager = ConnectionManager(sink=sink, response_queue_maxsize=messages + 1)

    async def _noop_send(_payload: str) -> None:
        return None

    state = await manager.create_connection(send_payload=_noop_send)
    enqueued_at: list[float] = []
    started = time.perf_counter()
    for index in range(messages):
        enqueued_at.append(time.perf_counter())
        enqueue_response(
            state,
            OutboundText(content=f"baseline message {index}", player_name="Steve"),
        )
    await asyncio.wait_for(sink.done.wait(), timeout=30)
    finished = time.perf_counter()
    # Dispatch latency pairs each message's enqueue timestamp with the sink's
    # receive timestamp in the same (FIFO) order.
    dispatch_ms = [
        (received - sent) * 1000.0
        for sent, received in zip(enqueued_at, sink.received_at, strict=True)
    ]
    elapsed = finished - started
    await manager.drop_connection(state.id)
    return Observation(
        name="outbound",
        count=messages,
        elapsed_s=elapsed,
        latency_ms=dispatch_ms,
        extra={"response_queue_maxsize": messages + 1},
    )


# --------------------------------------------------------------------------- #
# Memory pass
# --------------------------------------------------------------------------- #


async def measure_memory(
    scenario: str,
    *,
    messages: int,
    text_bytes: int,
    iterations: int,
    command_budget: int,
) -> int:
    tracemalloc.start()
    try:
        if scenario == "chunking":
            measure_chunking(
                text_bytes=text_bytes,
                iterations=iterations,
                command_budget=command_budget,
            )
        elif scenario == "inbound":
            await measure_inbound(messages=messages, text_bytes=text_bytes)
        elif scenario == "outbound":
            await measure_outbound(messages=messages)
        else:  # pragma: no cover - argparse constrains the choice
            raise ValueError(scenario)
        _, peak = tracemalloc.get_traced_memory()
        return peak
    finally:
        tracemalloc.stop()


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _summarise(observations: list[Observation]) -> dict[str, Any]:
    latencies = [value for observation in observations for value in observation.latency_ms]
    throughputs = [observation.throughput_per_s for observation in observations]
    return {
        "runs": len(observations),
        "throughput_per_s_median": statistics.median(throughputs),
        "throughput_per_s_min": min(throughputs),
        "throughput_per_s_max": max(throughputs),
        "throughput_run_to_run_variation_pct": (
            (max(throughputs) - min(throughputs)) / statistics.median(throughputs) * 100.0
            if statistics.median(throughputs) else float("nan")
        ),
        "latency_ms_median": percentile(latencies, 0.5),
        "latency_ms_p95": percentile(latencies, 0.95),
        "latency_samples": len(latencies),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    scenario_runners: dict[str, Callable[[], Awaitable[Observation] | Observation]] = {
        "chunking": lambda: measure_chunking(
            text_bytes=args.payload_bytes,
            iterations=args.messages,
            command_budget=args.command_budget,
        ),
        "inbound": lambda: measure_inbound(
            messages=args.messages,
            text_bytes=args.payload_bytes,
        ),
        "outbound": lambda: measure_outbound(messages=args.messages),
    }

    report: dict[str, Any] = {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "mcbe_ws_sdk": mcbe_ws_sdk.__version__,
            "pydantic": pydantic.VERSION,
            "websockets": websockets.__version__,
            "structlog": structlog.__version__,
        },
        "parameters": {
            "runs": args.runs,
            "messages_per_run": args.messages,
            "payload_bytes": args.payload_bytes,
            "command_line_byte_budget": args.command_budget,
            "memory_method": "tracemalloc peak of one extra identical run per scenario",
        },
        "scenarios": {},
    }

    for name, runner in scenario_runners.items():
        observations: list[Observation] = []
        for _ in range(args.runs):
            result = runner()
            observations.append(await result if asyncio.iscoroutine(result) else result)
        peak = await measure_memory(
            name,
            messages=args.messages,
            text_bytes=args.payload_bytes,
            iterations=args.messages,
            command_budget=args.command_budget,
        )
        report["scenarios"][name] = {
            "summary": _summarise(observations),
            "memory_peak_bytes": peak,
            "extra": observations[0].extra,
            "runs": [
                {
                    "count": observation.count,
                    "elapsed_s": observation.elapsed_s,
                    "throughput_per_s": observation.throughput_per_s,
                    "latency_ms_median": percentile(observation.latency_ms, 0.5),
                    "latency_ms_p95": percentile(observation.latency_ms, 0.95),
                }
                for observation in observations
            ],
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help="repetitions per scenario")
    parser.add_argument("--messages", type=int, default=200, help="load items per run")
    parser.add_argument("--payload-bytes", type=int, default=4096, help="sample UTF-8 size")
    parser.add_argument(
        "--command-budget",
        type=int,
        default=461,
        help="commandLine byte budget (461 is the empirical default)",
    )
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.messages < 1 or args.payload_bytes < 1:
        parser.error("--runs, --messages and --payload-bytes must be positive")

    report = asyncio.run(run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
