#!/usr/bin/env python3
"""Generate the reference Addon protocol projection and Python fixtures.

``manifest.json`` and ``vectors.json`` are the only editable protocol assets.
This script deliberately emits deterministic text so CI can reject a hand
edited TypeScript projection or a stale test fixture.

Usage::

    python tools/generate_protocol_assets.py       # write generated files
    python tools/generate_protocol_assets.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "src/mcbe_ws_sdk/profiles/mcbews_v1/manifest.json"
VECTORS_PATH = ROOT / "src/mcbe_ws_sdk/profiles/mcbews_v1/vectors.json"
PROTOCOL_PATH = ROOT / "addon/scripts/bridge/protocol.ts"
FIXTURE_PATH = ROOT / "tests/fixtures/mcbews_v1_vectors.json"

WIRE_CONSTANTS = (
    ("capability_request_script_event_id", "CAPABILITY_REQUEST_SCRIPT_EVENT_ID"),
    ("capability_response_chat_prefix", "CAPABILITY_RESPONSE_CHAT_PREFIX"),
    ("ui_chat_chunk_prefix", "UI_CHAT_CHUNK_PREFIX"),
    ("session_request_chat_prefix", "SESSION_REQUEST_CHAT_PREFIX"),
    ("session_request_script_event_id", "SESSION_REQUEST_SCRIPT_EVENT_ID"),
    ("session_response_script_event_id", "SESSION_RESPONSE_SCRIPT_EVENT_ID"),
    ("text_response_script_event_id", "TEXT_RESPONSE_SCRIPT_EVENT_ID"),
    ("approval_allow_chat_prefix", "APPROVAL_ALLOW_CHAT_PREFIX"),
    ("approval_deny_chat_prefix", "APPROVAL_DENY_CHAT_PREFIX"),
    ("trusted_bridge_player_name", "TRUSTED_BRIDGE_PLAYER_NAME"),
)
VERSION_CONSTANTS = (
    ("capability_request_schema", "CAPABILITY_REQUEST_SCHEMA_VERSION"),
    ("session_schema", "SESSION_SCHEMA_VERSION"),
    ("text_response_framing", "TEXT_RESPONSE_FRAMING_VERSION"),
    ("ddui_persistence", "DDUI_PERSISTENCE_VERSION"),
)
LIMIT_CONSTANTS = (
    ("command_line_byte_budget", "COMMAND_LINE_BYTE_BUDGET"),
    ("command_line_budget_source", "COMMAND_LINE_BUDGET_SOURCE"),
    ("upstream_max_content_code_points", "UPSTREAM_MAX_CONTENT_CODE_POINTS"),
    ("response_max_buffers", "RESPONSE_MAX_BUFFERS"),
    ("response_max_chunks_per_message", "RESPONSE_MAX_CHUNKS_PER_MESSAGE"),
    ("response_max_message_bytes", "RESPONSE_MAX_MESSAGE_BYTES"),
    ("response_max_total_buffer_bytes", "RESPONSE_MAX_TOTAL_BUFFER_BYTES"),
    ("response_buffer_ttl_ms", "RESPONSE_BUFFER_TTL_MS"),
    ("session_response_max_command_bytes", "SESSION_RESPONSE_MAX_COMMAND_BYTES"),
)
TEXT_CONSTANTS = (
    ("usage_field", "TEXT_RESPONSE_USAGE_FIELD"),
    ("usage_completion_only", "TEXT_RESPONSE_USAGE_COMPLETION_ONLY"),
    ("conversation_id_field", "TEXT_RESPONSE_CONVERSATION_ID_FIELD"),
    ("title_field", "TEXT_RESPONSE_TITLE_FIELD"),
)


def _camel_case(name: str) -> str:
    return re.sub(r"_([a-zA-Z0-9])", lambda match: match.group(1).upper(), name)


def _ts_key(name: str) -> str:
    camel = _camel_case(name)
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", camel):
        return camel
    return json.dumps(camel, ensure_ascii=False)


def _ts_literal(value: Any, level: int = 0) -> str:
    """Render JSON-compatible data as stable, readable TypeScript literals."""

    indent = "  " * level
    child_indent = "  " * (level + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = ["{"]
        for key, item in value.items():
            lines.append(f"{child_indent}{_ts_key(str(key))}: {_ts_literal(item, level + 1)},")
        lines.append(f"{indent}}}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        one_line = "[" + ", ".join(_ts_literal(item, level + 1) for item in value) + "]"
        if "\n" not in one_line and len(indent) + len(one_line) <= 100:
            return one_line
        lines = ["["]
        for item in value:
            lines.append(f"{child_indent}{_ts_literal(item, level + 1)},")
        lines.append(f"{indent}]")
        return "\n".join(lines)
    if isinstance(value, str):
        double_quoted = json.dumps(value, ensure_ascii=False)
        single_quoted = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
        # Prettier chooses the quote style with fewer escapes for strings that
        # contain embedded JSON.  Keeping the same deterministic heuristic
        # lets the exact generator check run without a Node toolchain.
        if single_quoted.count("\\") < double_quoted.count("\\"):
            return single_quoted
        return double_quoted
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def _camel_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {_camel_case(str(key)): _camel_projection(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camel_projection(item) for item in value]
    return value


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def render_protocol(manifest: dict[str, Any], vectors: dict[str, Any]) -> str:
    """Render the complete Addon projection from both canonical JSON assets."""

    protocol_line = manifest.get("protocol_line")
    if not isinstance(protocol_line, str) or not protocol_line:
        raise ValueError("manifest.protocol_line must be a non-empty string")
    lines = [
        f"/** Generated {protocol_line} projection; edit manifest/vectors JSON instead. */",
        "",
    ]
    lines.append(
        f"export const MCBEWS_PROTOCOL_LINE = "
        f"{_ts_literal(protocol_line)} as const;"
    )
    for field, constant in WIRE_CONSTANTS:
        lines.append(
            f"export const {constant} = "
            f"{json.dumps(manifest['wire'][field], ensure_ascii=False)} as const;"
        )
    lines.append("")
    for field, constant in VERSION_CONSTANTS:
        lines.append(f"export const {constant} = {manifest['versions'][field]} as const;")
    lines.append("")
    for field, constant in LIMIT_CONSTANTS:
        value = manifest["limits"][field]
        lines.append(f"export const {constant} = {_ts_literal(value)} as const;")
    lines.append("")
    lines.append(
        "export const TEXT_RESPONSE_ALLOWED_ROLES = "
        f"{_ts_literal(manifest['text_response']['allowed_roles'])} as const;"
    )
    for field, constant in TEXT_CONSTANTS:
        value = manifest["text_response"][field]
        lines.append(f"export const {constant} = {_ts_literal(value)} as const;")
    lines.append("")
    lines.append(
        "export const MCBEWS_V1_ERROR_CODES = "
        f"{_ts_literal(manifest['error_codes'])} as const;"
    )
    lines.append("")

    # Keep generated manifest values linked to the generated constants instead
    # of duplicating wire/limit literals in a second source of truth.
    manifest_lines = [
        "export const MCBEWS_V1_MANIFEST = {",
        "  protocolLine: MCBEWS_PROTOCOL_LINE,",
    ]
    manifest_lines.extend(
        _render_manifest_group(
            "wire",
            manifest["wire"],
            dict(WIRE_CONSTANTS),
        )
    )
    manifest_lines.extend(
        _render_manifest_group(
            "versions",
            manifest["versions"],
            dict(VERSION_CONSTANTS),
        )
    )
    manifest_lines.extend(
        _render_manifest_group(
            "limits",
            manifest["limits"],
            dict(LIMIT_CONSTANTS),
        )
    )
    manifest_lines.append("  textResponse: {")
    manifest_lines.append(
        f"    allowedRoles: {_ts_literal(manifest['text_response']['allowed_roles'], 2)},"
    )
    for field, constant in TEXT_CONSTANTS:
        manifest_lines.append(f"    {_ts_key(field)}: {constant},")
    manifest_lines.append("  },")
    manifest_lines.append("  errorCodes: MCBEWS_V1_ERROR_CODES,")
    manifest_lines.append("} as const;")
    lines.extend(manifest_lines)
    lines.append("")
    lines.append(
        f"export const MCBEWS_V1_WIRE_VECTORS = {_ts_literal(_camel_projection(vectors))} as const;"
    )
    lines.append("")
    return "\n".join(lines)


def _render_manifest_group(
    name: str,
    group: dict[str, Any],
    constants: dict[str, str],
) -> list[str]:
    lines = [f"  {_ts_key(name)}: {{"]
    for field in group:
        constant = constants.get(field)
        value = constant if constant is not None else _ts_literal(group[field], 2)
        lines.append(f"    {_ts_key(field)}: {value},")
    lines.append("  },")
    return lines


def render_fixture(vectors: dict[str, Any]) -> str:
    """Render the checked-in Python fixture with canonical JSON formatting."""

    return json.dumps(vectors, ensure_ascii=False, indent=2) + "\n"


def generated_outputs() -> dict[Path, str]:
    manifest = _load_json(MANIFEST_PATH)
    vectors = _load_json(VECTORS_PATH)
    return {
        PROTOCOL_PATH: render_protocol(manifest, vectors),
        FIXTURE_PATH: render_fixture(vectors),
    }


def check_outputs() -> list[str]:
    violations: list[str] = []
    for path, expected in generated_outputs().items():
        actual = path.read_text(encoding="utf-8") if path.exists() else None
        if actual != expected:
            violations.append(str(path.relative_to(ROOT)))
    return violations


def write_outputs() -> None:
    for path, content in generated_outputs().items():
        path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when generated files are stale")
    args = parser.parse_args()
    stale = check_outputs()
    if args.check:
        if stale:
            print("generated protocol assets are stale:", file=sys.stderr)
            for path in stale:
                print(f"  {path}", file=sys.stderr)
            return 1
        print("generate_protocol_assets: ok")
        return 0
    write_outputs()
    print("generate_protocol_assets: wrote protocol.ts and mcbews_v1_vectors.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
