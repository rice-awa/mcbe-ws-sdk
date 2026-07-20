"""Distribution archive validation tests.

Builds real artifacts once, asserts the clean path passes, then constructs
minimal malicious tar/zip fixtures to test the check functions.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load tools/check_dist.py as a regular module
# ---------------------------------------------------------------------------

_here = Path(__file__).resolve().parent
_project_root = _here.parent.parent

# Use sys.executable for subprocess calls (portable)
_PYTHON = sys.executable or "python3"

_spec = importlib.util.spec_from_file_location(
    "check_dist",
    str(_project_root / "tools" / "check_dist.py"),
)
assert _spec is not None and _spec.loader is not None
_check_dist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_check_dist)

DistributionError = _check_dist.DistributionError
check_wheel = _check_dist.check_wheel
check_sdist = _check_dist.check_sdist
FORBIDDEN_PARTS = _check_dist.FORBIDDEN_PARTS
SDIST_MAX_BYTES = _check_dist.SDIST_MAX_BYTES
SDIST_MAX_FILES = _check_dist.SDIST_MAX_FILES


# ---------------------------------------------------------------------------
# Fixtures — session-scoped real build, then per-function synthetic
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def built_wheel() -> Path:
    """Build the wheel once per session and return its path."""
    return _build_artifacts()[0]


@pytest.fixture(scope="session")
def built_sdist() -> Path:
    """Build the sdist once per session and return its path."""
    return _build_artifacts()[1]


def _build_artifacts() -> tuple[Path, Path]:
    """Return (wheel_path, sdist_path)."""
    with __import__("tempfile").TemporaryDirectory() as tmp:
        dist_dir = Path(tmp)
        subprocess.run(
            [_PYTHON, "-m", "build", "--sdist", "--wheel", "--outdir", str(dist_dir)],
            cwd=str(_project_root),
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
        # Copy out of tempdir for the session
        import shutil

        cache_dir = _project_root / ".pytest_dist_cache"
        cache_dir.mkdir(exist_ok=True)
        for f in wheels + sdists:
            shutil.copy2(f, cache_dir / f.name)
        return (
            cache_dir / wheels[0].name,
            cache_dir / sdists[0].name,
        )


# ---------------------------------------------------------------------------
# Helper factories for malicious fixtures
# ---------------------------------------------------------------------------


def _make_zip_file(members: list[tuple[str, bytes]]) -> Path:
    """Create a temporary .whl (zip) with the given members."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".whl", delete=False) as handle:
        path = Path(handle.name)
        with zipfile.ZipFile(handle, "w") as zf:
            for name, content in members:
                zf.writestr(name, content)
    return path


def _make_tar_file(members: list[tuple[str, bytes]]) -> Path:
    """Create a temporary .tar.gz with the given members."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as handle:
        path = Path(handle.name)
        with tarfile.open(fileobj=handle, mode="w:gz") as tf:
            for name, content in members:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
    return path


# ===================================================================
# Real-artifact tests
# ===================================================================


def test_wheel_contains_py_typed(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        assert "mcbe_ws_sdk/py.typed" in archive.namelist()


def test_clean_wheel_passes(built_wheel: Path) -> None:
    # Should not raise
    check_wheel(built_wheel)


def test_clean_sdist_passes(built_sdist: Path) -> None:
    # Should not raise
    check_sdist(built_sdist)


# ===================================================================
# Malicious wheel (zip) tests
# ===================================================================


def test_wheel_rejects_path_traversal() -> None:
    path = _make_zip_file([("../../etc/passwd", b"root")])
    with pytest.raises(DistributionError, match="unsafe archive member"):
        check_wheel(path)
    path.unlink()


def test_wheel_rejects_env_file() -> None:
    path = _make_zip_file([("mcbe_ws_sdk/.env", b"SECRET=1")])
    with pytest.raises(DistributionError, match="forbidden archive member"):
        check_wheel(path)
    path.unlink()


def test_wheel_rejects_node_modules() -> None:
    path = _make_zip_file([("mcbe_ws_sdk/node_modules/lodash.js", b"//")])
    with pytest.raises(DistributionError, match="forbidden archive member"):
        check_wheel(path)
    path.unlink()


def test_wheel_rejects_missing_py_typed(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        names = [n for n in archive.namelist() if "py.typed" not in n]
    path = _make_zip_file([(n, b"") for n in names])

    with pytest.raises(DistributionError, match="missing mcbe_ws_sdk/py.typed"):
        check_wheel(path)
    path.unlink()


def test_wheel_rejects_too_large() -> None:
    # Create a wheel that exceeds WHEEL_MAX_BYTES
    size = _check_dist.WHEEL_MAX_BYTES + 1
    data = b"x" * size
    t = __import__("tempfile").NamedTemporaryFile(suffix=".whl", delete=False)
    t.write(data)
    t.close()
    path = Path(t.name)
    with pytest.raises(DistributionError, match="wheel too large"):
        check_wheel(path)
    path.unlink()


# ===================================================================
# Malicious sdist (tar.gz) tests
# ===================================================================


def test_sdist_rejects_path_traversal() -> None:
    path = _make_tar_file([("../../etc/passwd", b"root")])
    with pytest.raises(DistributionError, match="unsafe archive member"):
        check_sdist(path)
    path.unlink()


def test_sdist_rejects_env_file() -> None:
    path = _make_tar_file([("mcbe_ws_sdk/.env", b"SECRET=1")])
    with pytest.raises(DistributionError, match="forbidden archive member"):
        check_sdist(path)
    path.unlink()


def test_sdist_rejects_node_modules() -> None:
    path = _make_tar_file([("mcbe_ws_sdk/node_modules/lodash.js", b"//")])
    with pytest.raises(DistributionError, match="forbidden archive member"):
        check_sdist(path)
    path.unlink()


def test_sdist_rejects_too_many_files() -> None:
    members = [(f"file_{i}.txt", b"x") for i in range(SDIST_MAX_FILES + 1)]
    path = _make_tar_file(members)
    with pytest.raises(DistributionError, match="sdist has too many files"):
        check_sdist(path)
    path.unlink()


def test_sdist_rejects_too_large() -> None:
    size = SDIST_MAX_BYTES + 1
    data = b"x" * size
    t = __import__("tempfile").NamedTemporaryFile(suffix=".tar.gz", delete=False)
    t.write(data)
    t.close()
    path = Path(t.name)
    with pytest.raises(DistributionError, match="sdist too large"):
        check_sdist(path)
    path.unlink()


def test_check_dist_script_main(built_wheel: Path, built_sdist: Path) -> None:
    """Run the check_dist.py script as a subprocess against built artifacts."""
    dist_dir = built_wheel.parent
    result = subprocess.run(
        [_PYTHON, str(_project_root / "tools" / "check_dist.py"), str(dist_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
