#!/usr/bin/env python3
"""Guard wire-protocol and public-API names for the mcbews v1 cutover.

Checks:
1. Python ``McbewsV1Profile`` defaults match Addon ``constants.ts`` exports.
2. Forbidden AI-branded tokens are absent outside an explicit whitelist.
3. Public API exports the new symbols and does not export the old ones.
4. Flow-control delay kinds use ``text_resp`` (not the retired AI name).

Usage::

    python tools/check_protocol_names.py

Exit code 0 only when every check passes; otherwise prints all violations and
exits 1.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Python profile field → TypeScript constant name.
PAIRS: dict[str, str] = {
    "bridge_request_message_id": "BRIDGE_REQUEST_MESSAGE_ID",
    "response_message_id": "TEXT_RESP_MESSAGE_ID",
    "bridge_response_prefix": "BRIDGE_RESPONSE_PREFIX",
    "ui_chat_prefix": "BRIDGE_UI_CHAT_PREFIX",
    "bridge_sender": "BRIDGE_SENDER",
}

FORBIDDEN: list[str] = [
    r"\bmcbeai\b",
    r"\bMCBEAI\b",
    r"\bMcbeAi\b",
    r"\bMCBEAI_TOOL\b",
    r"\bai_resp\b",
    r"\bAI_RESP\b",
    r"\bLegacyMcbeAi",
    r"\bLEGACY_MCBEAI",
    r"\blegacy_mcbeai",
    r"\bAiRespHandler\b",
    r"\bsetAiRespHandler\b",
    r"\bencode_legacy_response_commands\b",
    r"\bLegacyResponseChunk\b",
    r"\bparseLegacyResponseChunk\b",
    r"\bTOOL_PLAYER_NAME\b",
    r"\bBRIDGE_MESSAGE_ID\b",
    r"\bAI_RESP_MESSAGE_ID\b",
]
FORBIDDEN_RES = [re.compile(pattern) for pattern in FORBIDDEN]

# Relative path prefixes that are never scanned for forbidden tokens.
WHITELIST_PREFIXES: tuple[str, ...] = (
    "docs/reviews/",
    "docs/superpowers/",
    "docs/plans/",
    ".superpowers/",
)

# Directory name components that exclude a path from the forbidden scan.
SKIP_DIR_PARTS: frozenset[str] = frozenset(
    {
        "node_modules",
        "__pycache__",
        "dist",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "lib",
    }
)

# File suffixes considered text for the forbidden scan.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".json",
        ".md",
        ".txt",
        ".toml",
        ".yml",
        ".yaml",
        ".cfg",
        ".ini",
        ".sh",
        ".css",
        ".html",
        ".svg",
    }
)

# docs/addon-bridge-protocol.md migration section: skip lines between this
# heading and the next top-level ``## `` heading.
MIGRATION_HEADING = "## 与旧协议（mcbeai）的关系"
MIGRATION_DOC = "docs/addon-bridge-protocol.md"

SCAN_DIRS: tuple[str, ...] = (
    "src",
    "addon/scripts",
    "addon/tests",
    "tests",
    "examples",
)
SCAN_FILES: tuple[str, ...] = (
    "docs/addon-bridge-protocol.md",
    "docs/PRD.md",
    "README.md",
    "README.zh.md",
    "addon/README.md",
    "addon/README.zh.md",
    "CLAUDE.md",
)

TS_CONST_RE = re.compile(r'export const ([A-Z0-9_]+) = "([^"]+)"')
REQUIRED_PUBLIC = (
    "MCBEWS_V1",
    "McbewsV1Profile",
    "McbewsV1Delivery",
    "encode_text_response_commands",
)
BANNED_PUBLIC = (
    "LEGACY_MCBEAI_V1",
    "LegacyMcbeAiV1Profile",
    "LegacyMcbeAiV1Delivery",
    "encode_legacy_response_commands",
)


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


def _is_whitelisted(rel: str) -> bool:
    if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in WHITELIST_PREFIXES):
        return True
    parts = Path(rel).parts
    return bool(SKIP_DIR_PARTS.intersection(parts))


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    # Extensionless files under scan roots are rare; skip them.
    return False


def _iter_scan_files() -> Iterator[Path]:
    for dirname in SCAN_DIRS:
        root = ROOT / dirname
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = _rel(path)
            if _is_whitelisted(rel):
                continue
            if not _is_text_file(path):
                continue
            yield path
    for filename in SCAN_FILES:
        path = ROOT / filename
        if path.is_file() and not _is_whitelisted(filename):
            yield path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_parity() -> list[Violation]:
    """Ensure Python profile defaults match Addon TypeScript constants."""
    violations: list[Violation] = []

    # Import lazily so the script can still report path issues if the package
    # is missing; public-API check will also fail clearly.
    try:
        from mcbe_ws_sdk.profiles.mcbews_v1.profile import McbewsV1Profile
    except Exception as exc:  # noqa: BLE001 - surface import failure as violation
        return [
            Violation(
                kind="parity",
                location="mcbe_ws_sdk.profiles.mcbews_v1.profile",
                detail=f"failed to import McbewsV1Profile: {exc}",
            )
        ]

    profile = McbewsV1Profile()
    ts_path = ROOT / "addon" / "scripts" / "bridge" / "constants.ts"
    if not ts_path.is_file():
        return [
            Violation(
                kind="parity",
                location=_rel(ts_path),
                detail="constants.ts not found",
            )
        ]

    ts_text = _read_text(ts_path)
    ts_consts = dict(TS_CONST_RE.findall(ts_text))

    for py_field, ts_name in PAIRS.items():
        py_value = getattr(profile, py_field, None)
        if py_value is None:
            violations.append(
                Violation(
                    kind="parity",
                    location=f"McbewsV1Profile.{py_field}",
                    detail="field missing on profile",
                )
            )
            continue
        if ts_name not in ts_consts:
            violations.append(
                Violation(
                    kind="parity",
                    location=_rel(ts_path),
                    detail=f"missing export const {ts_name}",
                )
            )
            continue
        ts_value = ts_consts[ts_name]
        if py_value != ts_value:
            violations.append(
                Violation(
                    kind="parity",
                    location=f"{py_field} ↔ {ts_name}",
                    detail=f"Python={py_value!r} TS={ts_value!r}",
                )
            )
    return violations


def _lines_with_migration_skip(rel: str, text: str) -> Iterable[tuple[int, str]]:
    """Yield (1-based line number, line) skipping the migration section.

    For ``docs/addon-bridge-protocol.md`` only, lines from
    ``## 与旧协议（mcbeai）的关系`` through the line before the next ``## ``
    heading are skipped. The heading line itself is also skipped.
    """
    in_migration = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if rel == MIGRATION_DOC:
            stripped = line.lstrip()
            if stripped.startswith(MIGRATION_HEADING):
                in_migration = True
                continue
            if in_migration and stripped.startswith("## "):
                in_migration = False
            if in_migration:
                continue
        yield lineno, line


def check_forbidden() -> list[Violation]:
    """Scan source/docs for retired AI-branded protocol tokens."""
    violations: list[Violation] = []
    seen: set[tuple[str, int, str]] = set()

    for path in _iter_scan_files():
        rel = _rel(path)
        try:
            text = _read_text(path)
        except UnicodeDecodeError:
            continue

        for lineno, line in _lines_with_migration_skip(rel, text):
            for pattern in FORBIDDEN_RES:
                match = pattern.search(line)
                if match is None:
                    continue
                key = (rel, lineno, match.group(0))
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    Violation(
                        kind="forbidden",
                        location=f"{rel}:{lineno}",
                        detail=f"matched {match.group(0)!r} via /{pattern.pattern}/",
                    )
                )
    return violations


def check_public_api() -> list[Violation]:
    """Verify public exports include new symbols and exclude retired ones."""
    violations: list[Violation] = []
    try:
        import mcbe_ws_sdk
    except Exception as exc:  # noqa: BLE001
        return [
            Violation(
                kind="public_api",
                location="mcbe_ws_sdk",
                detail=f"failed to import package: {exc}",
            )
        ]

    exported = set(mcbe_ws_sdk.__all__)
    for name in REQUIRED_PUBLIC:
        if name not in exported:
            violations.append(
                Violation(
                    kind="public_api",
                    location="mcbe_ws_sdk.__all__",
                    detail=f"missing required export {name!r}",
                )
            )
    for name in BANNED_PUBLIC:
        if name in exported:
            violations.append(
                Violation(
                    kind="public_api",
                    location="mcbe_ws_sdk.__all__",
                    detail=f"banned export still present: {name!r}",
                )
            )
    return violations


def check_delay_kinds() -> list[Violation]:
    """Ensure flow-control delay kinds use the mcbews ``text_resp`` name."""
    violations: list[Violation] = []
    try:
        from mcbe_ws_sdk.config import FlowControlSettings
    except Exception as exc:  # noqa: BLE001
        return [
            Violation(
                kind="delay_kind",
                location="mcbe_ws_sdk.config.FlowControlSettings",
                detail=f"failed to import: {exc}",
            )
        ]

    expected = frozenset({"tellraw", "scriptevent", "text_resp"})
    if FlowControlSettings.VALID_DELAY_KINDS != expected:
        violations.append(
            Violation(
                kind="delay_kind",
                location="FlowControlSettings.VALID_DELAY_KINDS",
                detail=(
                    f"expected {sorted(expected)}, "
                    f"got {sorted(FlowControlSettings.VALID_DELAY_KINDS)}"
                ),
            )
        )

    settings = FlowControlSettings()
    if "text_resp" not in settings.chunk_delays:
        violations.append(
            Violation(
                kind="delay_kind",
                location="FlowControlSettings().chunk_delays",
                detail="missing key 'text_resp'",
            )
        )

    # Avoid embedding the retired token as a source literal in this file's
    # own narrative; construct it so a future self-scan of tools/ stays clean.
    retired = "ai" + "_resp"
    if retired in settings.chunk_delays:
        violations.append(
            Violation(
                kind="delay_kind",
                location="FlowControlSettings().chunk_delays",
                detail=f"retired key {retired!r} must not be present",
            )
        )
    return violations


def main() -> int:
    violations: list[Violation] = []
    violations.extend(check_parity())
    violations.extend(check_forbidden())
    violations.extend(check_public_api())
    violations.extend(check_delay_kinds())

    if violations:
        print(f"check_protocol_names: {len(violations)} violation(s)", file=sys.stderr)
        for item in violations:
            print(item.format(), file=sys.stderr)
        return 1

    print("check_protocol_names: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
