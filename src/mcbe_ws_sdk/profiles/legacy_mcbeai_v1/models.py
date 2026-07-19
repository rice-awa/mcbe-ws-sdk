from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class AddonBridgeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    v: Literal[2] = 2
    request_id: str
    capability: str
    payload: dict[str, Any]


class AddonBridgeChunk(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str
    chunk_index: int
    total_chunks: int
    content: str


class AddonBridgeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str
    payload: dict[str, Any]


class UiChatChunk(BaseModel):
    model_config = ConfigDict(extra="allow")

    msg_id: str
    chunk_index: int
    total_chunks: int
    content: str


class UiChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    msg_id: str
    player_name: str
    message: str
