"""Frozen gateway settings.

The SDK is MCBEWS/2-only: the empirical command budget comes from the v2
manifest, and there is no dispatch-mode switch because every connection uses
the reliable business lanes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from types import MappingProxyType

from mcbe_ws_sdk.errors import ConfigurationError
from mcbe_ws_sdk.profiles.mcbews_v2.codec import MANIFEST

_DEFAULT_COMMAND_LINE_BYTE_BUDGET = int(MANIFEST["limits"]["command_bytes"])


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
        }
    )

    VALID_DELAY_KINDS = frozenset({"tellraw", "scriptevent"})

    def __post_init__(self) -> None:
        _require_positive_int(self.command_line_byte_budget, "flow.command_line_byte_budget")
        if self.command_line_byte_budget > _DEFAULT_COMMAND_LINE_BYTE_BUDGET:
            raise ConfigurationError(
                "flow.command_line_byte_budget may only lower the MCBEWS/2 empirical ceiling "
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
class WebsocketTransportConfig:
    """Transport knobs for the server's ``websockets.serve`` lifetime."""

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


@dataclass(frozen=True, slots=True)
class DispatchSettings:
    """Business-task and request-lifecycle knobs for the reliable path."""

    business_queue_maxsize: int = 256
    max_concurrent_business_tasks: int = 64
    event_queue_maxsize: int = 256
    max_pending_requests: int = 128
    command_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
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


def _require_non_negative_int(value: object, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise ConfigurationError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class SendSchedulerSettings:
    """Per-connection outbound scheduler capacity and rate limit.

    ``control_reserve_slots`` / ``control_reserve_bytes`` keep part of the
    queue available for control frames while bulk content is in flight; they
    are automatically clamped so at least one non-control frame still fits.
    ``fairness_quota`` bounds how many consecutive higher-priority frames may be
    sent while a lower priority waits.
    """

    queue_maxsize: int = 256
    queue_max_bytes: int = 4 * 1024 * 1024
    min_interval_seconds: float = 0.0
    capacity_timeout_seconds: float = 5.0
    control_reserve_slots: int = 1
    control_reserve_bytes: int = 4096
    fairness_quota: int = 8

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
        _require_non_negative_int(
            self.control_reserve_slots,
            "gateway.send.control_reserve_slots",
        )
        _require_non_negative_int(
            self.control_reserve_bytes,
            "gateway.send.control_reserve_bytes",
        )
        _require_positive_int(self.fairness_quota, "gateway.send.fairness_quota")


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    flow: FlowControlSettings = field(default_factory=FlowControlSettings)
    websocket: WebsocketTransportConfig = field(default_factory=WebsocketTransportConfig)
    dispatch: DispatchSettings = field(default_factory=DispatchSettings)
    send: SendSchedulerSettings = field(default_factory=SendSchedulerSettings)
