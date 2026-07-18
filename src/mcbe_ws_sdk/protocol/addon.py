"""Addon 桥接协议模型。"""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any

from pydantic import BaseModel


class AddonBridgeRequest(BaseModel):
    """脱壳后的入站 addon 能力请求（解码自 ``scriptevent mcbeai:bridge_request``）。"""

    request_id: str
    capability: str
    payload: dict[str, Any]


def parse_addon_bridge_request(command_line: str, message_id: str) -> AddonBridgeRequest | None:
    """Parse an inbound addon capability request out of a scriptevent commandLine.

    Returns ``None`` when ``command_line`` is not a matching ``scriptevent``.
    The JSON body is validated against :class:`AddonBridgeRequest`.
    """
    if not command_line.startswith("scriptevent "):
        return None
    rest = command_line[len("scriptevent "):]
    if not rest.startswith(message_id):
        return None
    rest = rest[len(message_id):].lstrip()
    try:
        payload = json.loads(rest)
    except JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return AddonBridgeRequest.model_validate(payload)
    except ValueError:
        return None


class AddonBridgeChunk(BaseModel):
    """从聊天消息解析出的桥接响应分片。"""

    request_id: str
    chunk_index: int
    total_chunks: int
    content: str


class AddonBridgeResponse(BaseModel):
    """重组后的桥接响应。"""

    request_id: str
    payload: dict[str, Any]


class UiChatChunk(BaseModel):
    """从聊天消息解析出的 UI 聊天分片。"""

    msg_id: str
    chunk_index: int
    total_chunks: int
    content: str


class UiChatMessage(BaseModel):
    """重组后的 UI 聊天消息。"""

    msg_id: str
    player_name: str
    message: str
