#!/usr/bin/env python3
"""Distribution archive validator for the V2-only mcbe-ws-sdk package.

Usage::

    python tools/check_dist.py <dist-dir>

Checks that exactly one wheel (``.whl``) and one sdist (``.tar.gz``) exist
under *dist-dir*, then validates each against the implementation contract:

* No unsafe archive members (absolute paths, ``..``, forbidden directories)
* No v1 package members (``profiles/mcbews_v1``, ``addon``,
  ``extensions/mcbews_v1``)
* Wheel must contain ``mcbe_ws_sdk/py.typed``
* ``profiles/mcbews_v2/manifest.json`` and ``vectors.json`` are the only
  required protocol assets, and they are the MCBEWS/2 authority
* Installed Python modules are free of retired MCBEWS/1 identifiers
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
from typing import Any

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
    "mcbe_ws_sdk/profiles/mcbews_v2/manifest.json",
    "mcbe_ws_sdk/profiles/mcbews_v2/vectors.json",
)
# Package-relative prefixes that must never ship.
FORBIDDEN_PACKAGE_PREFIXES: tuple[str, ...] = (
    "mcbe_ws_sdk/profiles/mcbews_v1/",
    "mcbe_ws_sdk/addon/",
    "mcbe_ws_sdk/extensions/mcbews_v1/",
)
V1_PRODUCTION_IDENTIFIERS: tuple[str, ...] = (
    "MCBEWS/1",
    "mcbews:bridge_req",
    "mcbews:session_req",
    "mcbews:text_resp",
    "MCBEWS|BRIDGE",
    "MCBEWS|UI_CHAT",
    "MCBEWS|SESSION",
    "MCBEWS|TOOL_APPROVE",
    "MCBEWS|TOOL_DENY",
)
STREAM_KINDS: set[str] = {
    "stream_open",
    "stream_item",
    "stream_end",
    "stream_error",
    "stream_cancel",
}


class DistributionError(Exception):
    """Raised when a distribution artifact violates the contract."""


def package_relative_member(name: str) -> str | None:
    """Return the ``mcbe_ws_sdk/...`` path for a wheel or sdist member.

    Sdist members are prefixed with ``<name>-<version>/src/``; wheels are not.
    Returns ``None`` when the member is outside the package tree.
    """
    parts = PurePosixPath(name).parts
    try:
        start = parts.index("mcbe_ws_sdk")
    except ValueError:
        return None
    return "/".join(parts[start:])


def validate_members(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise DistributionError(f"unsafe archive member: {name}")
        if FORBIDDEN_PARTS.intersection(path.parts):
            raise DistributionError(f"forbidden archive member: {name}")
        relative = package_relative_member(name)
        if relative is not None and relative.startswith(FORBIDDEN_PACKAGE_PREFIXES):
            raise DistributionError(f"forbidden v1 package member: {name}")


def _read_json(assets: dict[str, bytes], asset: str) -> Any:
    try:
        return json.loads(assets[asset])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError(f"protocol asset is not valid JSON: {asset}") from exc


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                keys.append(key)
            keys.extend(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


def _check_v2_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise DistributionError("v2 manifest must be a JSON object")
    if manifest.get("protocol") != "MCBEWS/2":
        raise DistributionError("manifest is not the MCBEWS/2 authority")
    fields = manifest.get("fields")
    if not isinstance(fields, dict) or not STREAM_KINDS.issubset(fields):
        raise DistributionError("v2 manifest is missing stream message kinds")
    carrier = manifest.get("carrier")
    if not isinstance(carrier, dict) or carrier.get("event") != "mcbews:packet_v2":
        raise DistributionError("v2 manifest carrier is not mcbews:packet_v2")
    negotiation = manifest.get("negotiation")
    if not isinstance(negotiation, dict):
        raise DistributionError("v2 manifest is missing negotiation")
    if negotiation.get("event") != "mcbews:negotiate_v2":
        raise DistributionError("v2 manifest negotiation is not mcbews:negotiate_v2")
    if manifest.get("versions") not in (None, ["MCBEWS/2"]):
        raise DistributionError("v2 manifest must not advertise a retired version")
    if negotiation.get("versions") != ["MCBEWS/2"]:
        raise DistributionError("v2 negotiation must advertise exactly MCBEWS/2")
    if "stream/1" not in negotiation.get("features", []):
        raise DistributionError("v2 manifest must advertise stream/1")
    for key in _walk_keys(manifest):
        lowered = key.lower()
        if "legacy" in lowered or lowered in {"v1", "v1_methods"}:
            raise DistributionError(f"v2 manifest still selects v1 via {key!r}")


def _check_v2_vectors(vectors: Any, errors: set[str]) -> None:
    if not isinstance(vectors, dict):
        raise DistributionError("v2 vectors must be a JSON object")
    if not all(
        isinstance(vectors.get(key), list) and vectors[key]
        for key in ("valid", "invalid", "raw_invalid")
    ):
        raise DistributionError("v2 vectors are incomplete")
    kinds = {
        vector["message"].get("kind")
        for vector in vectors["valid"]
        if isinstance(vector, dict) and isinstance(vector.get("message"), dict)
    }
    if not (STREAM_KINDS | {"request", "response", "notification"}).issubset(kinds):
        raise DistributionError("v2 vectors are missing message kinds")
    for vector in [*vectors["invalid"], *vectors["raw_invalid"]]:
        if not isinstance(vector, dict) or vector.get("expected") not in errors:
            raise DistributionError("v2 vectors declare an unknown rejection code")


def validate_authorities(assets: dict[str, bytes]) -> None:
    """Validate the sole v2 authority without importing the SDK."""
    manifest = _read_json(assets, REQUIRED_WHEEL_ASSETS[0])
    vectors = _read_json(assets, REQUIRED_WHEEL_ASSETS[1])
    _check_v2_manifest(manifest)
    _check_v2_vectors(vectors, set(manifest.get("errors", [])))


def _assert_clean_source(label: str, name: str, text: str) -> None:
    for token in V1_PRODUCTION_IDENTIFIERS:
        if token in text:
            raise DistributionError(
                f"{label} module still contains retired identifier {token!r}: {name}"
            )


def _scan_wheel_python_sources(archive: zipfile.ZipFile, names: list[str]) -> None:
    """Reject retired v1 identifiers anywhere in installed Python modules."""
    for name in names:
        if not name.endswith(".py") or ".dist-info/" in name:
            continue
        try:
            text = archive.read(name).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DistributionError(f"wheel module is not UTF-8: {name}") from exc
        _assert_clean_source("wheel", name, text)


def _scan_sdist_python_sources(archive: tarfile.TarFile, names: list[str]) -> None:
    """Reject retired v1 identifiers in sdist package modules."""
    for name in names:
        relative = package_relative_member(name)
        if relative is None or not relative.startswith("mcbe_ws_sdk/"):
            continue
        if not relative.endswith(".py"):
            continue
        handle = archive.extractfile(name)
        if handle is None:
            continue
        try:
            text = handle.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DistributionError(f"sdist module is not UTF-8: {name}") from exc
        _assert_clean_source("sdist", name, text)


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
        validate_authorities({asset: archive.read(asset) for asset in REQUIRED_WHEEL_ASSETS})
        _scan_wheel_python_sources(archive, names)


def check_sdist(path: Path) -> None:
    if path.stat().st_size >= SDIST_MAX_BYTES:
        raise DistributionError(f"sdist too large: {path}")
    with tarfile.open(path, "r:gz") as archive:
        names = [item.name for item in archive.getmembers() if item.isfile()]
        validate_members(names)
        if len(names) >= SDIST_MAX_FILES:
            raise DistributionError(f"sdist has too many files: {len(names)}")
        assets: dict[str, bytes] = {}
        for asset in REQUIRED_WHEEL_ASSETS:
            member = next((name for name in names if name.endswith(f"/src/{asset}")), None)
            if member is None:
                raise DistributionError(f"sdist is missing canonical protocol asset: {asset}")
            handle = archive.extractfile(member)
            assert handle is not None
            assets[asset] = handle.read()
        validate_authorities(assets)
        _scan_sdist_python_sources(archive, names)


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
