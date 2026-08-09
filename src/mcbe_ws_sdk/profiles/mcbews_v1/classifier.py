"""Trusted ToolPlayer channel classification for MCBEWS/1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mcbe_ws_sdk.profiles.mcbews_v1.codec import (
    decode_approval_decision,
    decode_session_request_chat,
)
from mcbe_ws_sdk.profiles.mcbews_v1.models import ApprovalDecision, SessionRequest
from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1, McbewsV1Profile

ToolPlayerChannel = Literal["bridge", "ui_chat", "session", "approval"]


@dataclass(frozen=True, slots=True)
class ToolPlayerMessage:
    """A control-channel message accepted only from the trusted ToolPlayer."""

    channel: ToolPlayerChannel
    sender: str
    raw_message: str
    session_request: SessionRequest | None = None
    approval_decision: ApprovalDecision | None = None


def is_tool_player_channel_message(
    message: str,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> bool:
    """Return whether a message uses a reserved MCBEWS/1 control prefix."""

    return any(
        message.startswith(prefix)
        for prefix in (
            f"{profile.capability_response_chat_prefix}|",
            f"{profile.ui_chat_chunk_prefix}|",
            f"{profile.session_request_chat_prefix}|",
            f"{profile.approval_allow_chat_prefix}|",
            f"{profile.approval_deny_chat_prefix}|",
        )
    )


def classify_tool_player_message(
    sender: str,
    message: str,
    profile: McbewsV1Profile = MCBEWS_V1,
) -> ToolPlayerMessage | None:
    """Authenticate and classify one ToolPlayer chat message.

    ``None`` means either that the sender is not the trusted ToolPlayer or the
    message is ordinary player chat.  Malformed reserved messages raise a typed
    :class:`ProtocolError`; callers can log/drop them without passing them to a
    business hook.
    """

    if sender != profile.trusted_bridge_player_name:
        return None
    if message.startswith(f"{profile.capability_response_chat_prefix}|"):
        return ToolPlayerMessage("bridge", sender, message)
    if message.startswith(f"{profile.ui_chat_chunk_prefix}|"):
        return ToolPlayerMessage("ui_chat", sender, message)
    if message.startswith(f"{profile.session_request_chat_prefix}|"):
        request = decode_session_request_chat(message, profile=profile)
        return ToolPlayerMessage("session", sender, message, session_request=request)
    if message.startswith(f"{profile.approval_allow_chat_prefix}|") or message.startswith(
        f"{profile.approval_deny_chat_prefix}|"
    ):
        decision = decode_approval_decision(message, profile=profile)
        return ToolPlayerMessage("approval", sender, message, approval_decision=decision)
    return None


__all__ = [
    "ToolPlayerChannel",
    "ToolPlayerMessage",
    "classify_tool_player_message",
    "is_tool_player_channel_message",
]
