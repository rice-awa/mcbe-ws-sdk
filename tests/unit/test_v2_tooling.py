"""Cross-language dependency policy and portable asset-generation entry point."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from . import test_v2_codec as codec_tests


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("missing", ["node", "typescript"])
def test_missing_cross_language_tools(
    monkeypatch: pytest.MonkeyPatch, required: bool, missing: str
) -> None:
    monkeypatch.setenv("MCBE_REQUIRE_CROSS_LANGUAGE", "1" if required else "0")
    monkeypatch.setattr(
        codec_tests.shutil, "which", lambda _: None if missing == "node" else "node"
    )
    run = Mock(
        return_value=subprocess.CompletedProcess([], 1, "", "Cannot find module 'typescript'")
    )
    monkeypatch.setattr(codec_tests.subprocess, "run", run)
    error = pytest.fail.Exception if required else pytest.skip.Exception
    with pytest.raises(error, match="install Node.js 22 and run npm ci in addon/"):
        codec_tests.typescript([])
    assert run.call_count == (0 if missing == "node" else 1)


def test_runner_failure_is_not_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCBE_REQUIRE_CROSS_LANGUAGE", raising=False)
    monkeypatch.setattr(codec_tests.shutil, "which", lambda _: "node")
    failure = subprocess.CalledProcessError(1, ["node", "addon/tests/v2-runner.cjs"])
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0, "", ""), failure])
    monkeypatch.setattr(codec_tests.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        codec_tests.typescript([])
    assert run.call_count == 2


def test_generation_uses_active_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asset = tmp_path / "addon/scripts/bridge/v2/assets.ts"
    asset.parent.mkdir(parents=True)
    asset.write_text("// unchanged\n")
    monkeypatch.setattr(codec_tests, "ROOT", tmp_path)
    run = Mock()
    monkeypatch.setattr(codec_tests.subprocess, "run", run)
    codec_tests.test_generation_stable()
    assert run.call_count == 2
    assert all(call.args[0][0] == sys.executable for call in run.call_args_list)
