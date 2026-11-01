"""Workflow smoke tests: verify CI/release workflow structure.

These tests parse the YAML workflow files with ``yaml.BaseLoader`` to avoid any
schema coercion and assert the structural properties that gate the release
pipeline.
"""

from __future__ import annotations

import shlex
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
    """Distribution waits for all prerequisite CI gates."""
    workflow = load_workflow("ci.yml")
    assert set(workflow["jobs"]["dist"]["needs"]) == {
        "quality",
        "python",
        "cross-language",
        "websockets",
        "addon",
        "docs",
    }


def test_ci_cross_language_gate_requires_tools_and_regressions() -> None:
    """Real cross-language regressions must run with dependencies and fail closed."""
    job = load_workflow("ci.yml")["jobs"]["cross-language"]
    assert job["env"]["MCBE_REQUIRE_CROSS_LANGUAGE"] == "1"
    assert "continue-on-error" not in job
    steps = job["steps"]
    assert all("continue-on-error" not in step for step in steps)
    assert all("||" not in step.get("run", "") for step in steps)

    node_index = next(
        i for i, step in enumerate(steps) if step.get("uses") == "actions/setup-node@v4"
    )
    assert steps[node_index]["with"]["node-version"] == "22"
    install_index = next(
        i
        for i, step in enumerate(steps)
        if step.get("run") == "npm ci" and step.get("working-directory") == "addon"
    )
    test_index = next(
        i for i, step in enumerate(steps) if step.get("run", "").startswith("python -m pytest ")
    )
    assert node_index < install_index < test_index
    test_args = shlex.split(steps[test_index]["run"])
    assert {
        "tests/unit/test_v2_codec.py",
        "tests/unit/test_v2_transport.py",
        "tests/unit/test_v2_contract.py",
        "tests/unit/test_v2_tooling.py",
        "tests/unit/test_v2_review_regressions.py",
        "tests/unit/test_v2_runtime.py",
        "tests/unit/test_v2_negotiation.py",
        "tests/unit/test_v2_streams.py",
        "tests/unit/test_example_behavior.py",
    } <= set(test_args)


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


def test_both_pipelines_check_generated_v2_and_installed_contract() -> None:
    for name in ("ci.yml", "release.yml"):
        jobs = load_workflow(name)["jobs"]
        commands = [step.get("run", "") for job in jobs.values() for step in job["steps"]]
        assert "python tools/generate_v2_assets.py --check" in commands
        probes = [command for command in commands if "tools/probe_installed_contract.py" in command]
        assert len(probes) == 1
        assert "cd /tmp" in probes[0]
        assert any('"twine>=7"' in command for command in commands)
    verify = load_workflow("release.yml")["jobs"]["verify"]
    assert verify["env"]["MCBE_REQUIRE_CROSS_LANGUAGE"] == "1"
    commands = [step.get("run", "") for step in verify["steps"]]
    assert commands.index("npm ci") < next(
        index for index, command in enumerate(commands) if command.startswith("pytest ")
    )


def test_websockets_gate_includes_public_server_entries() -> None:
    steps = load_workflow("ci.yml")["jobs"]["websockets"]["steps"]
    commands = [step.get("run", "") for step in steps]
    assert any("tests/unit/test_server_entry.py" in command for command in commands)


def test_workflow_test_paths_exist() -> None:
    """Deleted test files must not leave a permanently failing release gate."""
    root = Path(__file__).resolve().parents[2]
    for name in ("ci.yml", "release.yml"):
        for job in load_workflow(name)["jobs"].values():
            for step in job["steps"]:
                for argument in shlex.split(step.get("run", "")):
                    path = argument.split("::", 1)[0]
                    if path.startswith("tests/") and path.endswith(".py"):
                        assert (root / path).is_file(), f"{name}: missing test path {path}"
