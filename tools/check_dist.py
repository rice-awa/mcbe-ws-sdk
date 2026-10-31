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

import json
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
REQUIRED_WHEEL_ASSETS: tuple[str, ...] = (
    "mcbe_ws_sdk/profiles/mcbews_v1/manifest.json",
    "mcbe_ws_sdk/profiles/mcbews_v1/vectors.json",
    "mcbe_ws_sdk/profiles/mcbews_v2/manifest.json",
    "mcbe_ws_sdk/profiles/mcbews_v2/vectors.json",
)


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
    for asset in REQUIRED_WHEEL_ASSETS:
        if asset not in names:
            raise DistributionError(f"wheel is missing canonical protocol asset: {asset}")
    with zipfile.ZipFile(path) as archive:
        validate_authorities({asset: archive.read(asset) for asset in REQUIRED_WHEEL_ASSETS})


def validate_authorities(assets: dict[str, bytes]) -> None:
    """Validate both installed protocol authorities without importing the SDK."""
    try:
        manifest, vectors, v2_manifest, v2_vectors = (
            json.loads(assets[asset]) for asset in REQUIRED_WHEEL_ASSETS
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError("canonical protocol assets are not valid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("protocol_line") != "MCBEWS/1":
        raise DistributionError("manifest is not the MCBEWS/1 authority")
    if not isinstance(vectors, dict) or not {
        "bridge_requests",
        "ui_chat",
        "text_response",
        "session",
        "approval",
        "behavior",
    }.issubset(vectors):
        raise DistributionError("v1 vectors are incomplete")
    stream_kinds = {"stream_open", "stream_item", "stream_end", "stream_error", "stream_cancel"}
    if (
        not isinstance(v2_manifest, dict)
        or v2_manifest.get("protocol") != "MCBEWS/2"
        or not isinstance(v2_manifest.get("fields"), dict)
        or not stream_kinds.issubset(v2_manifest["fields"])
        or "stream/1" not in v2_manifest.get("negotiation", {}).get("features", [])
    ):
        raise DistributionError("manifest is not the streaming MCBEWS/2 authority")
    if not isinstance(v2_vectors, dict) or not all(
        isinstance(v2_vectors.get(key), list) and v2_vectors[key]
        for key in ("valid", "invalid", "raw_invalid")
    ):
        raise DistributionError("v2 vectors are incomplete")
    kinds = {vector.get("message", {}).get("kind") for vector in v2_vectors["valid"]}
    if not (stream_kinds | {"request", "response", "notification"}).issubset(kinds):
        raise DistributionError("v2 vectors are missing message kinds")


def check_sdist(path: Path) -> None:
    if path.stat().st_size >= SDIST_MAX_BYTES:
        raise DistributionError(f"sdist too large: {path}")
    with tarfile.open(path, "r:gz") as archive:
        names = [item.name for item in archive.getmembers() if item.isfile()]
    validate_members(names)
    if len(names) >= SDIST_MAX_FILES:
        raise DistributionError(f"sdist has too many files: {len(names)}")
    assets = {}
    with tarfile.open(path, "r:gz") as archive:
        for asset in REQUIRED_WHEEL_ASSETS:
            member = next((name for name in names if name.endswith(f"/src/{asset}")), None)
            if member is None:
                raise DistributionError(f"sdist is missing canonical protocol asset: {asset}")
            handle = archive.extractfile(member)
            assert handle is not None
            assets[asset] = handle.read()
    validate_authorities(assets)


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
