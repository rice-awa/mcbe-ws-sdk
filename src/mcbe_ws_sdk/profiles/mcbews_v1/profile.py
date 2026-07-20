from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Literal

from mcbe_ws_sdk.errors import ConfigurationError


def _require_finite_non_negative_real(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or value < 0:
        raise ConfigurationError(f"mcbews {field_name} must be a finite non-negative real number")


@dataclass(frozen=True)
class McbewsV1Profile:
    bridge_request_message_id: str = "mcbews:bridge_req"
    bridge_response_prefix: str = "MCBEWS|BRIDGE"
    ui_chat_prefix: str = "MCBEWS|UI_CHAT"
    bridge_sender: str = "MCBEWS_BRIDGE"
    response_message_id: str = "mcbews:text_resp"
    request_version: Literal[2] = 2
    response_chunk_delay: float = 0.15
    response_prelude_delay: float = 0.5

    def __post_init__(self) -> None:
        if self.request_version != 2:
            raise ConfigurationError("mcbews request_version must be 2")
        _require_finite_non_negative_real(self.response_chunk_delay, "response_chunk_delay")
        _require_finite_non_negative_real(self.response_prelude_delay, "response_prelude_delay")


MCBEWS_V1 = McbewsV1Profile()
