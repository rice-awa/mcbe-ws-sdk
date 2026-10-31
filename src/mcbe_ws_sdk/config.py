from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from types import MappingProxyType
from typing import Literal

from mcbe_ws_sdk.errors import ConfigurationError
from mcbe_ws_sdk.profiles.mcbews_v1.manifest import MCBEWS_V1_MANIFEST
from mcbe_ws_sdk.profiles.mcbews_v1.profile import McbewsV1Profile

_DEFAULT_COMMAND_LINE_BYTE_BUDGET = int(
    MCBEWS_V1_MANIFEST["limits"]["command_line_byte_budget"]
)


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
    command_line_byte_budget: int = _DEFAULT_COMMAND_LINE_BYTE_BUDGET
    max_chunk_content_length: int = 400
    chunk_sentence_mode: bool = True
    chunk_delays: Mapping[str, float] = field(
        default_factory=lambda: {
            "tellraw": 0.05,
            "scriptevent": 0.05,
            "text_resp": 0.15,
        }
    )

    VALID_DELAY_KINDS = frozenset({"tellraw", "scriptevent", "text_resp"})

    def __post_init__(self) -> None:
        _require_positive_int(self.command_line_byte_budget, "flow.command_line_byte_budget")
        if self.command_line_byte_budget > _DEFAULT_COMMAND_LINE_BYTE_BUDGET:
            raise ConfigurationError(
                "flow.command_line_byte_budget may only lower the MCBEWS/1 empirical ceiling "
                f"({_DEFAULT_COMMAND_LINE_BYTE_BUDGET})"
            )
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
    # MCBEWS/1 is the only runtime profile.  The historical
    # ``AddonBridgeProfile`` name is a deprecated type alias, not a replacement
    # profile seam.
    profile: McbewsV1Profile = field(default_factory=McbewsV1Profile)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, McbewsV1Profile):
            raise ConfigurationError("addon.profile must be a concrete McbewsV1Profile")
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
    response_queue_maxsize: int = 256

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
        _require_positive_int(self.response_queue_maxsize, "websocket.response_queue_maxsize")


DispatchMode = Literal["legacy", "reliable"]


@dataclass(frozen=True, slots=True)
class DispatchSettings:
    """Business-task and request-lifecycle knobs for the opt-in reliable path.

    ``mode="legacy"`` keeps the original serial callback contract. Selecting
    ``"reliable"`` is explicit; it does not change the wire format and does not
    affect the legacy entry point.
    """

    mode: DispatchMode = "legacy"
    business_queue_maxsize: int = 256
    max_concurrent_business_tasks: int = 64
    event_queue_maxsize: int = 256
    max_pending_requests: int = 128
    command_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.mode not in ("legacy", "reliable"):
            raise ConfigurationError("gateway.dispatch.mode must be 'legacy' or 'reliable'")
        for name in (
            "business_queue_maxsize",
            "max_concurrent_business_tasks",
            "event_queue_maxsize",
            "max_pending_requests",
        ):
            _require_positive_int(getattr(self, name), f"gateway.dispatch.{name}")
        _require_finite_real(
            self.command_timeout_seconds,
            "gateway.dispatch.command_timeout_seconds",
        )


@dataclass(frozen=True, slots=True)
class SendSchedulerSettings:
    """Per-connection outbound scheduler capacity and rate limit."""

    queue_maxsize: int = 256
    queue_max_bytes: int = 4 * 1024 * 1024
    min_interval_seconds: float = 0.0
    capacity_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        _require_positive_int(self.queue_maxsize, "gateway.send.queue_maxsize")
        _require_positive_int(self.queue_max_bytes, "gateway.send.queue_max_bytes")
        _require_finite_real(
            self.min_interval_seconds,
            "gateway.send.min_interval_seconds",
            allow_zero=True,
        )
        _require_finite_real(
            self.capacity_timeout_seconds,
            "gateway.send.capacity_timeout_seconds",
        )


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    flow: FlowControlSettings = field(default_factory=FlowControlSettings)
    addon: AddonBridgeSettings = field(default_factory=AddonBridgeSettings)
    websocket: WebsocketTransportConfig = field(default_factory=WebsocketTransportConfig)
    dispatch: DispatchSettings = field(default_factory=DispatchSettings)
    send: SendSchedulerSettings = field(default_factory=SendSchedulerSettings)
