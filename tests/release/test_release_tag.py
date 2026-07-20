"""Tests for tools/check_release_tag.py.

Covers match, mismatch and malformed tag via subprocess calls.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_tag_check(tag: str) -> subprocess.CompletedProcess[str]:
    """Run check_release_tag.py from the project root with *tag*."""
    tool = Path(__file__).resolve().parent.parent.parent / "tools" / "check_release_tag.py"
    return subprocess.run(
        [sys.executable, str(tool), tag],
        cwd=str(tool.parent.parent),
        capture_output=True,
        text=True,
    )


def test_tag_matches_package_version() -> None:
    """v0.1.0 matches the current pyproject.toml version."""
    result = _run_tag_check("v0.1.0")
    assert result.returncode == 0, result.stderr


def test_tag_does_not_match() -> None:
    """A non-matching tag exits non-zero."""
    result = _run_tag_check("v999.999.999")
    assert result.returncode != 0


def test_malformed_tag_is_rejected() -> None:
    """A tag without the leading 'v' is malformed."""
    result = _run_tag_check("0.1.0")
    assert result.returncode != 0


def test_empty_tag_is_rejected() -> None:
    """An empty tag is malformed."""
    result = _run_tag_check("")
    assert result.returncode != 0
