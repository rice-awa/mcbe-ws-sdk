from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from mcbe_ws_sdk.delivery.outbound import McbeOutboundDelivery
from mcbe_ws_sdk.profiles.mcbews_v1.codec import (
    encode_session_response_command,
    encode_text_response_commands,
)
from mcbe_ws_sdk.profiles.mcbews_v1.models import SessionResponse
from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1, McbewsV1Profile

Sleeper = Callable[[float], Awaitable[None]]


class McbewsV1Delivery:
    def __init__(
        self,
        outbound: McbeOutboundDelivery,
        *,
        profile: McbewsV1Profile = MCBEWS_V1,
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
        conversation_id: str | None = None,
        title: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> int:
        payloads = encode_text_response_commands(
            player_name=player_name,
            role=role,
            text=text,
            flow=self._outbound.flow,
            response_id=response_id,
            conversation_id=conversation_id,
            title=title,
            usage=usage,
            profile=self._profile,
        )
        await self._sleep(self._profile.response_prelude_delay)
        await self._outbound.send_chunked(
            payloads,
            "text_resp",
            "mcbews_v1_text_resp",
            delay=self._profile.response_chunk_delay,
        )
        return len(payloads)

    async def send_session_response(self, response: SessionResponse) -> None:
        """Send one atomic correlated session response.

        Session responses deliberately bypass generic scriptevent chunking. A
        result that does not fit is encoded as one ``SESSION_RESPONSE_TOO_LARGE``
        error by :func:`encode_session_response_command`.
        """

        payload = encode_session_response_command(
            response,
            self._outbound.flow,
            profile=self._profile,
        )
        await self._outbound.send_payload(payload, "session_resp")
