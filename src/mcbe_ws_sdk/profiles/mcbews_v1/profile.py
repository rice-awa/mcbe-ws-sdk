from __future__ import annotations

import warnings
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Literal, cast

from mcbe_ws_sdk.errors import ConfigurationError
from mcbe_ws_sdk.profiles.mcbews_v1.manifest import MCBEWS_V1_MANIFEST


def _require_finite_non_negative_real(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or value < 0:
        raise ConfigurationError(f"mcbews {field_name} must be a finite non-negative real number")


def _wire(name: str) -> str:
    value = MCBEWS_V1_MANIFEST["wire"][name]
    if not isinstance(value, str):
        raise RuntimeError(f"MCBEWS/1 manifest wire field {name!r} is not a string")
    return value


def _version(name: str) -> int:
    value = MCBEWS_V1_MANIFEST["versions"][name]
    if type(value) is not int or value <= 0:
        raise RuntimeError(f"MCBEWS/1 manifest version {name!r} is invalid")
    return value


@dataclass(frozen=True, slots=True, init=False)
class McbewsV1Profile:
    """Concrete MCBEWS/1 wire profile.

    The semantic field names are the protocol authority.  Wire identifiers,
    schema versions and safety bounds come from the installed manifest and
    cannot be replaced by a runtime profile seam.  The empirical command
    budget may be lowered for a deployment and response delays remain
    operational knobs.  Historical names are accepted for one migration cycle
    only when they still select the manifest value.
    """

    protocol_line: str
    capability_request_script_event_id: str
    capability_response_chat_prefix: str
    ui_chat_chunk_prefix: str
    session_request_chat_prefix: str
    session_request_script_event_id: str
    session_response_script_event_id: str
    text_response_script_event_id: str
    approval_allow_chat_prefix: str
    approval_deny_chat_prefix: str
    trusted_bridge_player_name: str
    capability_request_schema_version: Literal[2]
    session_schema_version: Literal[1]
    text_response_framing_version: Literal[1]
    ddui_persistence_version: Literal[2]
    command_line_byte_budget: int
    upstream_max_content_code_points: int
    response_max_buffers: int
    response_max_chunks_per_message: int
    response_max_message_bytes: int
    response_max_total_buffer_bytes: int
    response_buffer_ttl_ms: int
    session_response_max_command_bytes: int
    response_chunk_delay: float
    response_prelude_delay: float

    def __init__(
        self,
        *,
        capability_request_script_event_id: str | None = None,
        capability_response_chat_prefix: str | None = None,
        ui_chat_chunk_prefix: str | None = None,
        session_request_chat_prefix: str | None = None,
        session_request_script_event_id: str | None = None,
        session_response_script_event_id: str | None = None,
        text_response_script_event_id: str | None = None,
        approval_allow_chat_prefix: str | None = None,
        approval_deny_chat_prefix: str | None = None,
        trusted_bridge_player_name: str | None = None,
        capability_request_schema_version: int | None = None,
        session_schema_version: int | None = None,
        text_response_framing_version: int | None = None,
        ddui_persistence_version: int | None = None,
        command_line_byte_budget: int | None = None,
        upstream_max_content_code_points: int | None = None,
        response_max_buffers: int | None = None,
        response_max_chunks_per_message: int | None = None,
        response_max_message_bytes: int | None = None,
        response_max_total_buffer_bytes: int | None = None,
        response_buffer_ttl_ms: int | None = None,
        session_response_max_command_bytes: int | None = None,
        response_chunk_delay: float = 0.15,
        response_prelude_delay: float = 0.5,
        # Deprecated aliases.  They are deliberately not stored as independent
        # values, so alias and semantic field parity cannot drift.
        bridge_request_message_id: str | None = None,
        bridge_response_prefix: str | None = None,
        ui_chat_prefix: str | None = None,
        bridge_sender: str | None = None,
        response_message_id: str | None = None,
        session_request_message_id: str | None = None,
        session_response_message_id: str | None = None,
        request_version: int | None = None,
    ) -> None:
        aliases = {
            "bridge_request_message_id": (
                bridge_request_message_id,
                "capability_request_script_event_id",
            ),
            "bridge_response_prefix": (bridge_response_prefix, "capability_response_chat_prefix"),
            "ui_chat_prefix": (ui_chat_prefix, "ui_chat_chunk_prefix"),
            "bridge_sender": (bridge_sender, "trusted_bridge_player_name"),
            "response_message_id": (response_message_id, "text_response_script_event_id"),
            "session_request_message_id": (
                session_request_message_id,
                "session_request_script_event_id",
            ),
            "session_response_message_id": (
                session_response_message_id,
                "session_response_script_event_id",
            ),
            "request_version": (request_version, "capability_request_schema_version"),
        }
        values: dict[str, object] = {
            "capability_request_script_event_id": capability_request_script_event_id,
            "capability_response_chat_prefix": capability_response_chat_prefix,
            "ui_chat_chunk_prefix": ui_chat_chunk_prefix,
            "session_request_chat_prefix": session_request_chat_prefix,
            "session_request_script_event_id": session_request_script_event_id,
            "session_response_script_event_id": session_response_script_event_id,
            "text_response_script_event_id": text_response_script_event_id,
            "approval_allow_chat_prefix": approval_allow_chat_prefix,
            "approval_deny_chat_prefix": approval_deny_chat_prefix,
            "trusted_bridge_player_name": trusted_bridge_player_name,
            "capability_request_schema_version": capability_request_schema_version,
            "session_schema_version": session_schema_version,
            "text_response_framing_version": text_response_framing_version,
            "ddui_persistence_version": ddui_persistence_version,
            "command_line_byte_budget": command_line_byte_budget,
            "upstream_max_content_code_points": upstream_max_content_code_points,
            "response_max_buffers": response_max_buffers,
            "response_max_chunks_per_message": response_max_chunks_per_message,
            "response_max_message_bytes": response_max_message_bytes,
            "response_max_total_buffer_bytes": response_max_total_buffer_bytes,
            "response_buffer_ttl_ms": response_buffer_ttl_ms,
            "session_response_max_command_bytes": session_response_max_command_bytes,
        }
        request_version_alias_used = request_version is not None
        for alias_name, (alias_value, semantic_name) in aliases.items():
            if alias_value is None:
                continue
            warnings.warn(
                f"McbewsV1Profile.{alias_name} is deprecated; use {semantic_name}",
                DeprecationWarning,
                stacklevel=2,
            )
            current = values[semantic_name]
            if current is not None and current != alias_value:
                raise ConfigurationError(
                    f"{alias_name} conflicts with {semantic_name}; provide one name only"
                )
            values[semantic_name] = alias_value

        provided_values = {name: value for name, value in values.items() if value is not None}

        defaults: dict[str, object] = {
            "capability_request_script_event_id": _wire("capability_request_script_event_id"),
            "capability_response_chat_prefix": _wire("capability_response_chat_prefix"),
            "ui_chat_chunk_prefix": _wire("ui_chat_chunk_prefix"),
            "session_request_chat_prefix": _wire("session_request_chat_prefix"),
            "session_request_script_event_id": _wire("session_request_script_event_id"),
            "session_response_script_event_id": _wire("session_response_script_event_id"),
            "text_response_script_event_id": _wire("text_response_script_event_id"),
            "approval_allow_chat_prefix": _wire("approval_allow_chat_prefix"),
            "approval_deny_chat_prefix": _wire("approval_deny_chat_prefix"),
            "trusted_bridge_player_name": _wire("trusted_bridge_player_name"),
            "capability_request_schema_version": _version("capability_request_schema"),
            "session_schema_version": _version("session_schema"),
            "text_response_framing_version": _version("text_response_framing"),
            "ddui_persistence_version": _version("ddui_persistence"),
            "command_line_byte_budget": MCBEWS_V1_MANIFEST["limits"]["command_line_byte_budget"],
            "upstream_max_content_code_points": MCBEWS_V1_MANIFEST["limits"][
                "upstream_max_content_code_points"
            ],
            "response_max_buffers": MCBEWS_V1_MANIFEST["limits"]["response_max_buffers"],
            "response_max_chunks_per_message": MCBEWS_V1_MANIFEST["limits"][
                "response_max_chunks_per_message"
            ],
            "response_max_message_bytes": MCBEWS_V1_MANIFEST["limits"][
                "response_max_message_bytes"
            ],
            "response_max_total_buffer_bytes": MCBEWS_V1_MANIFEST["limits"][
                "response_max_total_buffer_bytes"
            ],
            "response_buffer_ttl_ms": MCBEWS_V1_MANIFEST["limits"]["response_buffer_ttl_ms"],
            "session_response_max_command_bytes": MCBEWS_V1_MANIFEST["limits"][
                "session_response_max_command_bytes"
            ],
        }
        for name, default in defaults.items():
            if values[name] is None:
                values[name] = default

        expected_capability_schema = defaults["capability_request_schema_version"]
        if values["capability_request_schema_version"] != expected_capability_schema:
            field_name = (
                "request_version"
                if request_version_alias_used
                else "capability_request_schema_version"
            )
            raise ConfigurationError(f"mcbews {field_name} must be {expected_capability_schema}")

        fixed_fields = (
            *defaults.keys(),
        )
        lowerable_limits = {"command_line_byte_budget", "session_response_max_command_bytes"}
        for name in fixed_fields:
            expected = cast(int, defaults[name])
            provided = provided_values.get(name, expected)
            if name in lowerable_limits:
                if type(provided) is not int or provided <= 0 or provided > expected:
                    raise ConfigurationError(
                        f"mcbews {name} may only lower the manifest ceiling ({expected})"
                    )
                values[name] = provided
                continue
            if provided != expected:
                raise ConfigurationError(f"mcbews {name} is fixed by the MCBEWS/1 manifest")
            values[name] = expected

        for name in (
            "upstream_max_content_code_points",
            "response_max_buffers",
            "response_max_chunks_per_message",
            "response_max_message_bytes",
            "response_max_total_buffer_bytes",
            "response_buffer_ttl_ms",
        ):
            value = values[name]
            if type(value) is not int or value <= 0:
                raise ConfigurationError(f"mcbews {name} must be a positive integer")
        for name in (
            "capability_request_script_event_id",
            "capability_response_chat_prefix",
            "ui_chat_chunk_prefix",
            "session_request_chat_prefix",
            "session_request_script_event_id",
            "session_response_script_event_id",
            "text_response_script_event_id",
            "approval_allow_chat_prefix",
            "approval_deny_chat_prefix",
            "trusted_bridge_player_name",
        ):
            value = values[name]
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(f"mcbews {name} must be a non-empty string")
        _require_finite_non_negative_real(response_chunk_delay, "response_chunk_delay")
        _require_finite_non_negative_real(response_prelude_delay, "response_prelude_delay")
        protocol_line = MCBEWS_V1_MANIFEST.get("protocol_line")
        if not isinstance(protocol_line, str) or not protocol_line.strip():
            raise RuntimeError("MCBEWS/1 manifest protocol_line is invalid")
        object.__setattr__(self, "protocol_line", protocol_line)
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "response_chunk_delay", response_chunk_delay)
        object.__setattr__(self, "response_prelude_delay", response_prelude_delay)

    @property
    def bridge_request_message_id(self) -> str:
        warnings.warn(
            "bridge_request_message_id is deprecated; use capability_request_script_event_id",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.capability_request_script_event_id

    @property
    def bridge_response_prefix(self) -> str:
        warnings.warn(
            "bridge_response_prefix is deprecated; use capability_response_chat_prefix",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.capability_response_chat_prefix

    @property
    def ui_chat_prefix(self) -> str:
        warnings.warn(
            "ui_chat_prefix is deprecated; use ui_chat_chunk_prefix",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.ui_chat_chunk_prefix

    @property
    def bridge_sender(self) -> str:
        warnings.warn(
            "bridge_sender is deprecated; use trusted_bridge_player_name",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.trusted_bridge_player_name

    @property
    def response_message_id(self) -> str:
        warnings.warn(
            "response_message_id is deprecated; use text_response_script_event_id",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.text_response_script_event_id

    @property
    def session_request_message_id(self) -> str:
        warnings.warn(
            "session_request_message_id is deprecated; use session_request_script_event_id",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.session_request_script_event_id

    @property
    def session_response_message_id(self) -> str:
        warnings.warn(
            "session_response_message_id is deprecated; use session_response_script_event_id",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.session_response_script_event_id

    @property
    def request_version(self) -> Literal[2]:
        warnings.warn(
            "request_version is deprecated; use capability_request_schema_version",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.capability_request_schema_version


MCBEWS_V1 = McbewsV1Profile()

__all__ = ["MCBEWS_V1", "McbewsV1Profile"]
