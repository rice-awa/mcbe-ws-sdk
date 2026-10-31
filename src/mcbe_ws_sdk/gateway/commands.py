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
from uuid import UUID

import structlog

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.delivery.outbound import McbeOutboundDelivery
from mcbe_ws_sdk.errors import RequestTimeoutError
from mcbe_ws_sdk.gateway.connection import SendPayload
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommandResponse
from mcbe_ws_sdk.requests import CATEGORY_COMMAND, RequestTable

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
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._connection_id = connection_id
        self._requests = requests
        self._timeout = timeout_seconds
        self._delivery = McbeOutboundDelivery(
            connection_id=connection_id,
            send_payload=send_payload,
            settings=flow,
            log_raw_payloads=log_raw_payloads,
        )

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    async def run(self, command: str) -> MinecraftCommandResponse:
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
            self._requests.reserve(
                CATEGORY_COMMAND,
                request_id=request_id,
                future=future,
            )
            registered.append(request_id)

        try:
            request_id = await self._delivery.send_raw_command(
                command,
                source="native_command",
                before_send=_register,
            )
        except BaseException as exc:
            if registered:
                self._requests.discard(CATEGORY_COMMAND, registered[0])
                if not future.done():
                    future.set_exception(exc)
            raise

        try:
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError as exc:
            logger.warning(
                "native_command_timeout",
                connection_id=str(self._connection_id),
                request_id=request_id,
                timeout_seconds=self._timeout,
            )
            raise RequestTimeoutError(request_id, self._timeout) from exc
        finally:
            self._requests.discard(CATEGORY_COMMAND, request_id)


__all__ = ["NativeCommandClient"]
