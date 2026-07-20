from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from mcbe_ws_sdk.delivery.outbound import McbeOutboundDelivery
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.codec import encode_legacy_response_commands
from mcbe_ws_sdk.profiles.legacy_mcbeai_v1.profile import (
    LEGACY_MCBEAI_V1,
    LegacyMcbeAiV1Profile,
)

Sleeper = Callable[[float], Awaitable[None]]


class LegacyMcbeAiV1Delivery:
    def __init__(
        self,
        outbound: McbeOutboundDelivery,
        *,
        profile: LegacyMcbeAiV1Profile = LEGACY_MCBEAI_V1,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._outbound = outbound
        self._profile = profile
        self._sleep = sleeper

    async def send_response(
        self,
        *,
        player_name: str,
        role: str,
        text: str,
        response_id: str | None = None,
    ) -> int:
        payloads = encode_legacy_response_commands(
            player_name=player_name,
            role=role,
            text=text,
            flow=self._outbound.flow,
            response_id=response_id,
            profile=self._profile,
        )
        await self._sleep(self._profile.response_prelude_delay)
        await self._outbound.send_chunked(payloads, "ai_resp", "legacy_mcbeai_v1_response")
        return len(payloads)
