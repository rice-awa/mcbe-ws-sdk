"""Behavioral regressions from the R09–R11 independent spec review."""

import pytest

from mcbe_ws_sdk.profiles.mcbews_v2.codec import V2ProtocolError, decode
from mcbe_ws_sdk.profiles.mcbews_v2.transport import ReassemblyPool, fragment

from .test_v2_codec import typescript

MESSAGE = {"v": "MCBEWS/2", "kind": "notification", "method": "echo", "params": "x" * 500}


def test_one_receive_clock_sample_expiry_replay_and_capacity_recovery() -> None:
    now = [0.0]
    calls = [0]
    advancing = [False]

    def clock() -> float:
        if advancing[0]:
            value = 29999 + calls[0]
            calls[0] += 1
            return value
        return now[0]

    pool = ReassemblyPool(overrides={"tombstones": 2}, clock_ms=clock)
    frames = fragment(MESSAGE, "a.e.1", "up")
    pool.receive("c", "s", frames[0], "up")
    advancing[0] = True
    pool.receive("c", "s", "MCBEWS2|a.e.2|1/2|x", "up")
    assert calls[0] == 1
    advancing[0] = False
    now[0] = 30000
    pool.prune()
    assert all(pool.receive("c", "s", f, "up") is None for f in frames)
    assert pool.stats["tombstones"] == 1
    now[0] = 60000
    pool.prune()
    now[0] = 90000
    pool.prune()
    for tid in ["a.e.3", "a.e.4"]:
        values = [pool.receive("c", "s", f, "up") for f in fragment(MESSAGE, tid, "up")]
        assert [value for value in values if value is not None] == [MESSAGE]


@pytest.mark.parametrize("bad", ["x" * 300, "\ud800"])
def test_invalid_continuation_releases_and_suppresses(bad: str) -> None:
    frames = fragment(MESSAGE, "a.e.1", "up")
    pool = ReassemblyPool()
    pool.receive("c", "s", frames[0], "up")
    invalid = "MCBEWS2|a.e.1|2/3|" + bad
    with pytest.raises(V2ProtocolError, match="INVALID_FRAGMENT"):
        pool.receive("c", "s", invalid, "up")
    assert pool.stats == {"buffers": 0, "bytes": 0, "tombstones": 1}
    assert all(pool.receive("c", "s", f, "up") is None for f in frames)
    results = typescript(
        [{"op": "init"}, recv(frames[0]), recv(invalid)] + [recv(f) for f in frames]
    )
    assert results[2] == {"code": "INVALID_FRAGMENT", "stats": pool.stats}
    assert all(r["message"] is None for r in results[3:])


def recv(frame: str) -> dict:
    return {"op": "receive", "frame": frame, "direction": "up"}


@pytest.mark.parametrize("direction,tid", [("up", "a.e.1"), ("down", "p.e.1")])
@pytest.mark.parametrize("tail", ["2/3" + "x" * 500, "2/\ud800", "bad-index"])
def test_missing_content_separator_releases_and_suppresses(
    direction: str, tid: str, tail: str
) -> None:
    frames = fragment(MESSAGE, tid, direction)
    pool = ReassemblyPool()
    assert pool.receive("c", "s", frames[0], direction) is None
    assert pool.stats["bytes"] > 0
    invalid = f"MCBEWS2|{tid}|{tail}"
    with pytest.raises(V2ProtocolError, match="INVALID_FRAGMENT"):
        pool.receive("c", "s", invalid, direction)
    assert pool.stats == {"buffers": 0, "bytes": 0, "tombstones": 1}
    assert all(pool.receive("c", "s", f, direction) is None for f in frames[1:])
    results = typescript(
        [{"op": "init"}]
        + [dict(recv(f), direction=direction) for f in [frames[0], invalid, *frames[1:]]]
    )
    assert results[2] == {"code": "INVALID_FRAGMENT", "stats": pool.stats}
    assert all(r["message"] is None for r in results[3:])


