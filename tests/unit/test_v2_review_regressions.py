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
