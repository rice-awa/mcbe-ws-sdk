"""Protocol profiles for MCBE wire compatibility layers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import LEGACY_MCBEAI_V1, LegacyMcbeAiV1Profile


@runtime_checkable
class AddonBridgeProfile(Protocol):
    """Protocol defining the wire-format contract for addon bridge interop.

    Concrete profiles (e.g. :class:`LegacyMcbeAiV1Profile`) satisfy this
    protocol structurally; no inheritance is required.

    All members are declared as read-only properties so that frozen
    dataclass implementations satisfy the protocol.
    """

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
    "LEGACY_MCBEAI_V1",
    "LegacyMcbeAiV1Profile",
]
