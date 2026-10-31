"""Exercise the same contract probe used by isolated wheel CI."""

from __future__ import annotations

import runpy
from pathlib import Path

_PROBE = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "tools/probe_installed_contract.py")
)


def test_probe_public_entries_assets_and_codecs() -> None:
    _PROBE["probe_assets"]()


async def test_probe_request_stream_completion_and_cancellation() -> None:
    await _PROBE["probe_runtime"]()
