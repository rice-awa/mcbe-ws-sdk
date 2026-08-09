"""Addon bridge service.

Relocated from the main repo ``services/addon/service.py``

Changes vs. the original:

* No module-level ``_addon_bridge_service`` singleton and no
  ``get_addon_bridge_service()`` factory. The service is constructed with an
  explicit :class:`AddonBridgeSettings` and any number of independent instances
  may coexist.
* Timeout and profile are taken from ``AddonBridgeSettings`` instead of a
  mutable global settings read.
* ``is_bridge_chat_message`` / ``is_ui_chat_message`` use the configured profile.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import structlog

from mcbe_ws_sdk.addon.session import AddonBridgeSession
from mcbe_ws_sdk.config import AddonBridgeSettings
from mcbe_ws_sdk.errors import BridgeTimeoutError
from mcbe_ws_sdk.profiles.mcbews_v1.classifier import (
    ToolPlayerMessage,
    classify_tool_player_message,
    is_tool_player_channel_message,
)
from mcbe_ws_sdk.profiles.mcbews_v1.codec import encode_bridge_request
from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    AddonBridgeChunk,
    UiChatChunk,
    UiChatMessage,
)

logger = structlog.get_logger(__name__)

CommandSender = Callable[[str], Awaitable[None]]
UiChatCallback = Callable[[UUID, UiChatMessage], Awaitable[None]]
LegacyUiChatCallback = Callable[[UUID, str, str], Awaitable[None]]


class LegacyUiChatCallbackAdapter:
    """Adapt the pre-0.2.0 three-argument callback to the typed DTO callback."""

    def __init__(self, callback: LegacyUiChatCallback) -> None:
        self._callback = callback

    async def __call__(self, connection_id: UUID, message: UiChatMessage) -> None:
        await self._callback(connection_id, message.player_name, message.message)


@dataclass(frozen=True)
class AddonMessageResult:
    handled: bool
    bridge_chunk: AddonBridgeChunk | None = None
    ui_chunk: UiChatChunk | None = None
    ui_message: UiChatMessage | None = None
    control_message: ToolPlayerMessage | None = None


class AddonBridgeClient(Protocol):
    """Face an Agent uses to reach the addon bridge."""

    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Issue a capability request and return the reassembled payload."""


