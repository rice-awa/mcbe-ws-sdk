"""Connection-bound MCBEWS/2 request dispatch for the reliable gateway."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from mcbe_ws_sdk.capability import CapabilityContext, CapabilityRegistry
from mcbe_ws_sdk.errors import RemoteCallError, RequestClosedError, RequestTimeoutError
from mcbe_ws_sdk.gateway.sender import SendPriority
from mcbe_ws_sdk.profiles.mcbews_v2.codec import V2ProtocolError
from mcbe_ws_sdk.profiles.mcbews_v2.transport import ReassemblyPool, limits, send_downstream
from mcbe_ws_sdk.tracing import current_trace, trace_scope

if TYPE_CHECKING:
    from mcbe_ws_sdk.gateway.connection import ConnectionState

_CATEGORY = "addon.v2"
_VERSION = "MCBEWS/2"


class V2Bridge:
    """Own a shared reassembly pool and registered capabilities for a facade."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.registry = CapabilityRegistry()
        self.pool = ReassemblyPool()
        self.timeout_seconds = timeout_seconds
        self._connections: dict[UUID, V2Endpoint] = {}
        self._pruner: asyncio.Task[None] | None = None

    def activate(
        self, state: ConnectionState, *, epoch: str, command_bytes: int | None = None
    ) -> V2Endpoint:
        """Activate an explicitly agreed epoch on a reliable connection."""
        if state.runtime is None or state.requests is None:
            raise ValueError("MCBEWS/2 requires reliable dispatch")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", epoch):
            raise ValueError("invalid epoch")
        if state.id in self._connections:
            raise ValueError("MCBEWS/2 is already active on this connection")
        budget = state.runtime.command_line_byte_budget if command_bytes is None else command_bytes
        limits({"command_bytes": budget})
        if budget > state.runtime.command_line_byte_budget:
            raise ValueError("v2 command budget exceeds the connection budget")
        endpoint = V2Endpoint(self, state, epoch, budget)
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
        """Route a frame attributed by the facade's player-message parser."""
        endpoint = self._connections.get(state.id)
        if endpoint is not None and sender == "MCBEWS_BRIDGE":
            await endpoint.receive(frame, source=sender)

    def close(self, connection_id: UUID) -> None:
        """Release one connection without disturbing other connections."""
        endpoint = self._connections.pop(connection_id, None)
        if endpoint is not None:
            endpoint.close()
        self.pool.close(str(connection_id))
        if not self._connections and self._pruner is not None:
            self._pruner.cancel()
            self._pruner = None


class V2Endpoint:
    """Send and receive v2 messages for one explicitly agreed connection epoch."""

    def __init__(
        self, owner: V2Bridge, state: ConnectionState, epoch: str, command_bytes: int
    ) -> None:
        assert state.runtime is not None and state.requests is not None
        self._owner = owner
        self._state = state
        self._runtime = state.runtime
        self._requests = state.requests
        self.epoch = epoch
        self._command_bytes = command_bytes
        self._sequence = 0
        self._active_methods: set[str] = set()
        self._control_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def _next_id(self) -> str:
        if self._closed:
            raise RequestClosedError(reason="v2 endpoint closed")
        self._sequence += 1
        if self._sequence > 9_999_999_999:
            raise RequestClosedError(reason="v2 epoch exhausted")
        return f"p.{self.epoch}.{self._sequence}"

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
                SendPriority.CONTROL if message["kind"] == "response" else SendPriority.REQUEST
            ),
        )

    async def request(
        self,
        method: str,
        params: object,
        *,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Issue a request and return its result and preserved response metadata."""
        request_id = self._next_id()
        pending = self._requests.reserve(_CATEGORY, request_id=request_id)
        try:
            message = {
                "v": _VERSION,
                "kind": "request",
                "id": request_id,
                "method": method,
                "params": params,
                "meta": self._metadata(metadata, trace_id),
            }
            await self._send(message)
            try:
                response: dict[str, Any] = await asyncio.wait_for(
                    pending.future, self._owner.timeout_seconds
                )
            except TimeoutError as exc:
                raise RequestTimeoutError(request_id, self._owner.timeout_seconds) from exc
            if "error" in response:
                error = response["error"]
                raise RemoteCallError(request_id, code=error["code"], message=error["message"])
            return response
        finally:
            self._requests.cancel(pending)

    async def notify(
        self,
        method: str,
        params: object,
        *,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Send a notification without creating a reply waiter."""
        await self._send(
            {
                "v": _VERSION,
                "kind": "notification",
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
        if kind == "response":
            if message["id"].startswith(f"p.{self.epoch}."):
                self._requests.resolve(_CATEGORY, message["id"], message)
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
                await self._reply_error(message, code, str(error.get("message", "failure"))[:128])
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
        """Fail this endpoint's waiters without closing unrelated request categories."""
        if self._closed:
            return
        self._closed = True
        for task in self._control_tasks:
            task.cancel()
        for request_id in list(self._requests.entries(_CATEGORY)):
            self._requests.fail(_CATEGORY, request_id, RequestClosedError(request_id))


__all__ = ["V2Bridge", "V2Endpoint", "V2ProtocolError"]
