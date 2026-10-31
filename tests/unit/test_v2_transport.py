"""Cross-runtime fragmentation and bounded lifecycle behavior (R11)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.errors import FrameTooLargeError
from mcbe_ws_sdk.flow.flow_control import FlowControlMiddleware
from mcbe_ws_sdk.gateway.sender import OutboundScheduler
from mcbe_ws_sdk.profiles.mcbews_v2.codec import V2ProtocolError, encode
from mcbe_ws_sdk.profiles.mcbews_v2.transport import (
    COMMAND_PREFIX,
    ReassemblyPool,
    downstream_payloads,
    fragment,
    send_downstream,
)

from .test_v2_codec import typescript


def message(kind: str = "request", length: int = 600) -> dict[str, Any]:
    result: dict[str, Any] = {"v": "MCBEWS/2", "kind": kind, "id": "p.e.1"}
    if kind == "request":
        result.update(method="echo", params="玩家😀|\n" * length)
    else:
        result["result"] = {"data": "玩家😀|\n" * length, "null": None}
    return result


def frame(tid: str = "a.e.1", i: int = 1, n: int = 2, content: str = "x") -> str:
    return f"MCBEWS2|{tid}|{i}/{n}|{content}"


@pytest.mark.parametrize("kind", ["request", "response"])
@pytest.mark.parametrize("direction,tid", [("down", "p.e.2"), ("up", "a.e.2")])
def test_large_cross_language_both_directions(kind: str, direction: str, tid: str) -> None:
    obj = message(kind)
    frames = fragment(obj, tid, direction, overrides={"command_bytes": 180})
    assert len(frames) > 2
    steps = [{"op": "init"}] + [
        {"op": "receive", "frame": f, "direction": direction} for f in reversed(frames)
    ]
    results = typescript(steps)
    assert all(r["code"] == "ok" for r in results)
    assert results[-1]["message"] == obj
    assert results[-1]["stats"] == {"buffers": 0, "bytes": 0, "tombstones": 1}
    tsframes = typescript(
        [
            {
                "op": "fragment",
                "message": obj,
                "tid": tid,
                "direction": direction,
                "overrides": {"command_bytes": 180},
            }
        ]
    )[0]["frames"]
    assert tsframes == frames
    pool = ReassemblyPool()
    complete = [pool.receive("c", "s", f, direction) for f in reversed(tsframes)]
    assert [v for v in complete if v is not None] == [obj]
    assert all(pool.receive("c", "s", f, direction) is None for f in tsframes)
    if direction == "down":
        assert all(len((COMMAND_PREFIX + f).encode()) <= 180 for f in frames)
        payloads = downstream_payloads(obj, tid, overrides={"command_bytes": 180})
        assert all(len(json.loads(p)["body"]["commandLine"].encode()) <= 180 for p in payloads)
    else:
        assert all(len(f) <= 256 and len(f.encode()) <= 1024 for f in frames)
        assert max(len(f.encode()) for f in frames) > 461


def operations(
    steps: list[dict[str, Any]], overrides: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    now = [0.0]
    pool = ReassemblyPool(overrides=overrides, clock_ms=lambda: now[0])
    expected = []
    for step in steps:
        try:
            value = None
            if step["op"] == "receive":
                value = pool.receive(
                    step.get("connection", "c"),
                    step.get("source", "s"),
                    step["frame"],
                    step["direction"],
                )
            elif step["op"] == "time":
                now[0] = step["now"]
                pool.prune()
            elif step["op"] == "cancel":
                pool.cancel(step.get("connection", "c"), step.get("source", "s"), step["tid"])
            elif step["op"] == "close":
                pool.close(step.get("connection", "c"))
            result = {"code": "ok", "stats": pool.stats}
            if step["op"] == "receive":
                result["message"] = value
        except V2ProtocolError as err:
            result = {"code": err.code, "stats": pool.stats}
        expected.append(result)
    actual = typescript([{"op": "init", "overrides": overrides or {}}] + steps)[1:]
    assert actual == expected
    return actual


def recv(text: str, **kwargs: Any) -> dict[str, Any]:
    return {"op": "receive", "frame": text, "direction": "up", **kwargs}


def test_duplicates_conflict_missing_expiry_bad_indexes_and_once() -> None:
    complete = fragment(message(length=1), "a.e.1", "up")
    steps = [
        recv(frame()),
        recv(frame()),
        recv(frame(content="y")),
        recv(frame(i=2)),
        recv(frame("a.e.2")),
        recv(frame("a.e.2", i=2, n=3)),
        recv(frame("a.e.3")),
        {"op": "time", "now": 29999},
        recv(frame("a.e.3")),
        {"op": "time", "now": 30000},
        recv(frame("a.e.3", i=2)),
        recv(frame("a.e.4", i=0)),
        recv(frame("a.e.4", i=3, n=2)),
        recv(frame("a.e.4", n=1025)),
        recv("MCBEWS2|a.e.4|01/2|x"),
        recv(frame("p.e.1")),
        recv(frame("a.e.1\n")),
        recv("MCBEWS2|a.e.4|1/2|"),
        {"op": "close"},
    ] + [recv(f) for f in complete] * 2
    results = operations(steps)
    assert results[2]["code"] == "FRAGMENT_CONFLICT"
    assert results[5]["code"] == "FRAGMENT_CONFLICT"
    assert results[10]["message"] is None
    assert results[-1]["message"] is None
    assert sum(r.get("message") == message(length=1) for r in results) == 1


@pytest.mark.parametrize(
    "overrides,steps",
    [
        ({"message_bytes": 3}, [recv(frame(content="abcd"))]),
        ({"source_bytes": 3}, [recv(frame(content="ab")), recv(frame("a.e.2", content="ab"))]),
        (
            {"connection_bytes": 3},
            [recv(frame(content="ab")), recv(frame("a.e.2", content="ab"), source="s2")],
        ),
        (
            {"global_bytes": 3},
            [recv(frame(content="ab")), recv(frame("a.e.2", content="ab"), connection="c2")],
        ),
        ({"source_buffers": 1}, [recv(frame()), recv(frame("a.e.2"))]),
        ({"connection_buffers": 1}, [recv(frame()), recv(frame("a.e.2"), source="s2")]),
        ({"global_buffers": 1}, [recv(frame()), recv(frame("a.e.2"), connection="c2")]),
        (
            {"tombstones": 1},
            [recv(frame()), {"op": "cancel", "tid": "a.e.1"}, recv(frame("a.e.2"))],
        ),
    ],
)
def test_each_quota_releases_rejected_transfer(
    overrides: dict[str, int], steps: list[dict[str, Any]]
) -> None:
    result = operations(steps + [{"op": "close"}, {"op": "close", "connection": "c2"}], overrides)
    assert result[len(steps) - 1]["code"] == "LIMIT_EXCEEDED"
    assert result[-1]["stats"] == {"buffers": 0, "bytes": 0, "tombstones": 0}


def test_cancel_disconnect_isolation_tombstone_expiry_and_global_pool() -> None:
    result = operations(
        [
            recv(frame()),
            recv(frame(), source="s2"),
            recv(frame(), connection="c2"),
            {"op": "cancel", "tid": "a.e.1"},
            recv(frame(i=2)),
            {"op": "close"},
            {"op": "time", "now": 30000},
            {"op": "close", "connection": "c2"},
        ]
    )
    assert result[2]["stats"]["buffers"] == 3
    assert result[3]["stats"]["buffers"] == 2
    assert result[5]["stats"]["buffers"] == 1
    assert result[-1]["stats"] == {"buffers": 0, "bytes": 0, "tombstones": 0}
    result = operations(
        [recv(frame()), {"op": "time", "now": 30000}, {"op": "time", "now": 60000}, recv(frame())]
    )
    assert result[-1]["stats"] == {"buffers": 1, "bytes": 1, "tombstones": 0}


def test_upstream_independent_scalar_and_byte_limits_and_raw_command_rejection() -> None:
    # Full uplink frame is allowed to exceed 461 bytes while remaining <=256 scalars.
    f = frame(content="😀" * 200)
    assert len(f) < 256 and len(f.encode()) > 461
    result = operations(
        [recv(f), recv(frame("a.e.2", content="x" * 256)), recv(frame("a.e.2", content="\ud800"))]
    )
    assert (
        result[0]["code"] == "ok" and result[1]["code"] == result[2]["code"] == "INVALID_FRAGMENT"
    )
    result = operations([recv(frame(content="😀" * 10))], {"upstream_bytes": 30})
    assert result[0]["code"] == "INVALID_FRAGMENT"
    with pytest.raises(FrameTooLargeError):
        FlowControlMiddleware(FlowControlSettings()).chunk_raw_command("say " + "x" * 1000)
    for cfg in [{"command_bytes": 462}, {"command_bytes": 0}, {"chunks": True}, {"unknown": 1}]:
        with pytest.raises(V2ProtocolError):
            fragment(message(), "p.e.2", "down", overrides=cfg)
        assert (
            typescript(
                [
                    {
                        "op": "fragment",
                        "message": message(),
                        "tid": "p.e.2",
                        "direction": "down",
                        "overrides": cfg,
                    }
                ]
            )[0]["code"]
            == "LIMIT_EXCEEDED"
        )
    with pytest.raises(V2ProtocolError):
        fragment(message(), "p.e.2", "down", overrides={"command_bytes": 20})
    with pytest.raises(V2ProtocolError):
        fragment(message(), "p.e.2", "down", overrides={"chunks": 1})
    with pytest.raises(V2ProtocolError):
        encode(dict(message(), id="pXeX1"))


@pytest.mark.asyncio
async def test_actual_scheduler_send_failure_and_cancel_stop_remaining_frames() -> None:
    sent = []

    async def transport(payload: str) -> None:
        sent.append(payload)

    scheduler = OutboundScheduler(
        connection_id=uuid4(),
        transport=transport,
        max_queue=10,
        max_bytes=10000,
        min_interval=0,
        capacity_timeout=1,
        control_reserve_slots=1,
        control_reserve_bytes=100,
        fairness_quota=2,
    )
    await send_downstream(scheduler, message(length=50), "p.e.2", overrides={"command_bytes": 180})
    assert len(sent) > 1
    assert all(len(json.loads(v)["body"]["commandLine"].encode()) <= 180 for v in sent)
    await scheduler.close()
    count = 0

    async def broken(payload: str) -> None:
        nonlocal count
        count += 1
        raise RuntimeError("broken")

    scheduler = OutboundScheduler(
        connection_id=uuid4(),
        transport=broken,
        max_queue=10,
        max_bytes=10000,
        min_interval=0,
        capacity_timeout=1,
        control_reserve_slots=1,
        control_reserve_bytes=100,
        fairness_quota=2,
    )
    with pytest.raises(RuntimeError, match="broken"):
        await send_downstream(scheduler, message(), "p.e.2")
    assert count == 1
    await scheduler.close()
    started = asyncio.Event()

    async def paused(payload: str) -> None:
        started.set()
        await asyncio.Event().wait()

    scheduler = OutboundScheduler(
        connection_id=uuid4(),
        transport=paused,
        max_queue=10,
        max_bytes=10000,
        min_interval=0,
        capacity_timeout=1,
        control_reserve_slots=1,
        control_reserve_bytes=100,
        fairness_quota=2,
    )
    task = asyncio.create_task(send_downstream(scheduler, message(), "p.e.2"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await scheduler.close()


def test_message_size_exact_boundary_and_fragment_maximum() -> None:
    obj = message(length=0)
    overhead = len(encode(obj).encode())
    obj["params"] = "x" * (65536 - overhead)
    assert len(encode(obj).encode()) == 65536
    # Maximum legal message is exchanged in both runtimes, including lowered budgets.
    for direction, tid in [("down", "p.e.2"), ("up", "a.e.2")]:
        frames = fragment(obj, tid, direction)
        results = typescript(
            [{"op": "init"}]
            + [{"op": "receive", "frame": f, "direction": direction} for f in frames]
        )
        assert results[-1]["message"] == obj
        tsframes = typescript(
            [{"op": "fragment", "message": obj, "tid": tid, "direction": direction}]
        )[0]["frames"]
        assert tsframes == frames
    obj["params"] += "x"
    with pytest.raises(V2ProtocolError, match="LIMIT_EXCEEDED"):
        encode(obj)
    assert typescript([{"message": obj}])[0]["code"] == "LIMIT_EXCEEDED"
    # Allowed total 1024 need not allocate 1024 slots; missing fragments stay bounded.
    results = operations([recv(frame(n=1024)), {"op": "close"}])
    assert results[0]["stats"]["bytes"] == 1
    assert results[1]["stats"]["buffers"] == 0


def test_invalid_complete_message_frees_bytes_and_tombstones() -> None:
    results = operations(
        [recv(frame(n=1, content="bad JSON")), recv(frame(n=1, content="bad JSON"))]
    )
    assert results[0]["code"] == "MALFORMED_JSON"
    assert results[0]["stats"] == {"buffers": 0, "bytes": 0, "tombstones": 1}
    assert results[1]["message"] is None


def test_total_and_duplicate_conflict_discard_without_ttl_refresh() -> None:
    result = operations(
        [
            recv(frame()),
            {"op": "time", "now": 29999},
            recv(frame()),
            {"op": "time", "now": 30000},
            recv(frame(i=2)),
        ]
    )
    assert result[3]["stats"] == {"buffers": 0, "bytes": 0, "tombstones": 1}
    assert result[-1]["message"] is None
