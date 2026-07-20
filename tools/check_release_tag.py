#!/usr/bin/env python3
"""Verify that a git tag matches the package version in ``pyproject.toml``.

Usage::

    python tools/check_release_tag.py v0.1.0

Exits with return code 0 when the tag equals ``f"v{version}"`` (where *version*
is read from ``[project].version`` in ``pyproject.toml`` via ``tomllib``).
Exits non-zero on any mismatch, malformed input, or missing file.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def _get_project_version() -> str:
    """Read ``[project].version`` from ``pyproject.toml``."""
    root = Path(__file__).resolve().parent.parent
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        print(f"error: {pyproject} not found", file=sys.stderr)
        sys.exit(1)
    data = tomllib.loads(pyproject.read_text("utf-8"))
    project = data.get("project", {})
    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        print("error: [project].version is missing or empty in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    return version.strip()


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <tag>", file=sys.stderr)
        sys.exit(1)

    tag = sys.argv[1]
    if not tag:
        print("error: tag is empty", file=sys.stderr)
        sys.exit(1)

    version = _get_project_version()
    expected = f"v{version}"

    if tag == expected:
        print(f"ok  tag {tag} matches package version {version}")
        sys.exit(0)

    print(
        f"error: tag {tag!r} does not match expected {expected!r}",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
