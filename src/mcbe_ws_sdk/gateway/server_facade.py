"""Gateway entry point: the facade the host drives the SDK stack through.

:class:`McbeServerFacade` OWNS the WebSocket transport and the connection /
protocol machinery (the :class:`~mcbe_ws_sdk.gateway.connection.ConnectionManager`
response-sender loops, the
:class:`~mcbe_ws_sdk.gateway.handler.MinecraftProtocolHandler` command parser,
and the per-connection
:class:`~mcbe_ws_sdk.addon.service.AddonBridgeService` sessions) but deliberately
does NOT own any host application concern: there is no ``MessageBroker``, no LLM
worker, no provider selection. All of that is injected by the
host via the :class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook` lifecycle hooks
and the :class:`~mcbe_ws_sdk.gateway.sink.ResponseSink` outbound routes — the
same dependency-inversion the gateway already applies internally (see
:class:`NoOpHook`, :class:`DefaultResponseSink`).

The facade accepts connections, sends the handshake and subscriptions, routes
messages, and releases connection resources. Welcome messages belong to the host.

Lifetime::

    facade = McbeServerFacade(hook=my_hook, sink=my_sink)
    await facade.run_lifetime()            # blocks until ``stop()`` / cancelled
    # ... or drive it on an explicit host asyncio.Task and cancel it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
import websockets

from mcbe_ws_sdk.addon import AddonBridgeService
from mcbe_ws_sdk.command import CommandRegistry
from mcbe_ws_sdk.config import GatewaySettings
from mcbe_ws_sdk.diagnostics import (
    DiagnosticObserver,
    Diagnostics,
    DiagnosticSettings,
    is_external_echo,
)
from mcbe_ws_sdk.errors import (
    FacadeLifecycleError,
    ProtocolError,
    RemoteCallError,
    SendClosedError,
)
from mcbe_ws_sdk.flow.flow_control import FlowControlMiddleware
from mcbe_ws_sdk.gateway.commands import NativeCommandClient
from mcbe_ws_sdk.gateway.connection import ConnectionManager, ConnectionState, SendPayload
from mcbe_ws_sdk.gateway.events import EventBus, WsEventType
from mcbe_ws_sdk.gateway.handler import MessageSurfaceConfig, MinecraftProtocolHandler
from mcbe_ws_sdk.gateway.hook import (
    ConnectionHook,
    LegacyUiChatHook,
    LegacyUiChatHookAdapter,
    NoOpHook,
)
from mcbe_ws_sdk.gateway.native_events import NativeEvent
from mcbe_ws_sdk.gateway.runtime import ConnectionRuntime
from mcbe_ws_sdk.gateway.sender import SendPriority
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink, ResponseSink
from mcbe_ws_sdk.profiles.mcbews_v1.classifier import ToolPlayerMessage
from mcbe_ws_sdk.profiles.mcbews_v1.models import UiChatMessage
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommand,
    MinecraftCommandResponse,
    MinecraftErrorFrame,
)
from mcbe_ws_sdk.requests import CATEGORY_COMMAND, RequestTable
from mcbe_ws_sdk.tracing import TraceContext, current_trace, observe, trace_scope

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

    from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge

logger = structlog.get_logger(__name__)


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
        legacy_ui_chat_hook: LegacyUiChatHook | None = None,
        sink: ResponseSink | None = None,
        addon: AddonBridgeService | None = None,
        registry: CommandRegistry | None = None,
        surface: MessageSurfaceConfig | None = None,
        diagnostics: DiagnosticSettings | None = None,
        diagnostic_observers: tuple[DiagnosticObserver, ...] = (),
        v2: V2Bridge | None = None,
    ) -> None:
        self._diagnostic_settings = diagnostics
        self._diagnostic_observers = diagnostic_observers
        self._v2 = v2
        self._settings = settings if settings is not None else GatewaySettings()
        if v2 is not None and self._settings.dispatch.mode != "reliable":
            raise ValueError("MCBEWS/2 requires reliable dispatch")
        self._hook = hook if hook is not None else NoOpHook()
        self._legacy_ui_chat_hook = (
            LegacyUiChatHookAdapter(legacy_ui_chat_hook)
            if legacy_ui_chat_hook is not None
            else None
        )
        self._sink = sink if sink is not None else DefaultResponseSink()
        self._registry = registry if registry is not None else CommandRegistry()

        self._handler = MinecraftProtocolHandler(self._registry, surface=surface)
        self._addon = addon if addon is not None else AddonBridgeService(self._settings.addon)
        self._addon.set_ui_chat_callback(self._on_ui_chat_reassembled)

        self._manager = ConnectionManager(
            sink=self._sink,
            event_bus=EventBus(),
            response_queue_maxsize=self._settings.websocket.response_queue_maxsize,
        )

        self._stopped = asyncio.Event()
        self._lifetime_started = False
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

    @property
    def reliable_dispatch(self) -> bool:
        """Whether the opt-in reliable dispatch path is enabled.

        The legacy path keeps the original serial callback contract; the
        reliable path runs host callbacks in SDK-owned business tasks while the
        reader keeps draining frames.
        """
        return self._settings.dispatch.mode == "reliable"

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
        if self._lifetime_started:
            raise FacadeLifecycleError("McbeServerFacade is single-use")
        self._lifetime_started = True
        serve_host = host if host is not None else self._settings.websocket.host
        serve_port = port if port is not None else self._settings.websocket.port
        transport = self._settings.websocket

        try:
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
                await self._stopped.wait()
        finally:
            try:
                await self._graceful_shutdown()
            finally:
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
        raw_send = self._wrap_send(websocket, connection_id=connection_id)
        state = await self._manager.create_connection(
            connection_id=connection_id,
            send_payload=raw_send,
        )
        self._install_tracking(state, raw_send)

        try:
            assert state.send_payload is not None
            await self._send_frame(
                state,
                '{"Result":"true"}',
                priority=SendPriority.CONTROL,
                source="handshake",
            )
            await self._send_frame(
                state,
                self._handler.create_subscribe_message(),
                priority=SendPriority.CONTROL,
                source="subscribe",
            )
            logger.info(
                "event_subscribed",
                connection_id=str(state.id),
                events=list(self._handler.SUBSCRIBED_EVENTS),
            )
            if self._v2 is not None:
                flow = FlowControlMiddleware(self._settings.flow)

                async def send_hello(hello: str) -> None:
                    assert self._v2 is not None
                    command = MinecraftCommand.create_scriptevent(
                        hello, self._v2.negotiation_event
                    ).body.commandLine
                    payload = flow.chunk_raw_command(command)[0]
                    await self._send_frame(
                        state, payload, priority=SendPriority.CONTROL, source="v2.negotiation"
                    )

                async def legacy_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
                    async def send_legacy(command: str) -> None:
                        payload = flow.chunk_raw_command(command)[0]
                        await self._send_frame(state, payload, source="v2.legacy_fallback")

                    return await self._addon.request_capability(
                        state.id, method, params, send_legacy
                    )

                self._v2.start(state, send_hello, legacy_request)
            # CONNECTED is emitted only after handshake + subscribe succeed.
            # On the reliable path the event and the host hook are dispatched
            # asynchronously; the reader is not blocked by either.
            await self._emit_event(state, WsEventType.CONNECTED, state)
            await self._invoke_hook(
                state,
                self._hook.on_connected,
                state,
                lane="connection",
                label="on_connected",
                log_event="hook_on_connected_failed",
            )

            async for raw in websocket:
                await self._handle_raw(state, raw)
        except websockets.ConnectionClosed:
            logger.info("connection_closed", connection_id=str(state.id))
        except Exception:
            logger.exception("connection_error", connection_id=str(state.id))
        finally:
            if self._v2 is not None:
                await self._v2.close_connection(state.id)
            if self._addon is not None:
                try:
                    self._addon.close_connection(state.id)
                except Exception:
                    logger.exception("addon_close_connection_failed", connection_id=str(state.id))
            try:
                await self._manager.drop_connection(state.id)
            except Exception:
                logger.exception("drop_connection_failed", connection_id=str(state.id))
            try:
                await self._hook.on_disconnected(state)
            except Exception:
                logger.exception("hook_on_disconnected_failed", connection_id=str(state.id))

    def _install_tracking(self, state: ConnectionState, raw_send: SendPayload) -> None:
        """Attach the SDK-owned runtime / request tracking for one connection."""
        state.diagnostics = Diagnostics(
            state.id, self._diagnostic_settings, self._diagnostic_observers
        )
        self._addon.set_diagnostics(state.id, state.diagnostics)
        if self.reliable_dispatch:
            runtime = ConnectionRuntime(
                connection_id=state.id,
                dispatch=self._settings.dispatch,
                send=self._settings.send,
                flow=self._settings.flow,
                transport=raw_send,
                diagnostics=state.diagnostics,
            )
            state.runtime = runtime
            state.requests = runtime.requests
            state.command_client = runtime.commands
            state.send_payload = runtime.send_from_host
            state.send_bulk = runtime.send_bulk_from_host
            return
        state.requests = RequestTable(
            max_pending=self._settings.dispatch.max_pending_requests,
            name=f"commands:{state.id}",
        )
        state.command_client = NativeCommandClient(
            connection_id=state.id,
            requests=state.requests,
            flow=self._settings.flow,
            send_payload=raw_send,
            timeout_seconds=self._settings.dispatch.command_timeout_seconds,
            diagnostics=state.diagnostics,
        )

    async def _send_frame(
        self,
        state: ConnectionState,
        payload: str,
        *,
        priority: SendPriority = SendPriority.REQUEST,
        source: str = "facade",
    ) -> None:
        """Send one frame through the reliable scheduler when it is active."""
        runtime = state.runtime
        if runtime is not None:
            await runtime.send_raw(payload, priority=priority, source=source)
            return
        if state.send_payload is None:
            raise RuntimeError("connection has no send_payload")
        await state.send_payload(payload)

    async def _emit_event(self, state: ConnectionState, event: WsEventType, *args: Any) -> None:
        """Emit a bus event inline (legacy) or on the observation lane (reliable)."""
        runtime = state.runtime
        bus = self._manager.event_bus
        if runtime is None:
            await bus.emit(event, *args)
            return

        async def run() -> None:
            await bus.emit(event, *args)

        accepted = runtime.events.submit(run, lane="events", label=event.value)
        if not accepted:
            logger.warning(
                "event_dispatch_overloaded",
                connection_id=str(state.id),
                event_type=event.value,
            )

    async def _invoke_hook(
        self,
        state: ConnectionState,
        fn: Callable[..., Awaitable[None]],
        *args: Any,
        lane: str,
        label: str,
        log_event: str,
        **kwargs: Any,
    ) -> None:
        """Await a host hook inline (legacy) or submit it to a business lane."""
        runtime = state.runtime
        if runtime is None:
            try:
                observe("processing_started", label=label)
                await fn(*args, **kwargs)
                observe("processing_completed", label=label)
            except Exception:
                observe("processing_failed", label=label)
                logger.exception(log_event, connection_id=str(state.id))
            return

        async def run() -> None:
            try:
                await fn(*args, **kwargs)
            except Exception:
                logger.exception(log_event, connection_id=str(state.id))

        # Rejections are counted/logged by the dispatcher; the reader moves on.
        runtime.business.submit(run, lane=lane, label=label)

    async def _handle_raw(self, state: ConnectionState, raw: str | bytes) -> None:
        """Capture inbound metadata and restore request correlation before routing."""
        try:
            packet = json.loads(raw)
        except (ValueError, TypeError):
            packet = {}
        if not isinstance(packet, dict):
            packet = {}
        header = packet.get("header", {})
        body = packet.get("body", {})
        header = header if isinstance(header, dict) else {}
        body = body if isinstance(body, dict) else {}
        request_id = str(header.get("requestId", "")) or None
        purpose = str(header.get("messagePurpose", "event"))
        context = TraceContext(connection_id=str(state.id))
        pending = (
            state.requests.get(CATEGORY_COMMAND, request_id)
            if (
                state.requests is not None
                and request_id
                and purpose in {"commandResponse", "error"}
            )
            else None
        )
        wire_context = (
            state.diagnostics.take_context(request_id)
            if state.diagnostics is not None
            and request_id
            and purpose in {"commandResponse", "error"}
            else None
        )
        if pending is not None:
            context = pending.trace
        elif wire_context is not None:
            context = wire_context
            request_id = context.request_id
        elif self._addon.is_bridge_chat_message(
            str(body.get("sender", "")), str(body.get("message", ""))
        ):
            parts = str(body["message"]).split("|", 4)
            logical_id = parts[2] if len(parts) > 2 else ""
            request_id = logical_id
            context = self._addon.request_trace(state.id, logical_id) or context
        player = body.get("sender") if header.get("eventName") == "PlayerMessage" else None
        echo = (
            purpose == "event"
            and header.get("eventName") == "PlayerMessage"
            and is_external_echo(str(body.get("sender", "")), body.get("type"))
        )
        with trace_scope(
            context=context,
            connection_id=str(state.id),
            request_id=request_id,
            player=player if pending is None and context.player is None else None,
            diagnostics=state.diagnostics,
        ):
            if state.diagnostics is not None:
                state.diagnostics.record(
                    "received",
                    packet=raw,
                    wire_request_id=str(header.get("requestId", "")),
                    frame_type=purpose,
                    classification="external_echo" if echo else "inbound",
                )
            await self._route_raw(state, raw)

    async def _route_raw(self, state: ConnectionState, raw: str | bytes) -> None:
        """Route one inbound WS frame to the right branch."""
        await self._emit_event(state, WsEventType.RAW_INBOUND, state, raw)

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
            try:
                error = self._extract_error_frame(data)
            except Exception:
                logger.exception(
                    "error_frame_validate_failed",
                    connection_id=str(state.id),
                )
                return
            self._fail_tracked_command_request(state, error)
            await self._emit_event(state, WsEventType.ERROR, state, error)
            await self._invoke_hook(
                state,
                self._hook.on_error,
                state,
                error,
                lane="control",
                label="on_error",
                log_event="hook_on_error_failed",
            )
            return

        # Branch C — commandResponse. The SDK resolves its own tracked native
        # command requests inline (so a reply is never blocked by a business
        # handler), then forwards the frame to the host hook as before.
        if self._is_command_response(data):
            try:
                response = self._extract_command_response(data)
            except Exception:
                logger.exception(
                    "command_response_validate_failed",
                    connection_id=str(state.id),
                )
                return
            self._resolve_tracked_command_response(state, response)
            await self._emit_event(state, WsEventType.COMMAND_RESPONSE, state, response)
            await self._invoke_hook(
                state,
                self._hook.on_command_response,
                state,
                response,
                lane="control",
                label="on_command_response",
                log_event="hook_on_command_response_failed",
            )
            return

        # Parse a PlayerMessage event (returns None for any other event kind).
        event = self._handler.parse_player_message(data)
        if event is None:
            await self._dispatch_native_event(state, data)
            return

        if self._v2 is not None and event.message.startswith(f"{self._v2.negotiation_prefix}|"):
            self._v2.receive_negotiation(state, event.sender, event.message)
            return

        if self._v2 is not None and event.message.startswith("MCBEWS2|"):
            try:
                await self._v2.receive(state, event.sender, event.message)
            except ValueError:
                logger.warning("invalid_v2_frame", connection_id=str(state.id))
            return

        # Branch A — addon bridge response / UI chat (both arrive from the
        # simulated addon bridge tool player, not a real player command).
        # Every reserved control prefix is consumed by the SDK.  This is
        # important even for a wrong sender: forwarding a ``MCBEWS|SESSION`` or
        # approval prefix to the Host would let the Host accidentally treat a
        # real player as the ToolPlayer.  The service performs the trusted
        # sender gate before decoding a typed control message.
        if self._addon is not None and self._addon.is_tool_player_channel_message(event.message):
            try:
                result = await self._addon.handle_player_message(
                    state.id,
                    event.sender,
                    event.message,
                )
            except ProtocolError:
                self._log_malformed_addon_frame(event.message)
                return
            except Exception:
                logger.exception(
                    "addon_frame_handler_failed",
                    connection_id=str(state.id),
                )
                return

            if result.bridge_chunk is not None:
                await self._emit_event(
                    state,
                    WsEventType.BRIDGE_CHUNK,
                    state,
                    result.bridge_chunk,
                )
            if result.ui_chunk is not None:
                await self._emit_event(
                    state,
                    WsEventType.UI_CHAT_CHUNK,
                    state,
                    result.ui_chunk,
                )
            if result.ui_message is not None:
                await self._emit_event(
                    state,
                    WsEventType.UI_CHAT_REASSEMBLED,
                    state,
                    result.ui_message,
                )
            if result.control_message is not None:
                await self._invoke_addon_control_hook(state, result.control_message)
            return

        # Diagnostic: RESP/UI_CHAT content with the wrong sender never matches the
        # bridge filter above, so the request future times out with no clue. Surface
        # the mismatch so hosts can see what Bedrock actually delivered.
        profile = self._settings.addon.profile
        root_token = profile.capability_response_chat_prefix.split("|", 1)[0]
        if event.message.startswith(f"{root_token}|"):
            logger.warning(
                "bridge_prefix_not_matched",
                connection_id=str(state.id),
                sender=event.sender,
                message_type=event.type,
                receiver=event.receiver,
                message_bytes=len(event.message.encode("utf-8")),
            )

        # Branch B — player command/chat. Pass the parsed command to the hook so
        # the host does not re-run the registry (D1). In reliable mode each player
        # gets their own lane: one player's slow handler cannot block another,
        # while consecutive messages from the same player stay ordered.
        if state.runtime is not None:
            await self._dispatch_native_event(state, data, player=event.sender)
            return
        parsed = self._handler.parse_typed_command(event.message)
        await self._emit_event(state, WsEventType.PLAYER_MESSAGE, state, event)
        await self._invoke_hook(
            state,
            self._hook.on_player_message,
            state,
            event,
            parsed=parsed,
            lane=f"player:{event.sender}",
            label="on_player_message",
            log_event="hook_on_player_message_failed",
        )

    async def _dispatch_native_event(
        self, state: ConnectionState, packet: dict[str, Any], *, player: str | None = None
    ) -> None:
        runtime = state.runtime
        header = packet.get("header")
        if (
            runtime is None
            or not isinstance(header, dict)
            or header.get("messagePurpose", "event") != "event"
            or not isinstance(header.get("eventName"), str)
            or not header["eventName"]
            or not isinstance(packet.get("body"), dict)
        ):
            return
        event = NativeEvent(header["eventName"], packet)

        async def notify_observers() -> None:
            await runtime.native_events.notify(event)

        runtime.events.submit(notify_observers, lane="events", label=event.name)

        async def deliver() -> None:
            observe("processing_started", event_name=event.name)
            transformed = await runtime.native_events.apply(event)
            if transformed is None:
                observe("processing_rejected", event_name=event.name)
                return
            if transformed.name == "PlayerMessage":
                message = self._handler.parse_player_message(transformed.packet)
                if message is None:
                    return
                # Transforms cannot manufacture reserved bridge/control input.
                if (
                    self._addon.is_tool_player_channel_message(message.message)
                    or (self._v2 is not None and message.message.startswith("MCBEWS2|"))
                    or (
                        self._v2 is not None
                        and message.message.startswith(f"{self._v2.negotiation_prefix}|")
                    )
                ):
                    logger.warning("native_transform_reserved_channel", connection_id=str(state.id))
                    return
                await self._emit_event(state, WsEventType.PLAYER_MESSAGE, state, message)
                await self._hook.on_player_message(
                    state, message, parsed=self._handler.parse_typed_command(message.message)
                )
            else:
                callback = getattr(self._hook, "on_native_event", None)
                if callback is not None:
                    await callback(state, transformed)
            observe("processing_completed", event_name=event.name)

        runtime.business.submit(
            deliver,
            lane=f"player:{player}" if player is not None else "native_events",
            label=event.name,
        )

    def _resolve_tracked_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
        """Resolve an SDK-tracked native command request, if any."""
        table = state.requests
        if table is None:
            return
        table.resolve(CATEGORY_COMMAND, response.request_id, response)

    def _fail_tracked_command_request(
        self,
        state: ConnectionState,
        error: MinecraftErrorFrame,
    ) -> None:
        """Fail an SDK-tracked native command request on a matching error frame."""
        table = state.requests
        if table is None:
            return
        body = error.body if isinstance(error.body, dict) else {}
        status_message = body.get("statusMessage")
        message = str(status_message) if status_message else "remote error"
        table.fail(
            CATEGORY_COMMAND,
            error.request_id,
            RemoteCallError(
                error.request_id,
                code=error.header.get("code"),
                message=message,
            ),
        )

    async def _on_ui_chat_reassembled(
        self,
        connection_id: UUID,
        message: UiChatMessage,
    ) -> None:
        """Callback the addon fires when a fragmented UI_CHAT message reassembles."""
        state = self._manager.get_connection(connection_id)
        if state is None:
            logger.warning(
                "ui_chat_reassembled_connection_missing",
                connection_id=str(connection_id),
                player=message.player_name,
            )
            return
        await self._invoke_ui_chat_hook(state, message)

    async def _invoke_ui_chat_hook(self, state: ConnectionState, message: UiChatMessage) -> None:
        """Invoke the typed UI callback, or an explicitly supplied legacy adapter."""
        lane = f"player:{message.player_name}"
        if self._legacy_ui_chat_hook is not None:
            await self._invoke_hook(
                state,
                self._legacy_ui_chat_hook.invoke,
                state,
                message,
                lane=lane,
                label="on_ui_chat_reassembled",
                log_event="hook_on_ui_chat_reassembled_failed",
            )
            return
        await self._invoke_hook(
            state,
            self._hook.on_ui_chat_reassembled,
            state,
            message,
            lane=lane,
            label="on_ui_chat_reassembled",
            log_event="hook_on_ui_chat_reassembled_failed",
        )

    async def _invoke_addon_control_hook(
        self,
        state: ConnectionState,
        message: ToolPlayerMessage,
    ) -> None:
        """Deliver authenticated session/approval DTOs without body guessing."""

        callback = getattr(self._hook, "on_addon_control_message", None)
        if callback is None:
            # A structurally compatible pre-0.2.0 hook does not implement the
            # optional control callback. It is safer to drop than to pass a
            # reserved ToolPlayer frame through ``on_player_message``.
            logger.warning(
                "addon_control_hook_missing",
                connection_id=str(state.id),
                channel=message.channel,
            )
            return
        player = self._control_player_name(message)
        await self._invoke_hook(
            state,
            callback,
            state,
            message,
            lane=f"player:{player}" if player else "control",
            label="on_addon_control_message",
            log_event="hook_on_addon_control_message_failed",
        )

    @staticmethod
    def _control_player_name(message: ToolPlayerMessage) -> str | None:
        """Return the explicit player identity carried by a control DTO, if any."""
        if message.session_request is not None:
            return message.session_request.player_name
        if message.approval_decision is not None:
            return message.approval_decision.player_name
        return None

    def _wrap_send(
        self,
        websocket: ServerConnection,
        *,
        connection_id: UUID,
    ) -> SendPayload:
        """Build the transport frame-send callable, tagging outbound frames on the bus."""

        async def send_payload(payload: str) -> None:
            current_state = self._manager.get_connection(connection_id)
            if current_state is None:
                # The manager removes the active connection before the runtime
                # marks itself closed. In reliable mode a silent return would be
                # counted as a successful transport call by the scheduler, so
                # surface the drop instead. The legacy path keeps its original
                # best-effort behaviour.
                if self.reliable_dispatch:
                    raise SendClosedError("connection dropped")
                logger.debug(
                    "send_payload_after_drop",
                    connection_id=str(connection_id),
                )
                return
            await self._emit_event(
                current_state,
                WsEventType.RAW_OUTBOUND,
                current_state,
                payload,
            )
            try:
                packet = json.loads(payload)
                header = packet.get("header", {}) if isinstance(packet, dict) else {}
            except (ValueError, TypeError):
                header = {}
            header = header if isinstance(header, dict) else {}
            with trace_scope(
                connection_id=str(connection_id),
                request_id=current_trace().request_id or str(header.get("requestId", "")) or None,
                diagnostics=current_state.diagnostics,
            ):
                diagnostics = current_state.diagnostics
                fields = {
                    "wire_request_id": str(header.get("requestId", "")),
                    "frame_type": str(header.get("messagePurpose", "")),
                }
                if diagnostics is not None:
                    diagnostics.remember(fields["wire_request_id"])
                    diagnostics.record("prepared", packet=payload, **fields)
                try:
                    await websocket.send(payload)
                except BaseException as exc:
                    if diagnostics is not None:
                        diagnostics.take_context(fields["wire_request_id"])
                        diagnostics.record(
                            "transport_failed", error_type=type(exc).__name__, **fields
                        )
                    raise
                if diagnostics is not None:
                    diagnostics.record("transport_sent", **fields)

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
