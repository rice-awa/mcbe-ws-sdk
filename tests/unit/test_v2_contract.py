"""Independent v2 assets and reproducible carrier cost evidence."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "src/mcbe_ws_sdk/profiles/mcbews_v2"


def test_contract_independent_and_all_axes_defined() -> None:
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    assert manifest["protocol"] == "MCBEWS/2"
    assert set(manifest["fields"]) == {
        "common",
        "request",
        "response",
        "notification",
        "stream_open",
        "stream_item",
        "stream_end",
        "stream_error",
        "stream_cancel",
    }
    assert manifest["limits"]["command_bytes"] == 461
    assert manifest["limits"]["upstream_code_points"] == 256
    assert manifest["carrier"]["event"] == "mcbews:packet_v2"
    assert manifest["negotiation"]["versions"] == ["MCBEWS/2"]
    assert not (ASSETS.parent / "mcbews_v1").exists()
    vectors = json.loads((ASSETS / "vectors.json").read_text())
    assert [len(vectors[k]) for k in ("valid", "invalid", "raw_invalid")] == [12, 18, 3]
    assert all("expected" in v for group in vectors.values() for v in group)


def test_format_comparison_reproducible() -> None:
    spec = importlib.util.spec_from_file_location("compare", ROOT / "tools/compare_v2_formats.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = module.measure()
    assert rows == module.measure()
    assert all(r["json_commands"] == r["compact_commands"] for r in rows)
    assert rows[0]["json_final_command_bytes"] == 292
    assert rows[-1]["compact_final_command_bytes"] == 5168
