"""Workflow smoke tests: verify CI/release workflow structure.

These tests parse the YAML workflow files with ``yaml.BaseLoader`` to avoid any
schema coercion and assert the structural properties that gate the release
pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml


def load_workflow(name: str) -> dict[str, Any]:
    """Load a GitHub Actions workflow file from ``.github/workflows/<name>``."""
    path = Path(".github/workflows") / name
    return cast(
        dict[str, Any],
        yaml.load(path.read_text("utf-8"), Loader=yaml.BaseLoader),
    )


def test_ci_dist_depends_on_every_gate() -> None:
    """The dist job waits for every non-dist CI gate (incl. docs)."""
    workflow = load_workflow("ci.yml")
    assert set(workflow["jobs"]["dist"]["needs"]) == {
        "quality",
        "python",
        "websockets",
        "addon",
        "docs",
    }


def test_release_publishes_the_verified_artifact_only() -> None:
    """Release publish downloads the verified artifact; it never rebuilds."""
    workflow = load_workflow("release.yml")
    jobs = workflow["jobs"]

    assert jobs["publish"]["needs"] == "verify"
    assert jobs["publish"]["permissions"]["id-token"] == "write"

    for name, job in jobs.items():
        if name != "publish":
            assert job.get("permissions", {}).get("id-token") != "write"

    steps = jobs["publish"]["steps"]
    assert any(step.get("uses") == "actions/download-artifact@v4" for step in steps)
    assert all("python -m build" not in step.get("run", "") for step in steps)
