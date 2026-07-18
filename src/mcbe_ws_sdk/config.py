from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AddonProtocolConfig:
    bridge_message_id: str = "mcbeai:bridge_request"
    bridge_prefix: str = "MCBEAI|RESP"
    ui_chat_prefix: str = "MCBEAI|UI_CHAT"
    bridge_tool_player_name: str = "MCBEAI_TOOL"
    ai_resp_message_id: str = "mcbeai:ai_resp"


@dataclass(frozen=True)
class FlowControlSettings:
    command_line_byte_budget: int = 461
    max_chunk_content_length: int = 400
    chunk_sentence_mode: bool = True
    chunk_delays: dict[str, float] = field(default_factory=lambda: {
        "tellraw": 0.05,
        "scriptevent": 0.05,
        "ai_resp": 0.15,
        "ai_resp_prelude": 0.5,
    })


@dataclass(frozen=True)
class AddonBridgeSettings:
    timeout_seconds: float = 5.0
    protocol: AddonProtocolConfig = field(default_factory=AddonProtocolConfig)


@dataclass(frozen=True)
class WebsocketTransportConfig:
    """Transport knobs for the facade's ``websockets.serve`` lifetime."""

    host: str = "0.0.0.0"
    port: int = 8080


@dataclass(frozen=True)
class GatewaySettings:
    flow: FlowControlSettings = field(default_factory=FlowControlSettings)
    addon: AddonBridgeSettings = field(default_factory=AddonBridgeSettings)
    websocket: WebsocketTransportConfig = field(default_factory=WebsocketTransportConfig)
