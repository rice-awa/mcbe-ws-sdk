"""Connection-bound MCBEWS/2 request dispatch for the reliable gateway."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from mcbe_ws_sdk.capability import CapabilityContext, CapabilityRegistry
from mcbe_ws_sdk.errors import RemoteCallError, RequestClosedError, RequestTimeoutError
from mcbe_ws_sdk.gateway.sender import SendPriority
from mcbe_ws_sdk.profiles.mcbews_v2.codec import MANIFEST, V2ProtocolError
from mcbe_ws_sdk.profiles.mcbews_v2.streams import IncomingStream, StreamManager, StreamRegistry
from mcbe_ws_sdk.profiles.mcbews_v2.transport import ReassemblyPool, limits, send_downstream
from mcbe_ws_sdk.tracing import current_trace, trace_scope

if TYPE_CHECKING:
    from mcbe_ws_sdk.gateway.connection import ConnectionState
    from mcbe_ws_sdk.requests import PendingRequest, RequestTable

_CATEGORY = "addon.v2"
_NEGOTIATION_CATEGORY = "addon.v2.negotiation"
_VERSION = "MCBEWS/2"
_NEGOTIATION = MANIFEST["negotiation"]
_ADMISSION = MANIFEST["admission"]
_TRUSTED_PLAYER = str(MANIFEST["carrier"]["trusted_player"])


class NegotiationError(RuntimeError):
    """A terminal admission failure carrying a stable manifest reason code."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        if reason not in _ADMISSION["reasons"]:
            raise ValueError(f"unknown admission reason: {reason}")
        self.reason = reason
        super().__init__(message or reason)


@dataclass(frozen=True, slots=True)
class NegotiatedConnection:
    """Selected wire version, negotiated features and the v2 endpoint."""

    version: str
    features: frozenset[str]
    endpoint: V2Endpoint | None = None


@dataclass(slots=True)
class _Discovery:
    attempt: str
    challenge: asyncio.Future[tuple[str, frozenset[str]]]
    requests: RequestTable
    pending: PendingRequest
    epoch: str | None = None
    features: frozenset[str] = frozenset()
    confirming: bool = False


