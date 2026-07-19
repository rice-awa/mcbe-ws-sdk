"""Addon bridge protocol models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AddonBridgeChunk(BaseModel):
    """Bridge response fragment parsed from addon chat."""

    request_id: str
    chunk_index: int
    total_chunks: int
    content: str


class AddonBridgeResponse(BaseModel):
    """Reassembled bridge response."""

    request_id: str
    payload: dict[str, Any]


class UiChatChunk(BaseModel):
    """UI chat fragment parsed from addon chat."""

    msg_id: str
    chunk_index: int
    total_chunks: int
    content: str


class UiChatMessage(BaseModel):
    """Reassembled UI chat message."""

    msg_id: str
    player_name: str
    message: str
