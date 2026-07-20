"""Protocol profiles for MCBE wire compatibility layers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1, McbewsV1Profile


@runtime_checkable
class AddonBridgeProfile(Protocol):
    """Wire-format contract for addon bridge interop."""

    @property
    def bridge_request_message_id(self) -> str: ...
    @property
    def bridge_response_prefix(self) -> str: ...
    @property
    def ui_chat_prefix(self) -> str: ...
    @property
    def bridge_sender(self) -> str: ...
    @property
    def response_message_id(self) -> str: ...
    @property
    def request_version(self) -> int: ...
    @property
    def response_chunk_delay(self) -> float: ...
    @property
    def response_prelude_delay(self) -> float: ...


__all__ = [
    "AddonBridgeProfile",
    "MCBEWS_V1",
    "McbewsV1Profile",
]
