"""Smoke tests: verify that examples run end-to-end through the public API surface."""

from __future__ import annotations

import subprocess
import sys


def test_addon_capability_example_runs() -> None:
    result = subprocess.run(
        [sys.executable, "examples/addon-capability-call/capability_call.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"ok": true' in result.stdout.lower()


def test_basic_server_example_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "examples/basic-server/server.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout


def test_addon_server_example_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "examples/addon-server/server.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout
