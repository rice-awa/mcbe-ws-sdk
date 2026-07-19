"""Protocol profiles for MCBE wire compatibility layers."""

from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import LegacyMcbeAiV1Profile

AddonBridgeProfile = LegacyMcbeAiV1Profile

__all__ = ["AddonBridgeProfile", "LegacyMcbeAiV1Profile"]
