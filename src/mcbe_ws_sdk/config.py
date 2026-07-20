from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from types import MappingProxyType

from mcbe_ws_sdk.errors import ConfigurationError
from mcbe_ws_sdk.profiles import AddonBridgeProfile
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import LegacyMcbeAiV1Profile


def _require_positive_int(value: object, field_name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ConfigurationError(f"{field_name} must be a positive integer")


def _require_finite_real(value: object, field_name: str, *, allow_zero: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not isfinite(value)
        or (value < 0 if allow_zero else value <= 0)
    ):
        comparison = "non-negative" if allow_zero else "positive"
        raise ConfigurationError(f"{field_name} must be a finite {comparison} real number")


@dataclass(frozen=True, slots=True)
class FlowControlSettings:
    command_line_byte_budget: int = 461
    max_chunk_content_length: int = 400
    chunk_sentence_mode: bool = True
    chunk_delays: Mapping[str, float] = field(
        default_factory=lambda: {
            "tellraw": 0.05,
            "scriptevent": 0.05,
            "ai_resp": 0.15,
        }
    )

    VALID_DELAY_KINDS = frozenset({"tellraw", "scriptevent", "ai_resp"})

    def __post_init__(self) -> None:
        _require_positive_int(self.command_line_byte_budget, "flow.command_line_byte_budget")
        _require_positive_int(self.max_chunk_content_length, "flow.max_chunk_content_length")
        try:
            delays = dict(self.chunk_delays)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("flow.chunk_delays must be a mapping") from exc
        invalid = delays.keys() - self.VALID_DELAY_KINDS
        if invalid:
            raise ConfigurationError(
                f"flow.chunk_delays contains unknown keys: {sorted(invalid)}; "
                f"valid keys: {sorted(self.VALID_DELAY_KINDS)}"
            )
        for kind, delay in delays.items():
            _require_finite_real(delay, f"flow.chunk_delays.{kind}", allow_zero=True)
        object.__setattr__(self, "chunk_delays", MappingProxyType(delays))


@dataclass(frozen=True, slots=True)
class AddonBridgeSettings:
    timeout_seconds: float = 5.0
    buffer_ttl_seconds: float = 30.0
    max_pending_requests: int = 128
    max_buffer_ids: int = 128
    max_chunks_per_message: int = 64
    max_message_bytes: int = 262_144
    max_total_buffer_bytes: int = 1_048_576
    profile: AddonBridgeProfile = field(default_factory=LegacyMcbeAiV1Profile)

    def __post_init__(self) -> None:
        for name in ("timeout_seconds", "buffer_ttl_seconds"):
            _require_finite_real(getattr(self, name), f"addon.{name}")
        for name in (
            "max_pending_requests",
            "max_buffer_ids",
            "max_chunks_per_message",
            "max_message_bytes",
            "max_total_buffer_bytes",
        ):
            _require_positive_int(getattr(self, name), f"addon.{name}")


@dataclass(frozen=True, slots=True)
class WebsocketTransportConfig:
    """Transport knobs for the facade's ``websockets.serve`` lifetime."""

    host: str = "0.0.0.0"
    port: int = 8080
    ping_interval: float | None = 30.0
    ping_timeout: float | None = 15.0
    close_timeout: float = 15.0
    max_size: int | None = 10 * 1024 * 1024
    max_queue: int | None = 32

    def __post_init__(self) -> None:
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ConfigurationError("websocket.port must be an integer between 1 and 65535")
        for name in ("ping_interval", "ping_timeout"):
            value = getattr(self, name)
            if value is not None:
                _require_finite_real(value, f"websocket.{name}")
        _require_finite_real(self.close_timeout, "websocket.close_timeout")
        for name in ("max_size", "max_queue"):
            value = getattr(self, name)
            if value is not None:
                _require_positive_int(value, f"websocket.{name}")


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    flow: FlowControlSettings = field(default_factory=FlowControlSettings)
    addon: AddonBridgeSettings = field(default_factory=AddonBridgeSettings)
    websocket: WebsocketTransportConfig = field(default_factory=WebsocketTransportConfig)
