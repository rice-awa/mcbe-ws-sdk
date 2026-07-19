from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import LegacyMcbeAiV1Profile

AddonBridgeProfile = LegacyMcbeAiV1Profile


def default_addon_bridge_profile() -> AddonBridgeProfile:
    return LegacyMcbeAiV1Profile()
