"""WebSocket server entry point.

:class:`McbeServer` owns the WebSocket transport and the connection / protocol
machinery, but not any host application concern: there is no message broker, no
LLM worker and no provider selection. All of that is injected through
:class:`~mcbe_ws_sdk.gateway.hook.ConnectionHook` and
:class:`~mcbe_ws_sdk.gateway.sink.ResponseSink`.

Connections negotiate the protocol before business processing::

    WS_TRANSPORT_CONNECTED -> NEGOTIATING -> READY_V2 -> CLOSING -> CLOSED
                                  \\-> REJECTED ------------------> CLOSED

The reader starts as soon as the handshake and subscribe frames are sent, then
negotiation runs concurrently. Frames that need a business handler are dropped
until ``READY_V2``; they are never buffered for replay. A failed negotiation
sends a best-effort reject notice and closes the transport with code 1008.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
import websockets

from mcbe_ws_sdk.command import CommandRegistry
from mcbe_ws_sdk.config import GatewaySettings
from mcbe_ws_sdk.diagnostics import (
    DiagnosticObserver,
    Diagnostics,
    DiagnosticSettings,
    is_external_echo,
)
from mcbe_ws_sdk.errors import (
    ProtocolError,
    RemoteCallError,
    RequestClosedError,
    SendClosedError,
    ServerLifecycleError,
)
from mcbe_ws_sdk.flow.flow_control import FlowControlMiddleware
from mcbe_ws_sdk.gateway.connection import ConnectionManager, ConnectionState, SendPayload
from mcbe_ws_sdk.gateway.events import EventBus, WsEventType
from mcbe_ws_sdk.gateway.handler import MessageSurfaceConfig, MinecraftProtocolHandler
from mcbe_ws_sdk.gateway.hook import ConnectionHook, NoOpHook
from mcbe_ws_sdk.gateway.native_events import NativeEvent
from mcbe_ws_sdk.gateway.runtime import ConnectionRuntime
from mcbe_ws_sdk.gateway.sender import SendPriority
from mcbe_ws_sdk.gateway.sink import DefaultResponseSink, ResponseSink
from mcbe_ws_sdk.profiles.mcbews_v2.codec import MANIFEST
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import NegotiatedConnection, NegotiationError, V2Bridge
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommand,
    MinecraftCommandResponse,
    MinecraftErrorFrame,
)
from mcbe_ws_sdk.requests import CATEGORY_COMMAND
from mcbe_ws_sdk.tracing import TraceContext, current_trace, observe, trace_scope

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

logger = structlog.get_logger(__name__)

_ADMISSION = MANIFEST["admission"]
_CLOSE_CODE = int(_ADMISSION["close_code"])
_REASON_MAX_BYTES = int(_ADMISSION["reason_max_bytes"])
_REJECT_NOTICE = str(_ADMISSION["reject_notice"])
_TRUSTED_BRIDGE_PLAYER = str(MANIFEST["carrier"]["trusted_player"])
_V2_FRAME_PREFIX = f"{MANIFEST['carrier']['prefix']}|"
_OLD_BUSINESS_PREFIXES = ("MCBEWS|", "mcbews:")


class McbeServer:
    """Serve MCBEWS/2 connections with reliable business dispatch.

    The constructor takes only keyword arguments and never builds a broker.
    Each ``None`` collapses to a gateway default so a host can stand up a
    working server with ``McbeServer()`` and override collaborators one at a
    time.

    Args:
        settings: Frozen gateway settings.
        hook: Host lifecycle hook; defaults to :class:`NoOpHook`.
        sink: Outbound response sink; defaults to :class:`DefaultResponseSink`.
        registry: Command registry used to parse player messages.
        surface: Presentation strings for status rendering.
        diagnostics: Diagnostic capture settings.
        diagnostic_observers: Best-effort observers for diagnostic records.
        v2: MCBEWS/2 bridge; a fresh :class:`V2Bridge` is created when omitted.
    """

    WS_TRANSPORT_CONNECTED = "WS_TRANSPORT_CONNECTED"
    NEGOTIATING = "NEGOTIATING"
    READY_V2 = "READY_V2"
    REJECTED = "REJECTED"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"

    def __init__(
        self,
        *,
        settings: GatewaySettings | None = None,
        hook: ConnectionHook | None = None,
        sink: ResponseSink | None = None,
        registry: CommandRegistry | None = None,
        surface: MessageSurfaceConfig | None = None,
        diagnostics: DiagnosticSettings | None = None,
        diagnostic_observers: tuple[DiagnosticObserver, ...] = (),
        v2: V2Bridge | None = None,
    ) -> None:
        self._diagnostic_settings = diagnostics
        self._diagnostic_observers = diagnostic_observers
        self._settings = settings if settings is not None else GatewaySettings()
        self._hook = hook if hook is not None else NoOpHook()
        self._sink = sink if sink is not None else DefaultResponseSink()
        self._registry = registry if registry is not None else CommandRegistry()
        self._handler = MinecraftProtocolHandler(self._registry, surface=surface)

        self._v2 = v2 if v2 is not None else V2Bridge()
        self._v2.set_session_invalid_handler(self._on_session_invalid)

        self._manager = ConnectionManager(
            sink=self._sink,
            event_bus=EventBus(),
            response_queue_maxsize=self._settings.websocket.response_queue_maxsize,
        )

        self._transports: dict[UUID, ServerConnection] = {}
        self._stopped = asyncio.Event()
        self._lifetime_started = False
        self._server: Any = None

    # -- public manipulables (read-friendly; tests/asserts use these) ------------

    @property
    def manager(self) -> ConnectionManager:
        """The server's owned connection manager."""
        return self._manager

    @property
    def handler(self) -> MinecraftProtocolHandler:
        """The server's owned protocol handler (command parser + renderer)."""
        return self._handler

    @property
    def v2(self) -> V2Bridge:
        """Negotiated addon requests, streams and host capability registrations."""
        return self._v2

    @property
    def settings(self) -> GatewaySettings:
        return self._settings

    # -- lifetime ---------------------------------------------------------------

    async def run_lifetime(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        """Bind a WebSocket server and serve until :meth:`stop` / cancellation.

        ``host``/``port`` default to ``settings.websocket.host`` / ``port`` when
        ``None``. Blocks on an internal event so an explicit ``stop()`` returns
        cleanly; the outer task being cancelled also unwinds into
        :meth:`_graceful_shutdown`.
        """
        if self._lifetime_started:
            raise ServerLifecycleError("McbeServer is single-use")
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
                    "server_listening",
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
        """Drop every connection (cancels each response-sender)."""
        logger.info("server_graceful_shutdown")
        await self._manager.shutdown_all()

    # -- per-connection protocol driver -----------------------------------------

    async def _on_connection(self, websocket: ServerConnection) -> None:
        """Per-connection driver: handshake -> concurrent admission -> teardown."""
        connection_id = uuid4()
        raw_send = self._wrap_send(websocket, connection_id=connection_id)
        state = await self._manager.create_connection(
            connection_id=connection_id,
            send_payload=raw_send,
        )
        self._install_tracking(state, raw_send)
        self._transports[state.id] = websocket
        admission: asyncio.Task[None] | None = None

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
            state.admission_state = self.NEGOTIATING
            logger.info(
                "admission_started",
                connection_id=str(state.id),
                protocol=MANIFEST["protocol"],
                stage="negotiating",
            )
            admission = asyncio.create_task(
                self._admit(state, websocket),
                name=f"admission:{state.id}",
            )
            async for raw in websocket:
                await self._handle_raw(state, raw)
        except websockets.ConnectionClosed as exc:
            logger.info(
                "connection_closed",
                connection_id=str(state.id),
                code=getattr(exc, "code", None),
                reason=getattr(exc, "reason", None),
                initiator="peer",
            )
        except Exception:
            logger.exception("connection_error", connection_id=str(state.id))
        finally:
            await self._teardown(state, websocket, admission)

    async def _teardown(
        self,
        state: ConnectionState,
        websocket: ServerConnection,
        admission: asyncio.Task[None] | None,
    ) -> None:
        if state.pre_ready_dropped:
            logger.info(
                "pre_ready_frames_dropped",
                connection_id=str(state.id),
                dropped=state.pre_ready_dropped,
                admission_state=state.admission_state,
            )
        if admission is not None:
            if not admission.done():
                if state.admission_state in (self.REJECTED, self.CLOSING):
                    # Let the reject notice and close finish before teardown
                    # releases the transport.
                    with suppress(Exception):
                        async with asyncio.timeout(1.5):
                            await asyncio.shield(admission)
                else:
                    admission.cancel()
            with suppress(asyncio.CancelledError):
                await admission
        try:
            await self._v2.close_connection(state.id)
        except Exception:
            logger.exception("v2_close_connection_failed", connection_id=str(state.id))
        self._transports.pop(state.id, None)
        try:
            await self._manager.drop_connection(state.id)
        except Exception:
            logger.exception("drop_connection_failed", connection_id=str(state.id))
        if state.ready:
            try:
                await self._hook.on_disconnected(state)
            except Exception:
                logger.exception("hook_on_disconnected_failed", connection_id=str(state.id))
        logger.info(
            "transport_closed",
            connection_id=str(state.id),
            admission_state=state.admission_state,
            code=getattr(websocket, "close_code", None),
            reason=getattr(websocket, "close_reason", None),
            initiator="unknown",
        )
        state.admission_state = self.CLOSED

    def _install_tracking(self, state: ConnectionState, raw_send: SendPayload) -> None:
        """Attach the SDK-owned runtime and request tracking for one connection."""
        state.diagnostics = Diagnostics(
            state.id, self._diagnostic_settings, self._diagnostic_observers
        )
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

    # -- admission --------------------------------------------------------------

    def _hello_sender(self, state: ConnectionState) -> Callable[[str], Awaitable[None]]:
        flow = FlowControlMiddleware(self._settings.flow)

        async def send_hello(hello: str) -> None:
            command = MinecraftCommand.create_scriptevent(
                hello, self._v2.negotiation_event
            ).body.commandLine
            payload = flow.chunk_raw_command(command)[0]
            await self._send_frame(
                state,
                payload,
                priority=SendPriority.CONTROL,
                source="v2.negotiation",
            )

        return send_hello

    async def _admit(self, state: ConnectionState, websocket: ServerConnection) -> None:
        """Run one bounded V2 negotiation and publish readiness exactly once."""
        try:
            self._v2.start(state, self._hello_sender(state))
            selection: NegotiatedConnection = await self._v2.ready(state.id)
        except asyncio.CancelledError:
            raise
        except RequestClosedError:
            # The transport closed before admission settled; teardown owns it.
            return
        except NegotiationError as exc:
            await self._reject(state, websocket, exc.reason)
            return
        except Exception:
            logger.exception(
                "admission_failed",
                connection_id=str(state.id),
                protocol=MANIFEST["protocol"],
                stage="negotiating",
                reason="INVALID_NEGOTIATION",
            )
            await self._reject(state, websocket, "INVALID_NEGOTIATION")
            return

        if selection.endpoint is None:
            await self._reject(state, websocket, "UNSUPPORTED_PROTOCOL")
            return
        if state.admission_state != self.NEGOTIATING:
            return

        state.ready = True
        state.admission_state = self.READY_V2
        logger.info(
            "admission_ready",
            connection_id=str(state.id),
            protocol=selection.version,
            stage="ready",
            epoch=selection.endpoint.epoch,
            features=sorted(selection.features),
        )
        # CONNECTED and the host callback are paired, and both run on the
        # business lane so the reader keeps draining inbound frames.
        await self._emit_event(state, WsEventType.CONNECTED, state)
        await self._invoke_hook(
            state,
            self._hook.on_connected,
            state,
            lane="connection",
            label="on_connected",
            log_event="hook_on_connected_failed",
        )

    async def _reject(
        self,
        state: ConnectionState,
        websocket: ServerConnection,
        reason: str,
    ) -> None:
        """Classify a terminal admission failure, notice, then close."""
        if state.admission_state in (self.REJECTED, self.CLOSING, self.CLOSED):
            return
        state.admission_state = self.REJECTED
        logger.warning(
            "admission_rejected",
            connection_id=str(state.id),
            protocol=MANIFEST["protocol"],
            stage="negotiating",
            reason=reason,
            pre_ready_dropped=state.pre_ready_dropped,
        )
        await self._send_reject_notice(state, reason)
        state.admission_state = self.CLOSING
        try:
            async with asyncio.timeout(1.0):
                await websocket.close(code=_CLOSE_CODE, reason=_close_reason(reason))
        except Exception:
            logger.debug(
                "admission_close_failed",
                connection_id=str(state.id),
                reason=reason,
            )

    async def _send_reject_notice(self, state: ConnectionState, reason: str) -> None:
        """Best-effort in-game notice; a failure never blocks the close."""
        if state.send_payload is None:
            return
        try:
            flow = FlowControlMiddleware(self._settings.flow)
            command = MinecraftCommand.create_scriptevent(reason, _REJECT_NOTICE).body.commandLine
            payload = flow.chunk_raw_command(command)[0]
            async with asyncio.timeout(0.5):
                await self._send_frame(
                    state,
                    payload,
                    priority=SendPriority.CONTROL,
                    source="admission.reject",
                )
        except Exception:
            logger.debug(
                "admission_reject_notice_failed",
                connection_id=str(state.id),
                reason=reason,
            )

    async def _on_session_invalid(self, connection_id: UUID) -> None:
        """Revoke admission after a lease failure and close the transport."""
        state = self._manager.get_connection(connection_id)
        websocket = self._transports.get(connection_id)
        if state is None or websocket is None or state.admission_state != self.READY_V2:
            return
        logger.warning(
            "session_invalid",
            connection_id=str(connection_id),
            protocol=MANIFEST["protocol"],
            stage="ready",
            reason="SESSION_INVALID",
        )
        await self._reject(state, websocket, "SESSION_INVALID")

    # -- outbound helpers -------------------------------------------------------

    async def _send_frame(
        self,
        state: ConnectionState,
        payload: str,
        *,
        priority: SendPriority = SendPriority.REQUEST,
        source: str = "server",
    ) -> None:
        runtime = state.runtime
        if runtime is not None:
            await runtime.send_raw(payload, priority=priority, source=source)
            return
        if state.send_payload is None:
            raise RuntimeError("connection has no send_payload")
        await state.send_payload(payload)

    async def _emit_event(self, state: ConnectionState, event: WsEventType, *args: Any) -> None:
        """Emit a bus event on the observation lane."""
        bus = self._manager.event_bus

        async def run() -> None:
            await bus.emit(event, *args)

        runtime = state.runtime
        if runtime is None:
            await run()
            return
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
        """Submit a host hook to its business lane without blocking the reader."""
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

        runtime.business.submit(run, lane=lane, label=label)

    # -- inbound routing --------------------------------------------------------

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
        """Route one inbound WS frame; business paths require ``state.ready``."""
        if state.ready:
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
            logger.warning(
                "non_object_frame_dropped",
                connection_id=str(state.id),
                frame_type=type(data).__name__,
            )
            return

        # Native command correlation is required while negotiating because the
        # SDK's own hello/confirm frames are commandRequests.
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
            if not state.ready:
                return
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
            if not state.ready:
                return
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

        event = self._handler.parse_player_message(data)
        if event is None:
            if state.ready:
                await self._dispatch_native_event(state, data)
            else:
                state.pre_ready_dropped += 1
            return

        message = event.message
        if message.startswith(f"{self._v2.negotiation_prefix}|"):
            self._v2.receive_negotiation(state, event.sender, message)
            return

        if message.startswith(_V2_FRAME_PREFIX):
            if state.ready and event.sender == _TRUSTED_BRIDGE_PLAYER:
                try:
                    await self._v2.receive(state, event.sender, message)
                except (ValueError, ProtocolError):
                    logger.warning(
                        "invalid_v2_frame",
                        connection_id=str(state.id),
                        sender=event.sender,
                    )
            else:
                state.pre_ready_dropped += 1
            return

        if message.startswith(_OLD_BUSINESS_PREFIXES):
            logger.warning(
                "old_protocol_frame_dropped",
                connection_id=str(state.id),
                sender=event.sender,
                ready=state.ready,
                message_bytes=len(message.encode("utf-8")),
            )
            if not state.ready:
                state.pre_ready_dropped += 1
            return

        if not state.ready:
            state.pre_ready_dropped += 1
            return

        await self._dispatch_native_event(state, data, player=event.sender)

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
                # Transforms cannot manufacture reserved protocol input.
                if message.message.startswith(
                    (
                        f"{self._v2.negotiation_prefix}|",
                        _V2_FRAME_PREFIX,
                        *_OLD_BUSINESS_PREFIXES,
                    )
                ):
                    logger.warning(
                        "native_transform_reserved_channel",
                        connection_id=str(state.id),
                    )
                    return
                await self._emit_event(state, WsEventType.PLAYER_MESSAGE, state, message)
                await self._hook.on_player_message(
                    state,
                    message,
                    parsed=self._handler.parse_typed_command(message.message),
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

    # -- transport send wrapper -------------------------------------------------

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
                # A silent return would be counted as a successful transport
                # call by the scheduler while the connection is gone.
                raise SendClosedError("connection dropped")
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

    # -- commandResponse shape detection ----------------------------------------

    @staticmethod
    def _is_command_response(data: dict[str, Any]) -> bool:
        header = data.get("header")
        if not isinstance(header, dict):
            return False
        return header.get("messagePurpose") == "commandResponse"

    @staticmethod
    def _extract_command_response(data: dict[str, Any]) -> MinecraftCommandResponse:
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


def _close_reason(reason: str) -> str:
    """Return a close reason token that fits the manifest's UTF-8 bound."""
    encoded = reason.encode("utf-8")
    if len(encoded) <= _REASON_MAX_BYTES:
        return reason
    return encoded[:_REASON_MAX_BYTES].decode("utf-8", errors="ignore")


__all__ = ["McbeServer"]
