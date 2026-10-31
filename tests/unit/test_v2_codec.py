"""Execute real TypeScript and exchange Python-produced and TS-produced JSON."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from mcbe_ws_sdk.profiles.mcbews_v2.codec import V2ProtocolError, decode, encode, validate

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "src/mcbe_ws_sdk/profiles/mcbews_v2/vectors.json").read_text())


def typescript(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["node", "addon/tests/v2-runner.cjs"],
        input=json.dumps(items),
        text=True,
        capture_output=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)  # type: ignore[no-any-return]


@pytest.mark.parametrize("vector", VECTORS["valid"], ids=lambda v: v["name"])
def test_cross_language_both_directions(vector: dict[str, Any]) -> None:
    original = vector["message"]
    result = typescript([{"wire": encode(original)}, {"message": original}])
    assert all(item["code"] == "ok" and item["message"] == original for item in result)
    assert all(decode(item["wire"]) == original for item in result)
    assert validate(original) == original


@pytest.mark.parametrize(
    "vector", VECTORS["invalid"] + VECTORS["raw_invalid"], ids=lambda v: v["name"]
)
def test_cross_language_invalid_results(vector: dict[str, Any]) -> None:
    item = {k: vector[k] for k in ("message", "wire") if k in vector}
    with pytest.raises(V2ProtocolError) as error:
        decode(item["wire"]) if "wire" in item else encode(item["message"])
    assert error.value.code == vector["expected"]
    assert typescript([item])[0]["code"] == vector["expected"]


def test_boundaries_nested_metadata_and_parser_agreement() -> None:
    base = {"v": "MCBEWS/2", "kind": "notification", "method": "m", "params": None}
    cases = [dict(base, params=value) for value in [1.0, -0.0, 2**53 - 1, -(2**53 - 1)]]
    for obj, result in zip(cases, typescript([{"message": v} for v in cases]), strict=True):
        assert result["code"] == "ok" and decode(result["wire"]) == validate(obj)
    wire = '{"v":"wrong","v":"MCBEWS/2","kind":"notification","method":"m","params":1e2}'
    assert decode(wire)["params"] == 100
    assert typescript([{"wire": wire}])[0]["message"] == decode(wire)
    for value in [float("inf"), float("nan"), object(), {1: "bad"}, "\ud800"]:
        with pytest.raises(V2ProtocolError):
            encode(dict(base, params=value))
    original = dict(
        base, meta={"__proto__": {"admin": False}, "custom": {"深": [None, True, "😀"]}}
    )
    decoded = typescript([{"wire": encode(original)}])[0]
    assert decoded["message"] == original and decode(decoded["wire"]) == original
    copy = validate(original)
    copy["meta"]["custom"]["深"].append("new")
    assert copy != original


def test_generation_stable_and_old_assets_unchanged() -> None:
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "tools/generate_v2_assets.py", "--check"],
        check=True,
        cwd=ROOT,
    )
    path = ROOT / "addon/scripts/bridge/v2/assets.ts"
    before = path.read_bytes()
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "tools/generate_v2_assets.py"], check=True, cwd=ROOT
    )
    assert path.read_bytes() == before
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "tools/generate_protocol_assets.py", "--check"],
        check=True,
        cwd=ROOT,
    )


def test_exact_tokens_reject_trailing_newline_and_huge_integer() -> None:
    cases = [
        {"v": "MCBEWS/2", "kind": "request", "id": "p.e.1\n", "method": "m", "params": 0},
        {"v": "MCBEWS/2", "kind": "notification", "method": "m\n", "params": 0},
    ]
    for obj, result in zip(cases, typescript([{"message": v} for v in cases]), strict=True):
        with pytest.raises(V2ProtocolError):
            encode(obj)
        assert result["code"] == "INVALID_MESSAGE"
    with pytest.raises(V2ProtocolError):
        encode({"v": "MCBEWS/2", "kind": "notification", "method": "m", "params": 10**1000})


def test_raw_deep_json_has_same_limit_error() -> None:
    wire = (
        '{"v":"MCBEWS/2","kind":"notification","method":"m","params":'
        + "[" * 1500
        + "0"
        + "]" * 1500
        + "}"
    )
    with pytest.raises(V2ProtocolError, match="LIMIT_EXCEEDED"):
        decode(wire)
    assert typescript([{"wire": wire}])[0]["code"] == "LIMIT_EXCEEDED"
