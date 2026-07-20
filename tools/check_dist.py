#!/usr/bin/env python3
"""Distribution archive validator for mcbe-ws-sdk.

Usage::

    python tools/check_dist.py <dist-dir>

Checks that exactly one wheel (``.whl``) and one sdist (``.tar.gz``) exist
under *dist-dir*, then validates each against the implementation contract:

* No unsafe archive members (absolute paths, ``..``, forbidden directories)
* Wheel must contain ``mcbe_ws_sdk/py.typed``
* Wheel size < 1 MiB
* Sdist size < 5 MiB
* Sdist file count < 250

Exits with return code 0 on success, 1 on any ``DistributionError``.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS: set[str] = {
    ".env",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "lib",
    "node_modules",
    "reviews",
    "superpowers",
}
SDIST_MAX_BYTES: int = 5 * 1024 * 1024
SDIST_MAX_FILES: int = 250
WHEEL_MAX_BYTES: int = 1 * 1024 * 1024


class DistributionError(Exception):
    """Raised when a distribution artifact violates the contract."""


def validate_members(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise DistributionError(f"unsafe archive member: {name}")
        if FORBIDDEN_PARTS.intersection(path.parts):
            raise DistributionError(f"forbidden archive member: {name}")


def check_wheel(path: Path) -> None:
    if path.stat().st_size >= WHEEL_MAX_BYTES:
        raise DistributionError(f"wheel too large: {path}")
    with zipfile.ZipFile(path) as archive:
        names = [item.filename for item in archive.infolist() if not item.is_dir()]
    validate_members(names)
    if "mcbe_ws_sdk/py.typed" not in names:
        raise DistributionError("wheel is missing mcbe_ws_sdk/py.typed")


def check_sdist(path: Path) -> None:
    if path.stat().st_size >= SDIST_MAX_BYTES:
        raise DistributionError(f"sdist too large: {path}")
    with tarfile.open(path, "r:gz") as archive:
        names = [item.name for item in archive.getmembers() if item.isfile()]
    validate_members(names)
    if len(names) >= SDIST_MAX_FILES:
        raise DistributionError(f"sdist has too many files: {len(names)}")


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <dist-dir>", file=sys.stderr)
        sys.exit(1)

    dist_dir = Path(sys.argv[1])
    if not dist_dir.is_dir():
        print(f"error: not a directory: {dist_dir}", file=sys.stderr)
        sys.exit(1)

    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))

    if len(wheels) != 1:
        print(f"error: expected exactly 1 wheel, found {len(wheels)}", file=sys.stderr)
        sys.exit(1)
    if len(sdists) != 1:
        print(f"error: expected exactly 1 sdist, found {len(sdists)}", file=sys.stderr)
        sys.exit(1)

    errors: list[str] = []
    for wheel in wheels:
        try:
            check_wheel(wheel)
            print(f"ok  {wheel.name}")
        except DistributionError as exc:
            errors.append(str(exc))
            print(f"FAIL {wheel.name}: {exc}", file=sys.stderr)

    for sdist in sdists:
        try:
            check_sdist(sdist)
            print(f"ok  {sdist.name}")
        except DistributionError as exc:
            errors.append(str(exc))
            print(f"FAIL {sdist.name}: {exc}", file=sys.stderr)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
