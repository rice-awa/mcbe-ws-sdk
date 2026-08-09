"""MCBEWS/1 protocol profile exports.

MCBEWS/1 is the sole runtime profile.  ``AddonBridgeProfile`` is retained as a
deprecated type alias/re-export for source compatibility; it is not a runtime
extension seam and SDK settings accept the concrete profile only.
"""

from __future__ import annotations

from typing import TypeAlias

from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1, McbewsV1Profile

# Deprecated type alias/re-export; this intentionally has no runtime Protocol
# or replaceable-profile contract.
AddonBridgeProfile: TypeAlias = McbewsV1Profile
McbewsV1Protocol = McbewsV1Profile

__all__ = [
    "AddonBridgeProfile",
    "MCBEWS_V1",
    "McbewsV1Protocol",
    "McbewsV1Profile",
]
