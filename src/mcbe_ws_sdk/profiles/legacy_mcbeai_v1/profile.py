from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mcbe_ws_sdk.errors import ConfigurationError


@dataclass(frozen=True)
class LegacyMcbeAiV1Profile:
    bridge_request_message_id: str = "mcbeai:bridge_request"
    bridge_response_prefix: str = "MCBEAI|RESP"
    ui_chat_prefix: str = "MCBEAI|UI_CHAT"
    bridge_sender: str = "MCBEAI_TOOL"
    response_message_id: str = "mcbeai:ai_resp"
    request_version: Literal[2] = 2
    response_chunk_delay: float = 0.15
    response_prelude_delay: float = 0.5

    def __post_init__(self) -> None:
        if self.response_chunk_delay < 0:
            raise ConfigurationError("legacy response_chunk_delay must be >= 0")
        if self.response_prelude_delay < 0:
            raise ConfigurationError("legacy response_prelude_delay must be >= 0")


LEGACY_MCBEAI_V1 = LegacyMcbeAiV1Profile()
