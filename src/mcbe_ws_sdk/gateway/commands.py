"""Awaitable native MCBE command entry.

The SDK pairs ``commandRequest`` / ``commandResponse`` frames through the same
:class:`~mcbe_ws_sdk.requests.RequestTable` used by the addon bridge. Callers
await :meth:`NativeCommandClient.run` instead of maintaining a host-side
``requestId -> future`` map.

Timeout semantics: :attr:`NativeCommandClient.timeout_seconds` covers only the
wait for the remote reply, measured from the moment the frame was accepted by
the send path. Time spent waiting for send capacity is reported by the send path
itself (for example :class:`~mcbe_ws_sdk.errors.SendQueueFullError`).
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

import structlog

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.delivery.outbound import McbeOutboundDelivery
from mcbe_ws_sdk.diagnostics import Diagnostics
from mcbe_ws_sdk.errors import RequestTimeoutError
from mcbe_ws_sdk.gateway.connection import SendPayload
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommandResponse
from mcbe_ws_sdk.requests import CATEGORY_COMMAND, RequestTable
from mcbe_ws_sdk.tracing import observe, trace_scope

logger = structlog.get_logger(__name__)


class NativeCommandClient:
    """Send a native game command and await its ``commandResponse``."""

    def __init__(
        self,
        *,
        connection_id: UUID,
        requests: RequestTable,
        flow: FlowControlSettings,
        send_payload: SendPayload,
        timeout_seconds: float = 5.0,
        log_raw_payloads: bool = False,
        diagnostics: Diagnostics | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._connection_id = connection_id
        self._diagnostics = diagnostics
        self._requests = requests
        self._timeout = timeout_seconds

        async def send_command(payload: str) -> None:
            request_id = str(json.loads(payload)["header"]["requestId"])
            with trace_scope(connection_id=str(connection_id), request_id=request_id):
                await send_payload(payload)

        self._delivery = McbeOutboundDelivery(
            connection_id=connection_id,
            send_payload=send_command,
            settings=flow,
            log_raw_payloads=log_raw_payloads,
        )

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    async def run(self, command: str) -> MinecraftCommandResponse:
        """Run a command within the current operation trace and await its reply.

        Registers before sending. The timeout covers remote waiting after send;
        encoding, admission, transport and remote error-frame failures propagate.
        Nonzero statusCode replies are returned unchanged for legacy compatibility.
        """
        with trace_scope(connection_id=str(self._connection_id), diagnostics=self._diagnostics):
            return await self._run(command)

    async def _run(self, command: str) -> MinecraftCommandResponse:
        """Send ``command`` and return its reply.

        Registration happens in ``before_send``, so a reply delivered while the
        transport call is still running is not lost.

        Raises:
            FrameTooLargeError: The command exceeds the command-line byte budget.
            RequestTimeoutError: No reply arrived within ``timeout_seconds``.
            RemoteCallError: The remote side reported failure.
            BaseException: The original send/transport failure.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[MinecraftCommandResponse] = loop.create_future()
        registered: list[str] = []

        def _register(request_id: str) -> None:
            with trace_scope(request_id=request_id):
                self._requests.reserve(CATEGORY_COMMAND, request_id=request_id, future=future)
                observe("request_registered", category=CATEGORY_COMMAND)
            registered.append(request_id)

        try:
            request_id = await self._delivery.send_raw_command(
                command,
                source="native_command",
                before_send=_register,
            )
        except BaseException as exc:
            observe(
                "request_failed",
                request_id=registered[0] if registered else None,
                error_type=type(exc).__name__,
            )
            if registered:
                self._requests.discard(CATEGORY_COMMAND, registered[0])
                if not future.done():
                    future.set_exception(exc)
            raise

        try:
            response = await asyncio.wait_for(future, self._timeout)
            status = response.body.get("statusCode")
            observe(
                ("remote_failure" if status != 0 else "remote_success")
                if isinstance(status, int)
                else "remote_response",
                request_id=request_id,
                category=CATEGORY_COMMAND,
            )
            return response
        except TimeoutError as exc:
            observe("request_timeout", request_id=request_id, category=CATEGORY_COMMAND)
            logger.warning(
                "native_command_timeout",
                connection_id=str(self._connection_id),
                request_id=request_id,
                timeout_seconds=self._timeout,
            )
            raise RequestTimeoutError(request_id, self._timeout) from exc
        except BaseException as exc:
            observe("request_failed", request_id=request_id, error_type=type(exc).__name__)
            raise
        finally:
            self._requests.discard(CATEGORY_COMMAND, request_id)


__all__ = ["NativeCommandClient"]
