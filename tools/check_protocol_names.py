#!/usr/bin/env python3
"""Fail when MCBEWS/1 business identifiers survive in production surfaces.

Checks:

1. Every ``src/mcbe_ws_sdk/**/*.py`` file is free of the retired MCBEWS/1
   business identifiers. No source file is exempt.
2. Every ``addon/scripts/**/*.ts`` production file is free of the same
   identifiers. Negative fixtures belong under ``addon/tests``.
3. Package JSON assets and Addon pack manifests are free of the identifiers.
   ``vectors.json`` may keep an old-version sample only inside
   ``invalid``/``raw_invalid``, where the vector's ``expected`` code is a
   declared rejection.
4. The v2 manifest is the sole authority: ``protocol == "MCBEWS/2"``,
   ``versions == ["MCBEWS/2"]`` and no ``v1_methods``/``legacy`` keys.
5. ``addon/scripts/bridge/v2/assets.ts`` still matches the manifest/vectors
   projection produced by :mod:`tools.generate_v2_assets`.

Usage::

    python tools/check_protocol_names.py

Exit code 0 only when every check passes; otherwise prints all violations and
exits 1.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

V2_MANIFEST = ROOT / "src/mcbe_ws_sdk/profiles/mcbews_v2/manifest.json"
V2_VECTORS = ROOT / "src/mcbe_ws_sdk/profiles/mcbews_v2/vectors.json"
V2_ASSETS = ROOT / "addon/scripts/bridge/v2/assets.ts"
V2_GENERATOR = ROOT / "tools/generate_v2_assets.py"

# Retired MCBEWS/1 business identifiers. These are substrings, so a new v1
# spelling cannot slip through by changing surrounding punctuation.
FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
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

# Manifest keys that only existed to select or emulate the v1 wire protocol.
# Any key containing ``legacy`` (case-insensitive) or named ``v1`` /
# ``v1_methods`` is rejected; this set documents the known spellings.
FORBIDDEN_MANIFEST_KEYS: frozenset[str] = frozenset(
    {"v1_methods", "legacy", "legacy_request", "_legacy"}
)

# Package directories removed by the V2-only cutover.
RETIRED_PACKAGE_PATHS: tuple[str, ...] = (
    "src/mcbe_ws_sdk/profiles/mcbews_v1",
    "src/mcbe_ws_sdk/extensions/mcbews_v1",
    "src/mcbe_ws_sdk/addon",
)

# Sections of vectors.json that deliberately contain rejected payloads.
NEGATIVE_VECTOR_SECTIONS: tuple[str, ...] = ("invalid", "raw_invalid")

_EXPECTED_VERSION = ["MCBEWS/2"]


@dataclass(frozen=True)
class Violation:
    """A single checker failure with a human-readable location."""

    kind: str
    location: str
    detail: str

    def format(self) -> str:
        return f"[{self.kind}] {self.location}: {self.detail}"


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _matches(text: str) -> list[str]:
    """Return the forbidden identifiers present in *text*, in declaration order."""
    return [token for token in FORBIDDEN_IDENTIFIERS if token in text]


def _iter_python_sources() -> Iterator[Path]:
    """Every production Python module, including comments and docstrings."""
    yield from sorted((ROOT / "src" / "mcbe_ws_sdk").rglob("*.py"))


def _iter_addon_scripts() -> Iterator[Path]:
    """Every handwritten production Addon TypeScript module.

    ``addon/scripts/bridge/v2/assets.ts`` is generated from ``vectors.json``.
    That source allows a declared negative vector, and ``check_generated_assets``
    proves this projection is byte-identical to it, so the projection is not
    text-scanned twice.
    """
    for path in sorted((ROOT / "addon" / "scripts").rglob("*.ts")):
        if path == V2_ASSETS:
            continue
        yield path


def _iter_package_json() -> Iterator[Path]:
    """Every JSON asset shipped inside the Python package."""
    yield from sorted((ROOT / "src" / "mcbe_ws_sdk").rglob("*.json"))


def _iter_addon_json() -> Iterator[Path]:
    """Addon package manifests; no negative fixtures belong here."""
    for relative in ("addon/behavior_packs", "addon/resource_packs"):
        yield from sorted((ROOT / relative).rglob("*.json"))
    package = ROOT / "addon" / "package.json"
    if package.is_file():
        yield package


def _scan_text(path: Path, *, kind: str) -> list[Violation]:
    violations: list[Violation] = []
    try:
        text = _read(path)
    except UnicodeDecodeError:
        return [Violation(kind, _rel(path), "file is not valid UTF-8 production text")]
    for lineno, line in enumerate(text.splitlines(), start=1):
        for token in _matches(line):
            violations.append(
                Violation(kind, f"{_rel(path)}:{lineno}", f"retired identifier {token!r}")
            )
    return violations


def _scan_json_asset(path: Path) -> list[Violation]:
    """Scan a package JSON asset, allowing explicit negative vectors only."""
    rel = _rel(path)
    try:
        payload = json.loads(_read(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [Violation("asset", rel, f"not valid JSON: {exc}")]

    if path != V2_VECTORS:
        violations: list[Violation] = []
        for token in _matches(json.dumps(payload, ensure_ascii=False)):
            violations.append(Violation("asset", rel, f"retired identifier {token!r}"))
        return violations

    if not isinstance(payload, dict):
        return [Violation("asset", rel, "v2 vectors must be a JSON object")]
    violations = []
    allowed_codes = _manifest_errors()
    valid_text = json.dumps(payload.get("valid", []), ensure_ascii=False)
    for token in _matches(valid_text):
        violations.append(Violation("asset", f"{rel} valid[]", f"retired identifier {token!r}"))
    for section in NEGATIVE_VECTOR_SECTIONS:
        for vector in payload.get(section, []):
            if not isinstance(vector, dict):
                violations.append(
                    Violation("asset", f"{rel} {section}[]", "vector is not an object")
                )
                continue
            text = json.dumps(vector, ensure_ascii=False)
            tokens = _matches(text)
            if not tokens:
                continue
            expected = vector.get("expected")
            if expected not in allowed_codes:
                violations.append(
                    Violation(
                        "asset",
                        f"{rel} {section}[]",
                        f"retired identifier {tokens[0]!r} without a declared rejection code",
                    )
                )
    return violations


def _manifest_errors() -> set[str]:
    """Return the declared MCBEWS/2 error codes, or an empty set on failure."""
    try:
        manifest = json.loads(_read(V2_MANIFEST))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    errors = manifest.get("errors")
    return set(errors) if isinstance(errors, list) else set()


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def check_retired_packages() -> list[Violation]:
    """Fail when a retired package still contains Python modules."""
    violations: list[Violation] = []
    for relative in RETIRED_PACKAGE_PATHS:
        path = ROOT / relative
        if path.is_dir() and any(path.rglob("*.py")):
            violations.append(
                Violation("tree", relative, "retired package still contains Python modules")
            )
    return violations


def check_production_sources() -> list[Violation]:
    """Scan Python, Addon and package JSON production surfaces."""
    violations: list[Violation] = []
    for path in _iter_python_sources():
        violations.extend(_scan_text(path, kind="python"))
    for path in _iter_addon_scripts():
        violations.extend(_scan_text(path, kind="addon"))
    for path in _iter_addon_json():
        violations.extend(_scan_text(path, kind="asset"))
    for path in _iter_package_json():
        violations.extend(_scan_json_asset(path))
    return violations


def check_manifest_authority() -> list[Violation]:
    """Require the v2 manifest to be the only protocol authority."""
    try:
        manifest = json.loads(_read(V2_MANIFEST))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [Violation("manifest", _rel(V2_MANIFEST), f"cannot load: {exc}")]
    if not isinstance(manifest, dict):
        return [Violation("manifest", _rel(V2_MANIFEST), "must be a JSON object")]

    violations: list[Violation] = []
    if manifest.get("protocol") != "MCBEWS/2":
        violations.append(
            Violation(
                "manifest",
                "protocol",
                f"expected 'MCBEWS/2', got {manifest.get('protocol')!r}",
            )
        )

    carrier = manifest.get("carrier")
    if not isinstance(carrier, dict) or carrier.get("event") != "mcbews:packet_v2":
        violations.append(Violation("manifest", "carrier.event", "expected 'mcbews:packet_v2'"))
    negotiation = manifest.get("negotiation")
    if not isinstance(negotiation, dict) or negotiation.get("event") != "mcbews:negotiate_v2":
        violations.append(
            Violation("manifest", "negotiation.event", "expected 'mcbews:negotiate_v2'")
        )
    version_sources: list[tuple[str, object]] = []
    if "versions" in manifest:
        version_sources.append(("versions", manifest["versions"]))
    if isinstance(negotiation, dict):
        version_sources.append(("negotiation.versions", negotiation.get("versions")))
    if not version_sources:
        violations.append(Violation("manifest", "versions", "no protocol version list is declared"))
    for location, versions in version_sources:
        if versions != _EXPECTED_VERSION:
            violations.append(
                Violation(
                    "manifest",
                    location,
                    f"expected {_EXPECTED_VERSION!r}, got {versions!r}",
                )
            )

    for key in _walk_keys(manifest):
        lowered = key.lower()
        retired = (
            key in FORBIDDEN_MANIFEST_KEYS
            or "legacy" in lowered
            or re.fullmatch(r"v1(?:_methods)?", lowered) is not None
        )
        if retired:
            violations.append(
                Violation(
                    "manifest",
                    "keys",
                    f"retired selection key {key!r} is not allowed in the v2 authority",
                )
            )
    return violations


def check_generated_assets() -> list[Violation]:
    """Require the generated Addon projection to match the v2 authority."""
    proc = subprocess.run(
        [sys.executable, str(V2_GENERATOR), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return []
    detail = (proc.stderr or proc.stdout or "generator failed").strip()
    return [
        Violation(
            "generated",
            _rel(V2_ASSETS),
            f"stale or mismatched projection; run tools/generate_v2_assets.py ({detail})",
        )
    ]


def collect() -> Iterable[Violation]:
    yield from check_retired_packages()
    yield from check_production_sources()
    yield from check_manifest_authority()
    yield from check_generated_assets()


def main() -> int:
    violations = list(collect())
    if violations:
        print(f"check_protocol_names: {len(violations)} violation(s)", file=sys.stderr)
        for item in violations:
            print(item.format(), file=sys.stderr)
        return 1
    print("check_protocol_names: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
