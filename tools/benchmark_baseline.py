#!/usr/bin/env python3
"""Repeatable V2-only measurements for the current SDK.

This tool records numbers; it does not assert that any result is good. There
is no MCBEWS/1 path and no warmup that assumes the retired profile.

Scenarios
---------
``fragment``
    Encode one large CJK/emoji request and split it into bounded
    ``MCBEWS2|...`` carrier frames with :func:`fragment`. Reports frames,
    bytes and per-call latency for the configured command budget.
``reassembly``
    Feed those frames through a fresh :class:`ReassemblyPool` per iteration
    and report the latency until the logical message is recovered.
``codec``
    Encode and decode the same logical message per iteration with the public
    v2 codec alone.

Every scenario is repeated ``--runs`` times; the report includes each run plus
the median and p95 of the aggregate metrics, and the observed run-to-run
variation. Run it on an idle machine and keep the environment block with the
numbers.

Usage::

    python tools/benchmark_baseline.py --runs 5 --out /tmp/v2-baseline.json
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pydantic
import structlog
import websockets

import mcbe_ws_sdk
from mcbe_ws_sdk.profiles.mcbews_v2.codec import LIMITS, decode, encode
from mcbe_ws_sdk.profiles.mcbews_v2.transport import ReassemblyPool, fragment

_SAMPLE_GLYPH = "重构基线🙂"
_TRANSPORT_ID = "p.bench.1"
_CONNECTION = "bench"
_SOURCE = "server"


# --------------------------------------------------------------------------- #
# Reusable samples
# --------------------------------------------------------------------------- #


def sample_text(target_utf8_bytes: int) -> str:
    """Return a CJK + emoji sample whose UTF-8 encoding is >= ``target_utf8_bytes``."""
    return _SAMPLE_GLYPH * max(1, target_utf8_bytes // len(_SAMPLE_GLYPH.encode("utf-8")))


def sample_message(text: str) -> dict[str, Any]:
    """Build one valid MCBEWS/2 request around *text*."""
    return {
        "v": "MCBEWS/2",
        "kind": "request",
        "id": _TRANSPORT_ID,
        "method": "bench.echo",
        "params": {"text": text},
    }


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


def measure_fragmentation(*, text_bytes: int, iterations: int, command_budget: int) -> Observation:
    message = sample_message(sample_text(text_bytes))
    overrides = {"command_bytes": command_budget}
    latencies: list[float] = []
    frames = 0
    total_bytes = 0
    for _ in range(iterations):
        started = time.perf_counter()
        payloads = fragment(message, _TRANSPORT_ID, "down", overrides=overrides)
        latencies.append((time.perf_counter() - started) * 1000.0)
        frames += len(payloads)
        total_bytes += sum(len(payload.encode("utf-8")) for payload in payloads)
    elapsed = sum(latencies) / 1000.0
    return Observation(
        name="fragment",
        count=iterations,
        elapsed_s=elapsed,
        latency_ms=latencies,
        extra={
            "input_utf8_bytes": len(encode(message).encode("utf-8")),
            "frames_total": frames,
            "frames_per_message": frames / iterations,
            "output_utf8_bytes": total_bytes,
            "bytes_per_message": total_bytes / iterations,
            "command_line_byte_budget": command_budget,
        },
    )


def measure_reassembly(*, text_bytes: int, iterations: int, command_budget: int) -> Observation:
    message = sample_message(sample_text(text_bytes))
    frames = fragment(message, _TRANSPORT_ID, "down", overrides={"command_bytes": command_budget})
    latencies: list[float] = []
    for _ in range(iterations):
        pool = ReassemblyPool()
        started = time.perf_counter()
        decoded = None
        for frame in frames:
            decoded = pool.receive(_CONNECTION, _SOURCE, frame, "down")
        assert decoded == message
        latencies.append((time.perf_counter() - started) * 1000.0)
    elapsed = sum(latencies) / 1000.0
    return Observation(
        name="reassembly",
        count=iterations,
        elapsed_s=elapsed,
        latency_ms=latencies,
        extra={"frames_per_message": len(frames)},
    )


def measure_codec(*, text_bytes: int, iterations: int) -> Observation:
    message = sample_message(sample_text(text_bytes))
    latencies: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        wire = encode(message)
        assert decode(wire) == message
        latencies.append((time.perf_counter() - started) * 1000.0)
    elapsed = sum(latencies) / 1000.0
    return Observation(
        name="codec",
        count=iterations,
        elapsed_s=elapsed,
        latency_ms=latencies,
        extra={"wire_utf8_bytes": len(encode(message).encode("utf-8"))},
    )


# --------------------------------------------------------------------------- #
# Memory pass
# --------------------------------------------------------------------------- #


def measure_memory(
    scenario: str,
    *,
    text_bytes: int,
    iterations: int,
    command_budget: int,
) -> int:
    tracemalloc.start()
    try:
        if scenario == "fragment":
            measure_fragmentation(
                text_bytes=text_bytes,
                iterations=iterations,
                command_budget=command_budget,
            )
        elif scenario == "reassembly":
            measure_reassembly(
                text_bytes=text_bytes,
                iterations=iterations,
                command_budget=command_budget,
            )
        elif scenario == "codec":
            measure_codec(text_bytes=text_bytes, iterations=iterations)
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
            if statistics.median(throughputs)
            else float("nan")
        ),
        "latency_ms_median": percentile(latencies, 0.5),
        "latency_ms_p95": percentile(latencies, 0.95),
        "latency_samples": len(latencies),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    scenarios: dict[str, Callable[[], Observation]] = {
        "fragment": lambda: measure_fragmentation(
            text_bytes=args.payload_bytes,
            iterations=args.messages,
            command_budget=args.command_budget,
        ),
        "reassembly": lambda: measure_reassembly(
            text_bytes=args.payload_bytes,
            iterations=args.messages,
            command_budget=args.command_budget,
        ),
        "codec": lambda: measure_codec(
            text_bytes=args.payload_bytes,
            iterations=args.messages,
        ),
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

    for name, runner in scenarios.items():
        observations = [runner() for _ in range(args.runs)]
        peak = measure_memory(
            name,
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


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _command_budget(value: str) -> int:
    parsed = _positive(value)
    if parsed > LIMITS["command_bytes"]:
        raise argparse.ArgumentTypeError(
            f"command budget cannot exceed the MCBEWS/2 limit ({LIMITS['command_bytes']})"
        )
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=_positive, default=5, help="repetitions per scenario")
    parser.add_argument("--messages", type=_positive, default=200, help="load items per run")
    parser.add_argument("--payload-bytes", type=_positive, default=4096, help="sample UTF-8 size")
    parser.add_argument(
        "--command-budget",
        type=_command_budget,
        default=LIMITS["command_bytes"],
        help="commandLine byte budget (461 is the MCBEWS/2 empirical default)",
    )
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    args = parser.parse_args(argv)

    report = run(args)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
