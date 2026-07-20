"""Legacy mcbeai v1 protocol profile.

``LegacyMcbeAiV1Delivery`` and ``encode_legacy_response_commands`` are
importable from ``delivery`` and ``codec`` submodules respectively rather
than re-exported here, because the addon session -> config -> profiles
import chain would introduce a circular dependency.
"""

from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import LEGACY_MCBEAI_V1, LegacyMcbeAiV1Profile

__all__ = [
    "LEGACY_MCBEAI_V1",
    "LegacyMcbeAiV1Profile",
]