@pytest.mark.parametrize(
    "direction,tid,other", [("up", "a.e.1", "p.e.1"), ("down", "p.e.1", "a.e.1")]
)
def test_unattributable_or_unknown_invalid_frames_do_not_allocate(
    direction: str, tid: str, other: str
) -> None:
    invalid = [
        f"wrong|{tid}|bad-index",
        f"MCBEWS2|{tid}",
        f"MCBEWS2|{other}|bad-index",
        f"MCBEWS2|{tid}!|bad-index",
        f"MCBEWS2|{tid[:-1]}2|bad-index",
        f"MCBEWS2|{tid[:-1]}2|2/3" + "x" * 500,
        f"MCBEWS2|{tid[:-1]}2|2/\ud800",
    ]
    pool = ReassemblyPool(overrides={"tombstones": 1})
    operations = [{"op": "init", "overrides": {"tombstones": 1}}]
    for frame in invalid:
        with pytest.raises(V2ProtocolError, match="INVALID_FRAGMENT"):
            pool.receive("c", "s", frame, direction)
        assert pool.stats == {"buffers": 0, "bytes": 0, "tombstones": 0}
        operations.append(dict(recv(frame), direction=direction))
    frames = fragment(MESSAGE, tid, direction)
    assert pool.receive("c", "s", frames[0], direction) is None
    active = dict(pool.stats)
    operations.append(dict(recv(frames[0]), direction=direction))
    for frame in invalid:
        with pytest.raises(V2ProtocolError, match="INVALID_FRAGMENT"):
            pool.receive("c", "s", frame, direction)
        assert pool.stats == active
        operations.append(dict(recv(frame), direction=direction))
    assert [v for f in frames[1:] if (v := pool.receive("c", "s", f, direction))] == [MESSAGE]
    operations.extend(dict(recv(f), direction=direction) for f in frames[1:])
    results = typescript(operations)
    assert all(
        r == {"code": "INVALID_FRAGMENT", "stats": {"buffers": 0, "bytes": 0, "tombstones": 0}}
        for r in results[1 : 1 + len(invalid)]
    )
    assert all(
        r == {"code": "INVALID_FRAGMENT", "stats": active}
        for r in results[2 + len(invalid) : 2 + 2 * len(invalid)]
    )
    assert results[-1]["message"] == MESSAGE
    assert results[-1]["stats"] == pool.stats


@pytest.mark.parametrize(
    "overrides,extra",
    [({"metadata_bytes": 1}, {"meta": {"x": "y"}}), ({"depth": 1}, {"params": {"nested": [0]}})],
)
def test_lowered_logical_limits_send_receive(overrides: dict, extra: dict) -> None:
    message = dict(MESSAGE, **extra)
    with pytest.raises(V2ProtocolError, match="LIMIT_EXCEEDED"):
        fragment(message, "a.e.1", "up", overrides=overrides)
    frames = fragment(message, "a.e.1", "up")
    pool = ReassemblyPool(overrides=overrides)
    for f in frames[:-1]:
        assert pool.receive("c", "s", f, "up") is None
    with pytest.raises(V2ProtocolError, match="LIMIT_EXCEEDED"):
        pool.receive("c", "s", frames[-1], "up")
    assert pool.stats == {"buffers": 0, "bytes": 0, "tombstones": 1}
    results = typescript(
        [
            {
                "op": "fragment",
                "message": message,
                "tid": "a.e.1",
                "direction": "up",
                "overrides": overrides,
            },
            {"op": "init", "overrides": overrides},
        ]
        + [recv(f) for f in frames]
    )
    assert results[0]["code"] == results[-1]["code"] == "LIMIT_EXCEEDED"
    assert results[-1]["stats"] == pool.stats


def test_raw_5000_digit_integer_classification() -> None:
    wire = '{"v":"MCBEWS/2","kind":"notification","method":"echo","params":' + "9" * 5000 + "}"
    with pytest.raises(V2ProtocolError, match="INVALID_MESSAGE"):
        decode(wire)
    assert typescript([{"wire": wire}])[0]["code"] == "INVALID_MESSAGE"


@pytest.mark.parametrize("elapsed,delivered", [(29999, True), (30000, False), (30001, False)])
def test_nonzero_exact_ttl_delivery_replay(elapsed: int, delivered: bool) -> None:
    now = [2001.0]
    pool = ReassemblyPool(clock_ms=lambda: now[0])
    frames = fragment(MESSAGE, "a.e.1", "up")
    for f in frames[:-1]:
        pool.receive("c", "s", f, "up")
    now[0] += elapsed
    assert pool.receive("c", "s", frames[-1], "up") == (MESSAGE if delivered else None)
    assert pool.stats == {"buffers": 0, "bytes": 0, "tombstones": 1}
    assert all(pool.receive("c", "s", f, "up") is None for f in frames)
    results = typescript(
        [{"op": "init"}, {"op": "time", "now": 2001}]
        + [recv(f) for f in frames[:-1]]
        + [{"op": "time", "now": now[0]}, recv(frames[-1])]
    )
    assert results[-1]["message"] == (MESSAGE if delivered else None)


@pytest.mark.parametrize("token,expected", [("1.0000000000000001", 1), ("1e-4000", 0)])
def test_parsed_ieee_numeric_semantics(token: str, expected: int) -> None:
    wire = '{"v":"MCBEWS/2","kind":"notification","method":"echo","params":' + token + "}"
    assert decode(wire)["params"] == expected
    assert typescript([{"wire": wire}])[0]["message"]["params"] == expected
