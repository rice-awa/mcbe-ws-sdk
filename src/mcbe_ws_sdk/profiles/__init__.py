"""Protocol profiles for MCBE wire compatibility layers."""

from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import LEGACY_MCBEAI_V1, LegacyMcbeAiV1Profile

# Backward-compat alias — kept for internal use only, not in public __all__
AddonBridgeProfile = LegacyMcbeAiV1Profile

__all__ = [
    "LEGACY_MCBEAI_V1",
    "LegacyMcbeAiV1Profile",
]
