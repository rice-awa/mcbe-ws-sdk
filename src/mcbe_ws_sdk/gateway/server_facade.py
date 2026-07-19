"""Gateway entry point: the facade the host drives the SDK stack through.

:class:`McbeServerFacade` OWNS the WebSocket transport and the connection /
protocol machinery (the :class:`~mcbe_ws_sdk.gateway.connection.ConnectionManager`
response-sender loops, the
:class:`~mcbe_ws_sdk.gateway.handler.MinecraftProtocolHandler` command parser,
and the per-connection
:class:`~mcbe_ws_sdk.addon.service.AddonBridgeService` sessions) but deliberately
does NOT own any host application concern: there is no ``MessageBroker``, no LLM
worker, no login/JWT flow, no provider selection. All of that is injected by the
host via the :class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook` lifecycle hooks
and the :class:`~mcbe_ws_sdk.gateway.sink.ResponseSink` outbound routes — the
same dependency-inversion the gateway already applies internally (see
:class:`NoOpHook`, :class:`SilentResponseSink`).

It mirrors the current main-repo ``WebSocketServer`` orchestration
(``services/websocket/server.py``) — accept → handshake → subscribe →
welcome → message loop → disconnect → shutdown — but inverted: the server
*receives* its collaborators; it does not build the host-only ones.

Lifetime::

    facade = McbeServerFacade(hook=my_hook, sink=my_sink)
    await facade.run_lifetime()            # blocks until ``stop()`` / cancelled
    # ... or drive it on an explicit host asyncio.Task and cancel it.

See ``docs/batch-d-scope.md`` section B for the authoritative spec.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import websockets

from mcbe_ws_sdk._logging import get_logger
from mcbe_ws_sdk.addon import AddonBridgeService
from mcbe_ws_sdk.capability import CapabilityRegistry
from mcbe_ws_sdk.command import CommandRegistry
from mcbe_ws_sdk.command.registry import DEFAULT_COMMANDS
from mcbe_ws_sdk.config import GatewaySettings
from mcbe_ws_sdk.errors import ProtocolError
from mcbe_ws_sdk.gateway.connection import ConnectionManager, ConnectionState, SendPayload
from mcbe_ws_sdk.gateway.events import EventBus, WsEventType
from mcbe_ws_sdk.gateway.handler import MessageSurfaceConfig, MinecraftProtocolHandler
from mcbe_ws_sdk.gateway.hook import ConnectionHook, NoOpHook
from mcbe_ws_sdk.gateway.sink import ResponseSink, SilentResponseSink
from mcbe_ws_sdk.protocol.addon import parse_addon_bridge_request
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommandResponse, MinecraftErrorFrame

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

logger = get_logger(__name__)


class McbeServerFacade:
    """Owns the WS transport + connection/protocol machinery; host injects the rest.

    The constructor takes ONLY keyword arguments and never builds a broker. Each
    ``None`` collapses to a gateway default so a host can stand up a working
    facade with ``McbeServerFacade()`` and override collaborators one at a time.
    """

    def __init__(
        self,
        *,
        settings: GatewaySettings | None = None,
        hook: ConnectionHook | None = None,
        sink: ResponseSink | None = None,
        addon: AddonBridgeService | None = None,
        registry: CommandRegistry | None = None,
        capabilities: CapabilityRegistry | None = None,
    ) -> None:
        self._settings = settings if settings is not None else GatewaySettings()
        self._hook = hook if hook is not None else NoOpHook()
        self._sink = sink if sink is not None else SilentResponseSink()
        self._registry = registry if registry is not None else CommandRegistry(DEFAULT_COMMANDS)
        self._capabilities = (
            capabilities if capabilities is not None else CapabilityRegistry()
        )

        self._handler = MinecraftProtocolHandler(self._registry, surface=MessageSurfaceConfig())
        self._addon = addon if addon is not None else AddonBridgeService(self._settings.addon)
        self._addon.set_ui_chat_callback(self._on_ui_chat_reassembled)

        self._manager = ConnectionManager(sink=self._sink, event_bus=EventBus())

        self._stopped = asyncio.Event()
        self._server: Any = None

    # -- public manipulables (read-friendly; tests/asserts use these) ------------

    @property
    def manager(self) -> ConnectionManager:
        """The facade's owned connection manager."""
        return self._manager

    @property
    def handler(self) -> MinecraftProtocolHandler:
        """The facade's owned protocol handler (command parser + renderer)."""
        return self._handler

    @property
    def addon(self) -> AddonBridgeService:
        """The facade's owned addon-bridge service instance."""
        return self._addon

    @property
    def settings(self) -> GatewaySettings:
        return self._settings

    # -- lifetime ---------------------------------------------------------------

    async def run_lifetime(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        """Bind a WebSocket server and serve until :meth:`stop` / task cancellation.

        ``host``/``port`` default to ``settings.websocket.host`` / ``port`` when
        ``None``. Uses the ``websockets`` async-context-manager API
        (``async with serve(...) as server``), which works on both ``>=12`` and
        the v13+ ``serve`` shape. Blocks on an internal ``asyncio.Event`` so an
        explicit ``stop()`` returns cleanly; the outer task being cancelled also
        unwinds into :meth:`_graceful_shutdown`.
        """
        serve_host = host if host is not None else self._settings.websocket.host
        serve_port = port if port is not None else self._settings.websocket.port
        transport = self._settings.websocket

        async with websockets.serve(
            self._on_connection,
            serve_host,
            serve_port,
            ping_interval=transport.ping_interval,
            ping_timeout=transport.ping_timeout,
            close_timeout=transport.close_timeout,
            max_size=transport.max_size,
            max_queue=transport.max_queue,
        ) as server:
            self._server = server
            logger.info(
                "facade_listening",
                host=serve_host,
                port=serve_port,
            )
            try:
                await self._stopped.wait()
            finally:
                await self._graceful_shutdown()

        self._server = None

    async def stop(self) -> None:
        """Signal :meth:`run_lifetime` to unwind (idempotent)."""
        self._stopped.set()

    async def _graceful_shutdown(self) -> None:
        """Drop every connection (cancels each response-sender → ``DISCONNECTED``)."""
        logger.info("facade_graceful_shutdown")
        await self._manager.shutdown_all()
        # The ``websockets.serve`` context manager closes + ``wait_closed``s the
        # server on ``__aexit__``; nothing transport-specific to do here.

    # -- per-connection protocol driver -----------------------------------------

    async def _on_connection(self, websocket: ServerConnection) -> None:
        """Per-connection protocol driver: handshake → message loop → teardown."""
        connection_id = uuid4()
        state = await self._manager.create_connection(
            connection_id=connection_id,
            send_payload=self._wrap_send(websocket, connection_id=connection_id),
        )

        try:
            assert state.send_payload is not None
            await state.send_payload('{"Result":"true"}')
            await state.send_payload(self._handler.create_subscribe_message())
            await self._hook.on_connected(state)

            async for raw in websocket:
                await self._handle_raw(state, raw)
        except websockets.ConnectionClosed:
            logger.info("connection_closed", connection_id=str(state.id))
        except Exception:
            logger.exception("connection_error", connection_id=str(state.id))
        finally:
            if self._addon is not None:
                self._addon.close_connection(state.id)
            await self._manager.drop_connection(state.id)
            await self._hook.on_disconnected(state)

    async def _handle_raw(self, state: ConnectionState, raw: str | bytes) -> None:
        """Route one inbound WS frame to the right branch."""
        await self._manager.event_bus.emit(WsEventType.RAW_INBOUND, state, raw)

        data: dict[str, Any] | None = None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning(
                "invalid_json",
                connection_id=str(state.id),
                frame_type=type(raw).__name__,
                utf8_byte_size=len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw),
            )
            return

        if not isinstance(data, dict):
            # Non-object frame (e.g. a JSON array): nothing we can route on.
            logger.warning(
                "non_object_frame_dropped",
                connection_id=str(state.id),
                frame_type=type(data).__name__,
            )
            return

        if self._is_error_frame(data):
            error = self._extract_error_frame(data)
            await self._manager.event_bus.emit(WsEventType.ERROR, state, error)
            await self._hook.on_error(state, error)
            return

        # Branch C — commandResponse: host-side command-execution future plumbing.
        # We only detect the frame + forward to the hook; there is intentionally
        # no ``pending_command_futures`` map on the state (that is host-owned).
        if self._is_command_response(data):
            response = self._extract_command_response(data)
            await self._manager.event_bus.emit(WsEventType.COMMAND_RESPONSE, state, response)
            await self._hook.on_command_response(state, response)
            return

        # Parse a PlayerMessage event (returns None for any other event kind).
        event = self._handler.parse_player_message(data)
        if event is None:
            return

        # Branch A — addon bridge response / UI chat (both arrive from the
        # simulated ``MCBEAI_TOOL`` player, not a real player command).
        if self._addon is not None and (
            self._addon.is_bridge_chat_message(event.sender, event.message)
            or self._addon.is_ui_chat_message(event.sender, event.message)
        ):
            try:
                result = await self._addon.handle_player_message(
                    state.id,
                    event.sender,
                    event.message,
                )
            except ProtocolError:
                self._log_malformed_addon_frame(event.message)
                return

            if result.bridge_chunk is not None:
                await self._manager.event_bus.emit(
                    WsEventType.BRIDGE_CHUNK,
                    state,
                    result.bridge_chunk,
                )
            if result.ui_chunk is not None:
                await self._manager.event_bus.emit(
                    WsEventType.UI_CHAT_CHUNK,
                    state,
                    result.ui_chunk,
                )
            if result.ui_message is not None:
                await self._manager.event_bus.emit(
                    WsEventType.UI_CHAT_REASSEMBLED,
                    state,
                    result.ui_message,
                )
            return

        # Branch B — inbound addon capability request (``scriptevent
        # mcbeai:bridge_request``). The hook is the host's override point; the
        # SDK provides no built-in capability execution (E.6).
        request = parse_addon_bridge_request(
            event.message,
            self._settings.addon.protocol.bridge_message_id,
        )
        if request is not None:
            await self._hook.on_bridge_message(state, request)
            return

        # Branch D — player command/chat. ``parse_typed_command`` is informational
        # (the host decides what to do via the hook).
        self._handler.parse_typed_command(event.message)
        await self._manager.event_bus.emit(WsEventType.PLAYER_MESSAGE, state, event)
        await self._hook.on_player_message(state, event)

    async def _on_ui_chat_reassembled(
        self,
        connection_id: UUID,
        player_name: str,
        message: str,
    ) -> None:
        """Callback the addon fires when a fragmented UI_CHAT message reassembles."""
        state = self._manager.get_connection(connection_id)
        if state is None:
            logger.warning(
                "ui_chat_reassembled_connection_missing",
                connection_id=str(connection_id),
                player=player_name,
            )
            return
        await self._hook.on_ui_chat_reassembled(state, player_name, message)

    def _wrap_send(
        self,
        websocket: ServerConnection,
        *,
        connection_id: UUID,
    ) -> SendPayload:
        """Build the transport frame-send callable, tagging outbound frames on the bus."""

        async def send_payload(payload: str) -> None:
            current_state = self._manager.get_connection(connection_id)
            if current_state is not None:
                await self._manager.event_bus.emit(
                    WsEventType.RAW_OUTBOUND,
                    current_state,
                    payload,
                )
            await websocket.send(payload)

        return send_payload

    @staticmethod
    def _log_malformed_addon_frame(message: str) -> None:
        parts = message.split("|", 4)
        message_type = "unknown"
        message_id = None
        if len(parts) >= 2:
            message_type = parts[1] or "unknown"
        if len(parts) >= 3 and parts[2]:
            message_id = parts[2]
        logger.warning(
            "malformed_addon_frame",
            addon_message_id=message_id,
            addon_message_type=message_type,
            utf8_byte_size=len(message.encode("utf-8")),
        )

    # -- commandResponse shape detection -----------------------------------------

    @staticmethod
    def _is_command_response(data: dict[str, Any]) -> bool:
        """True when ``data`` is a ``commandResponse`` frame (shape heuristic).

        Detects the envelope the main repo recognises: ``header.messagePurpose ==
        "commandResponse"``. Frames without a ``header`` (or a non-dict header) are
        not commandResponses.
        """
        header = data.get("header")
        if not isinstance(header, dict):
            return False
        return header.get("messagePurpose") == "commandResponse"

    @staticmethod
    def _extract_command_response(data: dict[str, Any]) -> MinecraftCommandResponse:
        """Return a typed ``commandResponse`` frame.

        The full envelope is preserved so the host can inspect canonical fields
        and any future header / top-level extension fields.
        """
        return MinecraftCommandResponse.model_validate(data)

    @staticmethod
    def _is_error_frame(data: dict[str, Any]) -> bool:
        header = data.get("header")
        if not isinstance(header, dict):
            return False
        return header.get("messagePurpose") == "error"

    @staticmethod
    def _extract_error_frame(data: dict[str, Any]) -> MinecraftErrorFrame:
        return MinecraftErrorFrame.model_validate(data)


__all__ = ["McbeServerFacade"]
