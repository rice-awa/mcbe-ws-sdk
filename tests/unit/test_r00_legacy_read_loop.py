"""R00 baseline pins for the legacy per-connection read loop.

R00 fixes the current behaviour before R01 changes the dispatch model. The
legacy read loop in :meth:`McbeServerFacade._on_connection` awaits every host
hook inline, so a hook that waits for an addon bridge reply blocks the only
reader of that connection: the reply frame cannot be read until the hook
returns, which makes the request time out.

The deterministic reproduction below uses a scripted transport instead of a
sleep: the transport only releases the addon reply frame after it has actually
observed the outbound bridge request, and the hook records how many frames the
transport had yielded when the request finished. No timing guess decides the
outcome.

``test_legacy_read_loop_blocks_while_hook_awaits_addon_reply`` is marked
``xfail(strict=True)`` on purpose. It pins the *desired* contract (the addon
reply resolves the request even when it was issued from a callback). R01 must
make it pass and then remove the ``xfail`` marker; a strict xfail turns an
unexpected pass into a failure so the marker cannot be forgotten.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from mcbe_ws_sdk.addon.service import AddonBridgeService
from mcbe_ws_sdk.config import AddonBridgeSettings, DispatchSettings, GatewaySettings
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.profiles.mcbews_v1.profile import MCBEWS_V1
from mcbe_ws_sdk.protocol.minecraft import PlayerMessageEvent

_CAPABILITY = "inventory.get"
_REQUEST_SCRIPT_EVENT_PREFIX = f"scriptevent {MCBEWS_V1.capability_request_script_event_id} "


class ScriptedBridgeTransport:
    """Transport that releases a bridge reply only after the request was sent.

    Frame order is fixed and driven by the connection's own progress:

    1. the player message that makes the hook issue the bridge request;
    2. the bridge reply, available only after ``send`` observed the request;
    3. end of stream.
    """

    def __init__(self, *, wait_after_reply: asyncio.Event | None = None) -> None:
        self.sent: list[str] = []
        self.frames_read = 0
        self._reply_frame: str | None = None
        self._reply_available = asyncio.Event()
        self._wait_after_reply = wait_after_reply

    def _player_message_frame(self) -> str:
        return json.dumps(
            {
                "header": {"eventName": "PlayerMessage", "requestId": "r00-evt-1"},
                "body": {"sender": "Alice", "message": "query inventory"},
            }
        )

    async def send(self, payload: str) -> None:
        self.sent.append(payload)
        if not payload.startswith(_REQUEST_SCRIPT_EVENT_PREFIX):
            return
        body = json.loads(payload[len(_REQUEST_SCRIPT_EVENT_PREFIX) :])
        request_id = body["request_id"]
        content = json.dumps({"ok": True}, separators=(",", ":"))

        message = f"{MCBEWS_V1.capability_response_chat_prefix}|{request_id}|1/1|{content}"
        self._reply_frame = json.dumps(
            {
                "header": {"eventName": "PlayerMessage", "requestId": "r00-evt-2"},
                "body": {"sender": MCBEWS_V1.trusted_bridge_player_name, "message": message},
            }
        )
        self._reply_available.set()

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter_frames()

    async def _iter_frames(self) -> AsyncIterator[str]:
        self.frames_read += 1
        yield self._player_message_frame()
        self.frames_read += 1
        await self._reply_available.wait()
        assert self._reply_frame is not None
        yield self._reply_frame
        self.frames_read += 1
        if self._wait_after_reply is not None:
            # Keep the stream open until the business task has recorded its
            # outcome, so end-of-stream teardown cannot cancel it mid-flight.
            await self._wait_after_reply.wait()


class AwaitingHook(NoOpHook):
    """Host hook that synchronously waits for an addon reply."""

    def __init__(self, service: AddonBridgeService, transport: ScriptedBridgeTransport) -> None:
        self._service = service
        self._transport = transport
        self.frames_read_before = 0
        self.frames_read_after = 0
        self.outcome: dict[str, Any] | Exception | None = None
        self.messages: list[PlayerMessageEvent] = []
        self.finished = asyncio.Event()

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: object | None = None,
    ) -> None:
        self.messages.append(player_event)
        client = self._service.create_client(state.id, _require_send(state))
        self.frames_read_before = self._transport.frames_read
        try:
            self.outcome = await client.request(_CAPABILITY, {"player": player_event.sender})
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion
            self.outcome = exc
        finally:
            self.frames_read_after = self._transport.frames_read
            self.finished.set()


def _require_send(state: ConnectionState) -> Any:
    assert state.send_payload is not None
    return state.send_payload


def _build(
    transport: ScriptedBridgeTransport,
    *,
    reliable: bool = False,
) -> tuple[McbeServerFacade, AwaitingHook]:
    service = AddonBridgeService(AddonBridgeSettings(timeout_seconds=0.2))
    hook = AwaitingHook(service, transport)
    settings = GatewaySettings(
        dispatch=DispatchSettings(mode="reliable" if reliable else "legacy"),
    )
    facade = McbeServerFacade(
        settings=settings,
        hook=hook,
        addon=service,
    )
    return facade, hook


async def test_reliable_read_loop_serves_addon_reply_issued_from_callback() -> None:
    """R00-3 regression: the reliable path must not block the reader on a hook.

    R00 saved this as a strict xfail against the legacy serial reader. R01 makes
    it pass on the explicitly enabled reliable path; the legacy reader keeps its
    own pinned behaviour in the next test.
    """

    transport = ScriptedBridgeTransport()
    facade, hook = _build(transport, reliable=True)
    transport._wait_after_reply = hook.finished

    await asyncio.wait_for(facade._on_connection(transport), timeout=5)

    assert hook.outcome == {"ok": True}
    # The reply frame was read while the callback was still waiting. The legacy
    # reader pins ``frames_read_after == 1``; the reliable reader has pulled at
    # least the reply frame (2) by then.
    assert hook.frames_read_after >= 2


async def test_legacy_read_loop_does_not_read_ahead_while_hook_waits() -> None:
    """Pin the legacy serial order: no second frame is read during the callback."""

    transport = ScriptedBridgeTransport()
    facade, hook = _build(transport)

    await asyncio.wait_for(facade._on_connection(transport), timeout=5)

    assert len(hook.messages) == 1
    assert hook.frames_read_before == 1
    # The reply frame is still unread when the inline callback returns.
    assert hook.frames_read_after == 1
    assert transport.frames_read == 3
