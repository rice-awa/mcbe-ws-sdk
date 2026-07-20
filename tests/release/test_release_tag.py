"""Tests for tools/check_release_tag.py.

Covers match, mismatch and malformed tag via subprocess calls.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


def _project_version() -> str:
    """Read the current version from pyproject.toml."""
    pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text("utf-8"))
    return data["project"]["version"]


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
    """The current package version tag matches pyproject.toml."""
    tag = f"v{_project_version()}"
    result = _run_tag_check(tag)
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
