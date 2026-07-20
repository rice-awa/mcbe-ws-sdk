from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_protocol_names_checker_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_protocol_names.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
