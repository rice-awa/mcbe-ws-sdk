"""Conftest for release-level distribution tests.

Builds the wheel once per session via ``python -m build``.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def built_artifacts() -> tuple[Path, Path]:
    """Build wheel and sdist into a temporary directory, return (wheel, sdist)."""
    project_root = Path(__file__).resolve().parent.parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        dist_dir = Path(tmp)
        subprocess.run(
            ["python", "-m", "build", "--sdist", "--wheel", "--outdir", str(dist_dir)],
            cwd=str(project_root),
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = sorted(dist_dir.glob("*.whl"))
        sdists = sorted(dist_dir.glob("*.tar.gz"))
        if not wheels:
            pytest.fail("no wheel was built")
        if not sdists:
            pytest.fail("no sdist was built")
        yield wheels[0], sdists[0]


@pytest.fixture(scope="session")
def built_wheel(built_artifacts: tuple[Path, Path]) -> Path:
    """Return the built wheel path."""
    return built_artifacts[0]
