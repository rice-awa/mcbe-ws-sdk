"""Addon bridge service.

Relocated from the main repo ``services/addon/service.py``

Changes vs. the original:

* No module-level ``_addon_bridge_service`` singleton and no
  ``get_addon_bridge_service()`` factory. The service is constructed with an
  explicit :class:`AddonBridgeSettings` and any number of independent instances
  may coexist.
* Timeout and protocol are taken from ``AddonBridgeSettings`` instead of a
  mutable global settings read.
* ``is_bridge_chat_message`` / ``is_ui_chat_message`` use the configured protocol
  rather than an implicit ``_protocol()`` global.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

import structlog

from mcbe_ws_sdk.addon.protocol import encode_bridge_request
from mcbe_ws_sdk.addon.session import AddonBridgeSession
from mcbe_ws_sdk.config import AddonBridgeSettings

logger = structlog.get_logger(__name__)

CommandSender = Callable[[str], Awaitable[str]]
UiChatCallback = Callable[[UUID, str, str], Awaitable[None]]


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
        self._protocol = settings.protocol
        self._ui_chat_callback: UiChatCallback | None = None

    def create_client(
        self,
        connection_id: UUID,
        send_command: CommandSender,
    ) -> AddonBridgeClient:
        """Build a client bound to one connection."""
        return _ConnectionAddonBridgeClient(self, connection_id, send_command)

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
            protocol=self._protocol,
        )

        command_result = await send_command(command)
        if command_result.startswith("命令执行失败") or command_result.startswith("命令执行超时"):
            session.fail_request(request.request_id, command_result)
            raise RuntimeError(command_result)

        try:
            return await asyncio.wait_for(
                request.future,
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            session.fail_request(request.request_id, "Addon 桥接响应超时")
            raise RuntimeError("Addon 桥接响应超时") from exc

    def is_bridge_chat_message(self, sender: str, message: str) -> bool:
        """True if the player chat message is an addon response fragment."""
        return (
            sender == self._protocol.bridge_tool_player_name
            and message.startswith(self._protocol.bridge_prefix)
        )

    def is_ui_chat_message(self, sender: str, message: str) -> bool:
        """True if the player chat message is a UI chat fragment."""
        return (
            sender == self._protocol.bridge_tool_player_name
            and message.startswith(self._protocol.ui_chat_prefix)
        )

    def handle_player_message(self, connection_id: UUID, sender: str, message: str) -> bool:
        """Route a routed message from the simulated player."""
        if self.is_bridge_chat_message(sender, message):
            session = self._sessions.get(connection_id)
            if session is None:
                logger.warning(
                    "bridge_chat_no_session",
                    connection_id=str(connection_id),
                    sender=sender,
                    message_prefix=message[:50] if message else "",
                )
                return False

            return session.handle_chat_chunk(message)

        if self.is_ui_chat_message(sender, message):
            logger.debug(
                "ui_chat_chunk_received",
                connection_id=str(connection_id),
                sender=sender,
                message_prefix=message[:50] if message else "",
            )
            session = self._session_for(connection_id)

            result = session.handle_ui_chat_chunk(message)
            if result is not None and self._ui_chat_callback is not None:
                player_name, chat_message = result
                logger.info(
                    "ui_chat_reassembled",
                    connection_id=str(connection_id),
                    player=player_name,
                    message_length=len(chat_message),
                    callback_registered=True,
                )
                ui_chat_coroutine = self._ui_chat_callback(connection_id, player_name, chat_message)
                asyncio.create_task(ui_chat_coroutine)  # type: ignore[arg-type]
            elif result is None:
                logger.debug(
                    "ui_chat_chunk_buffered",
                    connection_id=str(connection_id),
                    message_prefix=message[:50] if message else "",
                )
            return True

        return False

    def set_ui_chat_callback(self, callback: UiChatCallback) -> None:
        """Register the UI chat message callback."""
        self._ui_chat_callback = callback

    def close_connection(self, connection_id: UUID) -> None:
        """Tear down the per-connection session on disconnect."""
        session = self._sessions.pop(connection_id, None)
        if session is not None:
            session.close("Addon 桥接连接已关闭")

    def _session_for(self, connection_id: UUID) -> AddonBridgeSession:
        session = self._sessions.get(connection_id)
        if session is None:
            session = AddonBridgeSession(protocol=self._protocol)
            self._sessions[connection_id] = session
        return session


class _ConnectionAddonBridgeClient:
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
