"""R12 request lifecycle and multibyte validation regressions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest

from mcbe_ws_sdk.config import DispatchSettings, FlowControlSettings, SendSchedulerSettings
from mcbe_ws_sdk.errors import RemoteCallError, RequestClosedError, RequestTimeoutError
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.gateway.runtime import ConnectionRuntime
from mcbe_ws_sdk.gateway.sender import OutboundScheduler
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX, ReassemblyPool, fragment
from mcbe_ws_sdk.requests import RequestTable


class Harness:
    def __init__(
        self,
        transport: Callable[[str], Awaitable[None]],
        *,
        timeout: float = 1.0,
        bridge: V2Bridge | None = None,
        send: SendSchedulerSettings | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.state = ConnectionState()
        self.runtime = ConnectionRuntime(
            connection_id=self.state.id,
            dispatch=DispatchSettings(mode="reliable"),
            send=send or SendSchedulerSettings(),
            flow=FlowControlSettings(),
            transport=transport,
            sleep=sleep,
        )
        self.state.runtime = self.runtime
        self.state.requests = self.runtime.requests
        self.bridge = bridge or V2Bridge(timeout_seconds=timeout, negotiate=False)
        self.endpoint = self.bridge.activate(self.state, epoch="test")

    async def close(self) -> None:
        self.bridge.close(self.state.id)
        await self.runtime.close()


def request_id(harness: Harness) -> str:
    return next(iter(harness.runtime.requests.entries("addon.v2")))


async def respond(harness: Harness, response: dict[str, Any], sequence: int = 1) -> None:
    for frame in fragment(response, f"a.test.{sequence}", "up"):
        await harness.endpoint.receive(frame, source="MCBEWS_BRIDGE")


def frame_count(payload: str) -> int:
    command = json.loads(payload)["body"]["commandLine"]
    assert command.startswith(COMMAND_PREFIX)
    return int(command[len(COMMAND_PREFIX) :].split("|", 3)[2].split("/")[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "close", "cancel", "response", "error"])
async def test_blocked_send_terminates_and_never_sends_remaining_fragments(outcome: str) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    sent: list[str] = []

    async def transport(payload: str) -> None:
        sent.append(payload)
        if len(sent) == 1:
            started.set()
            await release.wait()

    harness = Harness(transport, timeout=0.05 if outcome == "timeout" else 10)
    call = asyncio.create_task(harness.endpoint.request("world.write", {"text": "x" * 1200}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert harness.runtime.requests.pending_count == 1
        assert frame_count(sent[0]) > 1
        identifier = request_id(harness)
        owned_send = harness.endpoint._send_tasks[identifier]
        if outcome == "close":
            harness.bridge.close(harness.state.id)
        elif outcome == "cancel":
            call.cancel()
        elif outcome in {"response", "error"}:
            response: dict[str, Any] = {
                "v": "MCBEWS/2",
                "kind": "response",
                "id": identifier,
                "meta": {"extension": {"hello": "玩家"}},
            }
            if outcome == "response":
                response["result"] = {"ok": True}
            else:
                response["error"] = {"code": "INVALID_PARAMS", "message": "no"}
            await respond(harness, response)

        if outcome == "timeout":
            with pytest.raises(RequestTimeoutError):
                await asyncio.wait_for(asyncio.shield(call), 1)
        elif outcome == "close":
            with pytest.raises(RequestClosedError):
                await asyncio.wait_for(asyncio.shield(call), 1)
        elif outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(call), 1)
        elif outcome == "error":
            with pytest.raises(RemoteCallError) as failure:
                await asyncio.wait_for(asyncio.shield(call), 1)
            assert failure.value.code == "INVALID_PARAMS"
        else:
            assert await asyncio.wait_for(asyncio.shield(call), 1) == response
        assert harness.runtime.requests.pending_count == 0
        assert owned_send.done() and not harness.endpoint._send_tasks
        if outcome in {"timeout", "close", "cancel"}:
            late = {"v": "MCBEWS/2", "kind": "response", "id": identifier, "result": {}}
            await respond(harness, late, sequence=2)
            assert harness.runtime.requests.pending_count == 0

        release.set()
        await asyncio.wait_for(harness.runtime.send_raw("unrelated"), 2)
        assert sent[-1] == "unrelated"
        assert len(sent) == 2
        assert not harness.runtime.scheduler.is_closed
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        await harness.close()


@pytest.mark.asyncio
async def test_close_keeps_unrelated_requests_and_connections_alive() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(_payload: str) -> None:
        started.set()
        await release.wait()

    async def healthy(_payload: str) -> None:
        return None

    first = Harness(blocked, timeout=10)
    second = Harness(healthy, bridge=first.bridge)
    unrelated = first.runtime.requests.reserve("command.native", request_id="unrelated")
    call = asyncio.create_task(first.endpoint.request("world.write", {"text": "x" * 1200}))
    other = asyncio.create_task(second.endpoint.request("echo", {}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        async with asyncio.timeout(2):
            while second.runtime.requests.pending_count == 0:
                await asyncio.sleep(0)
        other_id = request_id(second)
        first.bridge.close(first.state.id)
        with pytest.raises(RequestClosedError):
            await asyncio.wait_for(asyncio.shield(call), 1)
        assert not unrelated.future.done()
        assert first.runtime.requests.pending_count == 1
        response = {"v": "MCBEWS/2", "kind": "response", "id": other_id, "result": {"ok": True}}
        await respond(second, response)
        assert await asyncio.wait_for(other, 1) == response
        assert second.runtime.requests.pending_count == 0
    finally:
        release.set()
        first.runtime.requests.cancel(unrelated)
        for task in (call, other):
            if not task.done():
                task.cancel()
        await asyncio.gather(call, other, return_exceptions=True)
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_cancelling_one_request_leaves_same_endpoint_request_usable() -> None:
    started, next_send, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sent: list[str] = []

    async def transport(payload: str) -> None:
        sent.append(payload)
        if len(sent) == 1:
            started.set()
            await release.wait()
        else:
            next_send.set()

    harness = Harness(transport, timeout=10)
    first = asyncio.create_task(harness.endpoint.request("world.write", {"text": "x" * 1200}))
    second: asyncio.Task[dict[str, Any]] | None = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        first_id = request_id(harness)
        second = asyncio.create_task(harness.endpoint.request("echo", {}))
        async with asyncio.timeout(2):
            while harness.runtime.requests.pending_count != 2:
                await asyncio.sleep(0)
        second_id = next(
            identifier
            for identifier in harness.runtime.requests.entries("addon.v2")
            if identifier != first_id
        )
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        await asyncio.wait_for(next_send.wait(), 2)
        response = {"v": "MCBEWS/2", "kind": "response", "id": second_id, "result": {}}
        await respond(harness, response)
        assert await asyncio.wait_for(second, 2) == response
        assert len(sent) == 2 and frame_count(sent[0]) > 1
        assert harness.runtime.requests.pending_count == 0
    finally:
        release.set()
        tasks = [task for task in (first, second) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("broken transport"), TimeoutError("send timeout")]
)
async def test_send_failure_reaches_caller_and_cleans_request(failure: Exception) -> None:
    sent: list[str] = []

    async def transport(payload: str) -> None:
        sent.append(payload)
        raise failure

    harness = Harness(transport)
    try:
        with pytest.raises(type(failure)) as raised:
            await asyncio.wait_for(harness.endpoint.request("world.write", {"text": "x" * 1200}), 2)
        assert raised.value is failure
        assert len(sent) == 1
        assert harness.runtime.requests.pending_count == 0
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_deadline_is_shared_between_sending_and_remote_wait() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def transport(_payload: str) -> None:
        started.set()
        await release.wait()

    harness = Harness(transport, timeout=0.3)
    call = asyncio.create_task(harness.endpoint.request("echo", {}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.sleep(0.2)
        release.set()
        with pytest.raises(RequestTimeoutError):
            await asyncio.wait_for(asyncio.shield(call), 0.2)
        assert harness.runtime.requests.pending_count == 0
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        await harness.close()


@pytest.mark.asyncio
async def test_deadline_cancels_request_waiting_for_scheduler_capacity() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    sent: list[str] = []

    async def transport(payload: str) -> None:
        sent.append(payload)
        if payload == "occupy":
            started.set()
            await release.wait()

    harness = Harness(transport, timeout=0.05, send=SendSchedulerSettings(queue_maxsize=1))
    occupant = asyncio.create_task(harness.runtime.send_raw("occupy"))
    try:
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(RequestTimeoutError):
            await asyncio.wait_for(harness.endpoint.request("world.write", {}), 1)
        assert harness.runtime.requests.pending_count == 0
        assert not harness.endpoint._send_tasks
        release.set()
        await asyncio.wait_for(occupant, 2)
        await asyncio.wait_for(harness.runtime.send_raw("unrelated"), 2)
        assert sent == ["occupy", "unrelated"]
    finally:
        release.set()
        await asyncio.gather(occupant, return_exceptions=True)
        await harness.close()


@pytest.mark.asyncio
async def test_response_before_close_wins_without_late_fragments() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    sent: list[str] = []

    async def transport(payload: str) -> None:
        sent.append(payload)
        if len(sent) == 1:
            started.set()
            await release.wait()

    harness = Harness(transport, timeout=10)
    call = asyncio.create_task(harness.endpoint.request("world.write", {"text": "x" * 1200}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        response = {"v": "MCBEWS/2", "kind": "response", "id": request_id(harness), "result": 7}
        await respond(harness, response)
        harness.bridge.close(harness.state.id)
        assert await asyncio.wait_for(asyncio.shield(call), 1) == response
        release.set()
        await asyncio.wait_for(harness.runtime.send_raw("unrelated"), 2)
        assert len(sent) == 2 and sent[-1] == "unrelated"
        assert harness.runtime.requests.pending_count == 0
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        await harness.close()


@pytest.mark.asyncio
async def test_response_before_transport_failure_wins_once() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def transport(_payload: str) -> None:
        started.set()
        await release.wait()
        raise RuntimeError("transport failed after response")

    harness = Harness(transport, timeout=10)
    call = asyncio.create_task(harness.endpoint.request("world.write", {"text": "x" * 1200}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        response = {"v": "MCBEWS/2", "kind": "response", "id": request_id(harness), "result": 7}
        await respond(harness, response)
        release.set()
        assert await asyncio.wait_for(call, 1) == response
        async with asyncio.timeout(2):
            while not harness.runtime.scheduler.is_closed:
                await asyncio.sleep(0)
        assert harness.runtime.requests.pending_count == 0
        assert not harness.endpoint._send_tasks
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail",
    ["错" * 100, "🙂" * 70, "A" * 254 + "错", "A" * 256],
    ids=["chinese", "emoji", "split-scalar", "exact-boundary"],
)
async def test_unicode_validation_error_reply_arrives_and_cleans_up(detail: str) -> None:
    receiver = ReassemblyPool()
    remote = RequestTable()
    pending = remote.reserve("remote", request_id="a.test.1")

    async def transport(payload: str) -> None:
        command = json.loads(payload)["body"]["commandLine"]
        response = receiver.receive("connection", "python", command[len(COMMAND_PREFIX) :], "down")
        if response is not None:
            remote.resolve("remote", response["id"], response)

    harness = Harness(transport)
    harness.bridge.registry.register("reject", lambda *_: {}, validate=lambda *_: detail)
    message = {
        "v": "MCBEWS/2",
        "kind": "request",
        "id": "a.test.1",
        "method": "reject",
        "params": {},
        "meta": {"extension": ["玩家", None, True]},
    }
    try:
        for frame in fragment(message, "a.test.2", "up"):
            await harness.endpoint.receive(frame, source="MCBEWS_BRIDGE")
        response = await asyncio.wait_for(asyncio.shield(pending.future), 0.5)
        assert response["error"] == {
            "code": "INVALID_PARAMS",
            "message": detail.encode("utf-8")[:256].decode("utf-8", errors="ignore"),
        }
        assert response["meta"] == message["meta"]
        assert remote.pending_count == 0
        async with asyncio.timeout(2):
            while harness.runtime.business.active_count:
                await asyncio.sleep(0)
        assert harness.runtime.business.failed_count == 0
    finally:
        remote.cancel(pending)
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "close", "cancel"])
async def test_abandoned_rate_limited_final_frame_cannot_complete_message(outcome: str) -> None:
    rate_limited, release = asyncio.Event(), asyncio.Event()
    sent: list[str] = []

    async def transport(payload: str) -> None:
        sent.append(payload)

    async def sleep(_delay: float) -> None:
        rate_limited.set()
        await release.wait()

    harness = Harness(
        transport,
        timeout=0.1 if outcome == "timeout" else 10,
        send=SendSchedulerSettings(min_interval_seconds=0.3),
        sleep=sleep,
    )
    call = asyncio.create_task(harness.endpoint.request("world.write", {"text": "x" * 500}))
    try:
        await asyncio.wait_for(rate_limited.wait(), 2)
        assert len(sent) == 1 and frame_count(sent[0]) == 2
        if outcome == "close":
            harness.bridge.close(harness.state.id)
        elif outcome == "cancel":
            call.cancel()
        error = {
            "timeout": RequestTimeoutError,
            "close": RequestClosedError,
            "cancel": asyncio.CancelledError,
        }[outcome]
        with pytest.raises(error):
            await asyncio.wait_for(asyncio.shield(call), 1)
        release.set()
        await asyncio.wait_for(harness.runtime.send_raw("unrelated"), 2)
        assert len(sent) == 2 and sent[-1] == "unrelated"
        assert harness.runtime.requests.pending_count == 0
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        await harness.close()


@pytest.mark.asyncio
async def test_scheduler_checks_cancellation_again_before_transport_task_starts() -> None:
    now = [0.0]
    sent: list[tuple[str, float]] = []
    target: asyncio.Task[None] | None = None

    async def sleep(delay: float) -> None:
        now[0] += delay
        if target is not None and not target.done():
            asyncio.get_running_loop().call_soon(target.cancel)

    async def transport(payload: str) -> None:
        sent.append((payload, now[0]))

    scheduler = OutboundScheduler(
        connection_id=uuid4(),
        transport=transport,
        min_interval=0.1,
        clock=lambda: now[0],
        sleep=sleep,
    )
    try:
        await scheduler.send("first")
        target = asyncio.create_task(scheduler.send("cancelled"))
        with pytest.raises(asyncio.CancelledError):
            await target
        await asyncio.wait_for(scheduler.send("unrelated"), 2)
        assert sent == [("first", 0.0), ("unrelated", 0.1)]
        assert scheduler.sent_count == 2
        assert scheduler.pending_count == 0
    finally:
        await scheduler.close()
