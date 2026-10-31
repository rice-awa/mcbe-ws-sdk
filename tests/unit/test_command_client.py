"""R02: awaitable native command entry and reply pairing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from uuid import UUID

import pytest

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.errors import (
    FrameTooLargeError,
    RemoteCallError,
    RequestLimitError,
    RequestTimeoutError,
)
from mcbe_ws_sdk.gateway.commands import NativeCommandClient
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommandResponse
from mcbe_ws_sdk.requests import CATEGORY_COMMAND, RequestTable


def _response(request_id: str) -> MinecraftCommandResponse:
    return MinecraftCommandResponse.model_validate(
        {
            "header": {"requestId": request_id, "messagePurpose": "commandResponse"},
            "body": {"statusCode": 0, "statusMessage": "ok"},
        }
    )


def _request_id(payload: str) -> str:
    return str(json.loads(payload)["header"]["requestId"])


def _client(
    table: RequestTable,
    transport: Callable[[str], Awaitable[None]],
    *,
    timeout: float = 1.0,
) -> NativeCommandClient:
    return NativeCommandClient(
        connection_id=UUID(int=1),
        requests=table,
        flow=FlowControlSettings(),
        send_payload=transport,
        timeout_seconds=timeout,
    )


@pytest.mark.asyncio
async def test_reply_delivered_during_send_is_not_lost() -> None:
    table = RequestTable()
    seen: list[str] = []

    async def transport(payload: str) -> None:
        seen.append(payload)
        request_id = _request_id(payload)
        # Reply before the transport call returns.
        table.resolve(CATEGORY_COMMAND, request_id, _response(request_id))

    result = await _client(table, transport).run("say hi")

    assert len(seen) == 1
    assert result.request_id == _request_id(seen[0])
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_timeout_covers_only_remote_wait() -> None:
    """A slow send does not consume the reply timeout."""
    table = RequestTable()

    async def transport(payload: str) -> None:
        await asyncio.sleep(0.05)
        request_id = _request_id(payload)
        table.resolve(CATEGORY_COMMAND, request_id, _response(request_id))

    result = await _client(table, transport, timeout=0.01).run("say hi")
    assert result.body["statusCode"] == 0


@pytest.mark.asyncio
async def test_missing_reply_times_out_and_cleans_table() -> None:
    table = RequestTable()

    async def transport(payload: str) -> None:
        return None

    with pytest.raises(RequestTimeoutError):
        await _client(table, transport, timeout=0.01).run("say hi")
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_send_failure_propagates_and_cleans_table() -> None:
    table = RequestTable()
    failure = LookupError("transport down")

    async def transport(payload: str) -> None:
        raise failure

    with pytest.raises(LookupError) as exc_info:
        await _client(table, transport).run("say hi")
    assert exc_info.value is failure
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_remote_failure_reaches_caller() -> None:
    table = RequestTable()

    async def transport(payload: str) -> None:
        request_id = _request_id(payload)
        table.fail(
            CATEGORY_COMMAND,
            request_id,
            RemoteCallError(request_id, code=7, message="denied"),
        )

    with pytest.raises(RemoteCallError, match="denied"):
        await _client(table, transport).run("say hi")
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_oversized_command_fails_before_registration() -> None:
    table = RequestTable()
    called = False

    async def transport(payload: str) -> None:
        nonlocal called
        called = True

    with pytest.raises(FrameTooLargeError):
        await _client(table, transport).run("say " + "x" * 1000)
    assert called is False
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_limit_is_reported_before_send() -> None:
    table = RequestTable(max_pending=1)
    table.reserve(CATEGORY_COMMAND, request_id="existing")
    called = False

    async def transport(payload: str) -> None:
        nonlocal called
        called = True

    with pytest.raises(RequestLimitError):
        await _client(table, transport).run("say hi")
    assert called is False


@pytest.mark.asyncio
async def test_caller_cancellation_cleans_table() -> None:
    table = RequestTable()

    async def transport(payload: str) -> None:
        return None

    task = asyncio.create_task(_client(table, transport, timeout=10).run("say hi"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert table.pending_count == 0