class AddonBridgeService:
    """Manage the request/response lifecycle between Python and the addon."""

    def __init__(self, settings: AddonBridgeSettings) -> None:
        self._settings = settings
        self._sessions: dict[UUID, AddonBridgeSession] = {}
        self._timeout_seconds = settings.timeout_seconds
        self._profile = settings.profile
        self._ui_chat_callback: UiChatCallback | None = None

    def create_client(
        self,
        connection_id: UUID,
        send_command: CommandSender,
    ) -> AddonBridgeClient:
        """Build a client bound to one connection."""
        return ConnectionAddonBridgeClient(self, connection_id, send_command)

    async def request_capability(
        self,
        connection_id: UUID,
        capability: str,
        payload: dict[str, Any],
        send_command: CommandSender,
    ) -> dict[str, Any]:
        """Send a bridge request and wait for the addon's chat callback."""
        session = self._session_for(connection_id)
        request = session.create_request(capability=capability, payload=payload)
        command = encode_bridge_request(
            request_id=request.request_id,
            capability=capability,
            payload=payload,
            profile=self._profile,
        )
        payload_bytes = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        # INFO stays free of payload/command content (may contain player data).
        logger.info(
            "bridge_request_outbound",
            request_id=request.request_id,
            channel="capability",
            payload_bytes=payload_bytes,
        )
        logger.debug(
            "bridge_request_outbound",
            connection_id=str(connection_id),
            request_id=request.request_id,
            capability=capability,
            command_bytes=len(command.encode("utf-8")),
            timeout_seconds=self._timeout_seconds,
        )
        try:
            try:
                await send_command(command)
            except Exception as exc:
                # Fail-fast before waiting: host rejected the outbound frame
                # (e.g. FrameTooLarge) or the connection is gone. Waiting for a
                # RESP that can never arrive only produces a misleading timeout.
                logger.warning(
                    "bridge_request_send_failed",
                    connection_id=str(connection_id),
                    request_id=request.request_id,
                    channel="capability",
                    payload_bytes=payload_bytes,
                    error_type=type(exc).__name__,
                )
                raise
            logger.info(
                "bridge_request_sent",
                connection_id=str(connection_id),
                request_id=request.request_id,
                channel="capability",
                payload_bytes=payload_bytes,
            )
            try:
                result = await asyncio.wait_for(request.future, self._timeout_seconds)
            except TimeoutError as exc:
                logger.warning(
                    "bridge_request_timeout",
                    connection_id=str(connection_id),
                    request_id=request.request_id,
                    channel="capability",
                    payload_bytes=payload_bytes,
                )
                raise BridgeTimeoutError(request.request_id) from exc
            logger.info(
                "bridge_request_resolved",
                connection_id=str(connection_id),
                request_id=request.request_id,
                channel="capability",
                result_type=type(result).__name__,
            )
            logger.debug(
                "bridge_request_resolved_payload",
                connection_id=str(connection_id),
                request_id=request.request_id,
                capability=capability,
                result_type=type(result).__name__,
            )
            return result
        finally:
            session.cancel_request(request.request_id)

    def is_bridge_chat_message(self, sender: str, message: str) -> bool:
        """True if the player chat message is an addon response fragment."""
        return sender == self._profile.trusted_bridge_player_name and message.startswith(
            f"{self._profile.capability_response_chat_prefix}|"
        )

    def is_ui_chat_message(self, sender: str, message: str) -> bool:
        """True if the player chat message is a UI chat fragment."""
        return sender == self._profile.trusted_bridge_player_name and message.startswith(
            f"{self._profile.ui_chat_chunk_prefix}|"
        )

    def is_tool_player_channel_message(self, message: str) -> bool:
        """Return whether *message* is reserved for a ToolPlayer channel."""

        return is_tool_player_channel_message(message, profile=self._profile)

    def classify_tool_player_message(self, sender: str, message: str) -> ToolPlayerMessage | None:
        """Authenticate and classify a reserved ToolPlayer message."""

        return classify_tool_player_message(sender, message, profile=self._profile)

    async def handle_player_message(
        self, connection_id: UUID, sender: str, message: str
    ) -> AddonMessageResult:
        """Route a routed message from the simulated player."""
        if self.is_bridge_chat_message(sender, message):
            # Keep INFO body-free: these frames never reach the host hook because
            # the facade short-circuits bridge frames before on_player_message.
            logger.info(
                "bridge_chat_chunk_raw",
                connection_id=str(connection_id),
                channel="capability",
                message_bytes=len(message.encode("utf-8")),
            )
            session = self._sessions.get(connection_id)
            if session is None:
                logger.warning(
                    "bridge_chat_no_session",
                    connection_id=str(connection_id),
                    channel="capability",
                )
                return AddonMessageResult(handled=True)

            bridge_chunk = session.handle_chat_chunk(message)
            return AddonMessageResult(handled=True, bridge_chunk=bridge_chunk)

        if self.is_ui_chat_message(sender, message):
            logger.info(
                "ui_chat_chunk_raw",
                connection_id=str(connection_id),
                channel="ui_chat",
                message_bytes=len(message.encode("utf-8")),
            )
            session = self._session_for(connection_id)

            ui_chunk, ui_message = session.handle_ui_chat_chunk(message)
            if ui_message is not None and self._ui_chat_callback is not None:
                logger.info(
                    "ui_chat_reassembled",
                    connection_id=str(connection_id),
                    channel="ui_chat",
                    message_bytes=len(ui_message.message.encode("utf-8")),
                )
                await self._invoke_ui_chat_callback(connection_id, ui_message)
            return AddonMessageResult(
                handled=True,
                ui_chunk=ui_chunk,
                ui_message=ui_message,
            )

        if self.is_tool_player_channel_message(message):
            # Session and approval frames are decoded only after the trusted
            # sender gate.  The facade uses this branch to deliver typed control
            # messages to the Host hook; they never fall through as player chat.
            control_message = self.classify_tool_player_message(sender, message)
            if control_message is None:
                return AddonMessageResult(handled=True)
            return AddonMessageResult(handled=True, control_message=control_message)

        return AddonMessageResult(handled=False)

    def set_ui_chat_callback(
        self,
        callback: UiChatCallback,
    ) -> None:
        """Register the typed UI chat message callback."""
        self._ui_chat_callback = callback

    def set_legacy_ui_chat_callback(self, callback: LegacyUiChatCallback) -> None:
        """Register a legacy callback through an explicit migration adapter."""
        self._ui_chat_callback = LegacyUiChatCallbackAdapter(callback)

    async def _invoke_ui_chat_callback(self, connection_id: UUID, message: UiChatMessage) -> None:
        """Invoke the registered typed callback."""

        callback = self._ui_chat_callback
        if callback is not None:
            await callback(connection_id, message)

    def close_connection(self, connection_id: UUID) -> None:
        """Tear down the per-connection session on disconnect."""
        session = self._sessions.pop(connection_id, None)
        if session is not None:
            session.close()

    def _session_for(self, connection_id: UUID) -> AddonBridgeSession:
        session = self._sessions.get(connection_id)
        if session is None:
            session = AddonBridgeSession(self._settings)
            self._sessions[connection_id] = session
        return session


class ConnectionAddonBridgeClient:
    """Per-connection client bound to one bridge service instance."""

    def __init__(
        self,
        service: AddonBridgeService,
        connection_id: UUID,
        send_command: CommandSender,
    ) -> None:
        self._service = service
        self._connection_id = connection_id
        self._send_command = send_command

    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._service.request_capability(
            connection_id=self._connection_id,
            capability=capability,
            payload=payload,
            send_command=self._send_command,
        )
