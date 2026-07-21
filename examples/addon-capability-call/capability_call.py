"""Complete in-memory round trip through the mcbe-ws-sdk public API.

Simulates an addon bridge request/response cycle without any real WebSocket
transport.  ``send_command`` captures the outbound bridge request, synthesises
a matching response chat message, and feeds it back into the service — the
client's pending future resolves with the mocked server reply.

Usage::

    python examples/addon-capability-call/capability_call.py

Expected output (stdout)::

    {"ok":true,"greeting":"Hello, Steve"}
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from mcbe_ws_sdk.addon import AddonBridgeService, AddonBridgeSettings
from mcbe_ws_sdk.profiles import MCBEWS_V1


async def main() -> None:
    connection_id = UUID(int=1)
    service = AddonBridgeService(AddonBridgeSettings())

    async def send_command(command: str) -> None:
        _, message_id, encoded = command.split(" ", 2)
        assert message_id == MCBEWS_V1.bridge_request_message_id
        request = json.loads(encoded)
        response = json.dumps(
            {"ok": True, "greeting": f"Hello, {request['payload']['name']}"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        chat = f"{MCBEWS_V1.bridge_response_prefix}|{request['request_id']}|1/1|{response}"
        result = await service.handle_player_message(
            connection_id,
            MCBEWS_V1.bridge_sender,
            chat,
        )
        assert result.handled

    client = service.create_client(connection_id, send_command)
    result = await client.request("greet", {"name": "Steve"})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