class V2Bridge:
    """Own a shared reassembly pool and registered capabilities for a facade."""

    negotiation_event = _NEGOTIATION["event"]
    negotiation_prefix = _NEGOTIATION["response_prefix"]

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        negotiation_timeout_seconds: float = 1.0,
        stream_limits: dict[str, int] | None = None,
    ) -> None:
        if (
            not math.isfinite(timeout_seconds)
            or not math.isfinite(negotiation_timeout_seconds)
            or timeout_seconds <= 0
            or negotiation_timeout_seconds <= 0
        ):
            raise ValueError("timeouts must be positive")
        self.registry = CapabilityRegistry()
        self.streams = StreamRegistry()
        self.stream_limits = stream_limits
        self.pool = ReassemblyPool()
        self.timeout_seconds = timeout_seconds
        self.negotiation_timeout_seconds = negotiation_timeout_seconds
        self._connections: dict[UUID, V2Endpoint] = {}
        self._pruner: asyncio.Task[None] | None = None
        self._negotiations: dict[UUID, asyncio.Task[NegotiatedConnection]] = {}
        self._discoveries: dict[UUID, _Discovery] = {}
        self._controls: dict[UUID, Callable[[str], Awaitable[None]]] = {}
        self._leases: dict[UUID, asyncio.Task[None]] = {}
        self._lease_acks: dict[UUID, asyncio.Future[None]] = {}
        self._session_invalid: Callable[[UUID], Awaitable[None]] | None = None

    def set_session_invalid_handler(
        self, handler: Callable[[UUID], Awaitable[None]] | None
    ) -> None:
        """Register the server hook invoked when a live lease is lost."""
        self._session_invalid = handler

    def start(
        self,
        state: ConnectionState,
        send_hello: Callable[[str], Awaitable[None]],
    ) -> None:
        """Start one bounded discovery attempt without blocking the reader."""
        if state.runtime is None or state.requests is None:
            raise ValueError("MCBEWS/2 requires a connection runtime")
        if state.id in self._negotiations:
            raise ValueError("negotiation already started")
        attempt = uuid4().hex[:24]
        pending = state.requests.reserve(_NEGOTIATION_CATEGORY, request_id=attempt)
        discovery = _Discovery(
            attempt, asyncio.get_running_loop().create_future(), state.requests, pending
        )
        self._discoveries[state.id] = discovery
        self._controls[state.id] = send_hello

        async def discover() -> NegotiatedConnection:
            assert state.requests is not None
            succeeded = False
            try:
                try:
                    async with asyncio.timeout(self.negotiation_timeout_seconds):
                        await send_hello(f"{_NEGOTIATION['hello_prefix']}|{attempt}")
                        epoch, _features = await discovery.challenge
                        discovery.confirming = True
                        await send_hello(f"MCBEWS2C|{attempt}|{epoch}")
                        selection: NegotiatedConnection = await pending.future
                        succeeded = True
                        return selection
                except TimeoutError as exc:
                    if discovery.epoch is not None:
                        raise NegotiationError(
                            "NEGOTIATION_CONFIRMATION_TIMEOUT",
                            "negotiation confirmation timed out",
                        ) from exc
                    raise NegotiationError(
                        "NEGOTIATION_TIMEOUT", "negotiation timed out"
                    ) from exc
            except asyncio.CancelledError as exc:
                raise RequestClosedError(attempt) from exc
            finally:
                if self._discoveries.get(state.id) is discovery:
                    self._discoveries.pop(state.id, None)
                state.requests.cancel(pending)
                if not discovery.challenge.done():
                    discovery.challenge.cancel()
                elif not discovery.challenge.cancelled():
                    discovery.challenge.exception()
                endpoint = self._connections.get(state.id)
                if not succeeded and endpoint is not None and endpoint.epoch == discovery.epoch:
                    self._retire_endpoint(state.id, endpoint)

        self._negotiations[state.id] = asyncio.create_task(
            discover(), name=f"v2-negotiation:{state.id}"
        )

    def receive_negotiation(self, state: ConnectionState, sender: str, frame: str) -> None:
        """Match discovery and install its endpoint before the reader continues."""
        if sender != _TRUSTED_PLAYER or state.requests is None:
            return
        try:
            if len(frame) > 256 or len(frame.encode("utf-8")) > 1024:
                return
        except UnicodeError:
            return
        parts = frame.split("|")
        if not parts or parts[0] != _NEGOTIATION["response_prefix"]:
            return
        if len(parts) == 3 and parts[2] == "lease":
            endpoint = self._connections.get(state.id)
            ack = self._lease_acks.get(state.id)
            if endpoint is not None and parts[1] == endpoint.epoch and ack and not ack.done():
                ack.set_result(None)
            return
        discovery = self._discoveries.get(state.id)
        if discovery is None or len(parts) < 4 or parts[1] != discovery.attempt:
            return
        if state.requests.get(_NEGOTIATION_CATEGORY, discovery.attempt) is None:
            return

        requests = state.requests

        def fail(reason: str, message: str | None = None) -> None:
            exc = NegotiationError(reason, message)
            if not discovery.challenge.done():
                discovery.challenge.set_exception(exc)
            else:
                requests.fail(_NEGOTIATION_CATEGORY, discovery.attempt, exc)

        if len(parts) == 4 and parts[2:] == ["-", "busy"]:
            fail("NEGOTIATION_BUSY", "v2 peer admission busy")
            return
        if len(parts) == 5:
            _, _, epoch, version, tokens = parts
            if discovery.challenge.done():
                return
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", epoch):
                fail("INVALID_NEGOTIATION", "invalid negotiated epoch")
                return
            if version != "2":
                fail("UNSUPPORTED_PROTOCOL", "unsupported protocol version")
                return
            features = tokens.split(",") if tokens else []
            if (
                len(features) > 16
                or len(set(features)) != len(features)
                or any(re.fullmatch(MANIFEST["token_pattern"], item) is None for item in features)
            ):
                fail("INVALID_NEGOTIATION", "invalid negotiated features")
                return
            discovery.epoch = epoch
            discovery.features = frozenset(features) & frozenset(_NEGOTIATION["features"])
            discovery.challenge.set_result((epoch, discovery.features))
            return
        if (
            len(parts) == 4
            and parts[3] == "ready"
            and parts[2] == discovery.epoch
            and discovery.confirming
        ):
            if state.id in self._connections:
                return
            endpoint = self._activate(state, epoch=parts[2], features=discovery.features)
            selection = NegotiatedConnection(_VERSION, discovery.features, endpoint)
            state.requests.resolve(_NEGOTIATION_CATEGORY, discovery.attempt, selection)
            self._leases[state.id] = asyncio.create_task(
                self._renew(state.id, endpoint), name=f"v2-lease:{state.id}"
            )
        elif len(parts) == 4 and parts[3] not in ("ready", "busy"):
            fail("UNSUPPORTED_PROTOCOL", "unsupported protocol version")

    async def _renew(self, connection_id: UUID, endpoint: V2Endpoint) -> None:
        """Renew idle sessions without replaying business requests."""
        send = self._controls[connection_id]
        try:
            while True:
                await asyncio.sleep(_NEGOTIATION["refresh_ms"] / 1000)
                ack: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                self._lease_acks[connection_id] = ack
                async with asyncio.timeout(_NEGOTIATION["refresh_ms"] / 1000):
                    await send(f"MCBEWS2R|{endpoint.epoch}")
                    await ack
                self._lease_acks.pop(connection_id, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._connections.get(connection_id) is endpoint:
                self._retire_endpoint(connection_id, endpoint)
                self._notify_session_invalid(connection_id)
        finally:
            self._lease_acks.pop(connection_id, None)

    def _notify_session_invalid(self, connection_id: UUID) -> None:
        handler = self._session_invalid
        if handler is None:
            return

        async def notify() -> None:
            await handler(connection_id)

        task = asyncio.create_task(
            notify(), name=f"v2-session-invalid:{connection_id}"
        )
        task.add_done_callback(
            lambda completed: completed.exception() if not completed.cancelled() else None
        )

    def _retire_endpoint(self, connection_id: UUID, endpoint: V2Endpoint) -> None:
        if self._connections.get(connection_id) is not endpoint:
            return
        self._connections.pop(connection_id, None)
        lease = self._leases.pop(connection_id, None)
        if lease is not None and lease is not asyncio.current_task():
            lease.cancel()
        endpoint.close()
        self.pool.close(str(connection_id))
        if not self._connections and self._pruner is not None:
            self._pruner.cancel()
            self._pruner = None

    async def ready(self, connection_id: UUID) -> NegotiatedConnection:
        """Wait for this connection's discovery, independently of chat processing."""
        task = self._negotiations.get(connection_id)
        if task is None:
            endpoint = self._connections.get(connection_id)
            if endpoint is None:
                raise RequestClosedError(reason="no v2 negotiation for this connection")
            return NegotiatedConnection(_VERSION, endpoint.features, endpoint)
        result = await asyncio.shield(task)
        if (
            result.endpoint is not None
            and self._connections.get(connection_id) is not result.endpoint
        ):
            raise RequestClosedError(reason="v2 session closed")
        return result

    async def request(
        self,
        connection_id: UUID,
        method: str,
        params: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        require: Collection[str] = (),
    ) -> dict[str, Any]:
        """Call a negotiated capability; required features never silently downgrade."""
        selection = await self.ready(connection_id)
        required = set(require)
        if metadata is not None or trace_id is not None:
            required.add("metadata/1")
        missing = required - selection.features
        if missing:
            raise NegotiationError(
                "UNSUPPORTED_PROTOCOL",
                f"unsupported required features: {', '.join(sorted(missing))}",
            )
        if selection.endpoint is None:
            raise NegotiationError("UNSUPPORTED_PROTOCOL", "no MCBEWS/2 endpoint")
        return await selection.endpoint.request(
            method, params, metadata=metadata, trace_id=trace_id, require=required
        )

    async def stream(
        self,
        connection_id: UUID,
        method: str,
        params: object,
        *,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        require: Collection[str] = (),
    ) -> IncomingStream:
        """Open a negotiated request stream; streams require the ``stream/1`` feature."""
        selection = await self.ready(connection_id)
        if selection.endpoint is None or "stream/1" not in selection.features:
            raise NegotiationError(
                "UNSUPPORTED_PROTOCOL", "unsupported required features: stream/1"
            )
        return await selection.endpoint.stream(
            method, params, metadata=metadata, trace_id=trace_id, require=require
        )

    def _activate(
        self,
        state: ConnectionState,
        *,
        epoch: str,
        command_bytes: int | None = None,
        features: Collection[str] | None = None,
    ) -> V2Endpoint:
        """Install the endpoint selected by a matching challenge/ready pair.

        Private on purpose: negotiation is the only production admission path.
        """
        if state.runtime is None or state.requests is None:
            raise ValueError("MCBEWS/2 requires a connection runtime")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", epoch):
            raise ValueError("invalid epoch")
        if state.id in self._connections:
            raise ValueError("MCBEWS/2 is already active on this connection")
        budget = state.runtime.command_line_byte_budget if command_bytes is None else command_bytes
        limits({"command_bytes": budget})
        if budget > state.runtime.command_line_byte_budget:
            raise ValueError("v2 command budget exceeds the connection budget")
        endpoint = V2Endpoint(self, state, epoch, budget, features=features)
        self._connections[state.id] = endpoint
        if self._pruner is None:
            self._pruner = asyncio.create_task(self._prune(), name="v2-reassembly-pruner")
        return endpoint

    async def _prune(self) -> None:
        while True:
            await asyncio.sleep(5)
            self.pool.prune()

    def connection(self, connection_id: UUID) -> V2Endpoint | None:
        """Return the active endpoint, if any."""
        return self._connections.get(connection_id)

    async def receive(self, state: ConnectionState, sender: str, frame: str) -> None:
        """Route a frame attributed by the server's player-message parser."""
        endpoint = self._connections.get(state.id)
        if endpoint is not None and sender == _TRUSTED_PLAYER:
            await endpoint.receive(frame, source=sender)

    def close(self, connection_id: UUID) -> None:
        """Release one connection without disturbing other connections."""
        endpoint = self._connections.pop(connection_id, None)
        lease = self._leases.pop(connection_id, None)
        if lease is not None:
            lease.cancel()
        self._lease_acks.pop(connection_id, None)
        self._controls.pop(connection_id, None)
        task = self._negotiations.pop(connection_id, None)
        if task is not None:
            if not task.done():
                task.cancel()
            task.add_done_callback(
                lambda completed: completed.exception() if not completed.cancelled() else None
            )
        # A task cancelled before its first step never enters discover's finally.
        discovery = self._discoveries.pop(connection_id, None)
        if discovery is not None:
            discovery.requests.cancel(discovery.pending)
            if not discovery.challenge.done():
                discovery.challenge.cancel()
            elif not discovery.challenge.cancelled():
                discovery.challenge.exception()
        if endpoint is not None:
            endpoint.close()
        self.pool.close(str(connection_id))
        if not self._connections and self._pruner is not None:
            self._pruner.cancel()
            self._pruner = None

    async def close_connection(self, connection_id: UUID) -> None:
        """Retire one peer and send a bounded best-effort close control."""
        endpoint = self._connections.get(connection_id)
        send = self._controls.get(connection_id)
        self.close(connection_id)
        if endpoint is not None:
            await endpoint.streams.wait_closed()
        if endpoint is not None and send is not None:
            try:
                async with asyncio.timeout(min(self.negotiation_timeout_seconds, 1.0)):
                    await send(f"MCBEWS2X|{endpoint.epoch}")
            except Exception:
                pass


class V2Endpoint:
    """Send and receive v2 messages for one explicitly agreed connection epoch."""

    def __init__(
        self,
        owner: V2Bridge,
        state: ConnectionState,
        epoch: str,
        command_bytes: int,
        *,
        features: Collection[str] | None = None,
    ) -> None:
        assert state.runtime is not None and state.requests is not None
        self._owner = owner
        self._state = state
        self._runtime = state.runtime
        self._requests = state.requests
        self.epoch = epoch
        self.features = frozenset(_NEGOTIATION["features"]) & frozenset(
            _NEGOTIATION["features"] if features is None else features
        )
        self._command_bytes = command_bytes
        self._sequence = 0
        self._active_methods: set[str] = set()
        self._control_tasks: set[asyncio.Task[None]] = set()
        self._send_tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False
        self.streams = StreamManager(self._send, owner.streams, overrides=owner.stream_limits)

    def _next_id(self) -> str:
        if self._closed:
            raise RequestClosedError(reason="v2 endpoint closed")
        self._sequence += 1
        if self._sequence > 9_999_999_999:
            raise RequestClosedError(reason="v2 epoch exhausted")
        return f"p.{self.epoch}.{self._sequence}"

    def _check_features(
        self,
        metadata: dict[str, Any] | None,
        trace_id: str | None,
        require: Collection[str],
        *,
        notification: bool = False,
    ) -> None:
        required = set(require)
        if metadata is not None or trace_id is not None or current_trace().trace_id is not None:
            required.add("metadata/1")
        if notification:
            required.add("notifications/1")
        missing = required - self.features
        if missing:
            raise NegotiationError(
                "UNSUPPORTED_PROTOCOL",
                f"unsupported required features: {', '.join(sorted(missing))}",
            )

    def _metadata(self, metadata: dict[str, Any] | None, trace_id: str | None) -> dict[str, Any]:
        meta = dict(metadata or {})
        if any(key.startswith("sdk.") for key in meta):
            raise ValueError("sdk.* metadata is reserved")
        trace = trace_id or current_trace().trace_id
        if trace is not None:
            meta["sdk.trace_id"] = trace
        return meta

    async def _send(self, message: dict[str, Any]) -> None:
        await send_downstream(
            self._runtime.scheduler,
            message,
            self._next_id(),
            overrides={"command_bytes": self._command_bytes},
            priority=(
                SendPriority.CONTROL
                if message["kind"] in ("response", "stream_end", "stream_error", "stream_cancel")
                else SendPriority.BULK
                if message["kind"] == "stream_item"
                else SendPriority.REQUEST
            ),
        )

    async def _send_request(self, message: dict[str, Any], request_id: str) -> None:
        try:
            await self._send(message)
        except Exception as exc:
            # A response or close may already have settled the same request.
            self._requests.fail(_CATEGORY, request_id, exc)

    async def request(
        self,
        method: str,
        params: object,
        *,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        require: Collection[str] = (),
    ) -> dict[str, Any]:
        """Issue a request with one deadline for sending and the remote response."""
        self._check_features(metadata, trace_id, require)
        request_id = self._next_id()
        pending = self._requests.reserve(_CATEGORY, request_id=request_id)
        deadline = asyncio.get_running_loop().time() + self._owner.timeout_seconds
        send_task: asyncio.Task[None] | None = None
        try:
            message = {
                "v": _VERSION,
                "kind": "request",
                "id": request_id,
                "method": method,
                "params": params,
                "meta": self._metadata(metadata, trace_id),
            }
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RequestTimeoutError(request_id, self._owner.timeout_seconds)
            send_task = asyncio.create_task(
                self._send_request(message, request_id), name=f"v2-send:{request_id}"
            )
            self._send_tasks[request_id] = send_task
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            done, _ = await asyncio.wait({pending.future}, timeout=remaining)
            if not done:
                raise RequestTimeoutError(request_id, self._owner.timeout_seconds)
            response: dict[str, Any] = pending.future.result()
            if "error" in response:
                error = response["error"]
                raise RemoteCallError(request_id, code=error["code"], message=error["message"])
            return response
        finally:
            self._requests.cancel(pending)
            if send_task is not None:
                send_task.cancel()
                try:
                    await asyncio.gather(send_task, return_exceptions=True)
                finally:
                    self._send_tasks.pop(request_id, None)

    async def notify(
        self,
        method: str,
        params: object,
        *,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        require: Collection[str] = (),
    ) -> None:
        """Send a notification without creating a reply waiter."""
        self._check_features(metadata, trace_id, require, notification=True)
        await self._send(
            {
                "v": _VERSION,
                "kind": "notification",
                "method": method,
                "params": params,
                "meta": self._metadata(metadata, trace_id),
            }
        )

    async def stream(
        self,
        method: str,
        params: object,
        *,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        require: Collection[str] = (),
    ) -> IncomingStream:
        """Start consuming before the producer knows its final item count.

        Use the returned iterator as an async context manager when consumption
        may stop early. Buffers are bounded; a slow consumer fails explicitly.
        """
        self._check_features(metadata, trace_id, {*require, "stream/1"})
        return await self.streams.open(
            {
                "v": _VERSION,
                "kind": "stream_open",
                "id": self._next_id(),
                "method": method,
                "params": params,
                "meta": self._metadata(metadata, trace_id),
            }
        )

    async def receive(self, frame: str, *, source: str) -> None:
        """Pair responses inline and admit incoming calls to the business dispatcher."""
        if self._closed or not frame.startswith(f"MCBEWS2|a.{self.epoch}."):
            return
        message = self._owner.pool.receive(str(self._state.id), source, frame, "up")
        if message is None:
            return
        kind = message["kind"]
        if kind.startswith("stream_"):
            if "stream/1" not in self.features:
                return
            expected_origin = "a" if kind in ("stream_open", "stream_cancel") else "p"
            if not message["id"].startswith(f"{expected_origin}.{self.epoch}."):
                return
            self.streams.receive(
                message,
                CapabilityContext(
                    caller=source,
                    request_version=2,
                    metadata=message.get("meta", {}),
                    connection_id=str(self._state.id),
                    client=self,
                ),
            )
            return
        if kind == "response":
            if message["id"].startswith(f"p.{self.epoch}.") and self._requests.resolve(
                _CATEGORY, message["id"], message
            ):
                send_task = self._send_tasks.get(message["id"])
                if send_task is not None:
                    send_task.cancel()
            return
        if kind == "request" and not message["id"].startswith(f"a.{self.epoch}."):
            return
        method = message["method"]
        dispatcher = self._runtime.business
        overloaded = dispatcher.active_count + dispatcher.pending_count >= dispatcher.max_concurrent
        if method in self._active_methods or overloaded:
            if kind == "request":
                self._schedule_error(message, "ordering group busy")
            return
        self._active_methods.add(method)

        async def dispatch() -> None:
            try:
                await self._dispatch(message, source)
            finally:
                self._active_methods.discard(method)

        if not dispatcher.submit(dispatch, lane=f"v2:{method}", label="v2 capability"):
            self._active_methods.discard(method)
            if kind == "request":
                self._schedule_error(message, "business queue full")

    def _schedule_error(self, message: dict[str, Any], detail: str) -> None:
        if len(self._control_tasks) >= self._runtime.business.max_concurrent:
            return
        task = asyncio.create_task(self._reply_error(message, "LIMIT_EXCEEDED", detail))
        self._control_tasks.add(task)

        def finished(completed: asyncio.Task[None]) -> None:
            self._control_tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finished)

    async def _dispatch(self, message: dict[str, Any], source: str) -> None:
        if self._closed:
            return
        params = message["params"]
        if not isinstance(params, dict):
            if message["kind"] == "request":
                await self._reply_error(message, "INVALID_PARAMS", "parameters must be an object")
            return
        metadata = message.get("meta", {})
        context = CapabilityContext(
            caller=source,
            request_version=2,
            metadata=metadata,
            connection_id=str(self._state.id),
            client=self,
        )
        with trace_scope(
            connection_id=str(self._state.id),
            request_id=message.get("id"),
            trace_id=metadata.get("sdk.trace_id"),
        ):
            result = await self._owner.registry.dispatch(message["method"], params, context=context)
        if message["kind"] == "notification":
            return
        if result.get("ok") is False and isinstance(result.get("error"), dict):
            error = result["error"]
            code = {
                "UNSUPPORTED_CAPABILITY": "UNSUPPORTED_METHOD",
                "INVALID_REQUEST": "INVALID_PARAMS",
                "CAPABILITY_FAILED": "HANDLER_FAILED",
            }.get(error.get("code"))
            if code is not None:
                detail = str(error.get("message", "failure"))
                detail = detail.encode("utf-8")[:256].decode("utf-8", errors="ignore")
                await self._reply_error(message, code, detail)
                return
        try:
            await self._send(
                {
                    "v": _VERSION,
                    "kind": "response",
                    "id": message["id"],
                    "result": result,
                    "meta": metadata,
                }
            )
        except V2ProtocolError:
            await self._reply_error(message, "HANDLER_FAILED", "invalid handler result")

    async def _reply_error(self, message: dict[str, Any], code: str, detail: str) -> None:
        await self._send(
            {
                "v": _VERSION,
                "kind": "response",
                "id": message["id"],
                "error": {"code": code, "message": detail},
                "meta": message.get("meta", {}),
            }
        )

    def close(self) -> None:
        """Fail this endpoint's waiters and stop its unsent request fragments."""
        if self._closed:
            return
        self._closed = True
        self.streams.close()
        self._owner._retire_endpoint(self._state.id, self)
        for task in self._control_tasks:
            task.cancel()
        for request_id in list(self._requests.entries(_CATEGORY)):
            self._requests.fail(_CATEGORY, request_id, RequestClosedError(request_id))
        for task in self._send_tasks.values():
            task.cancel()


__all__ = ["NegotiatedConnection", "NegotiationError", "V2Bridge", "V2Endpoint", "V2ProtocolError"]
