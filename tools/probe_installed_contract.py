#!/usr/bin/env python3
"""Probe installed v1/v2 contracts from a directory outside the checkout."""

from __future__ import annotations

import asyncio
import json
from importlib.resources import files

import mcbe_ws_sdk as sdk
from mcbe_ws_sdk.profiles.mcbews_v1 import (
    MCBEWS_V1_MANIFEST,
    MCBEWS_V1_WIRE_VECTORS,
    decode_text_response_frame,
    encode_session_request_chat,
)
from mcbe_ws_sdk.profiles.mcbews_v2.codec import MANIFEST, V2ProtocolError, decode, encode
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge
from mcbe_ws_sdk.profiles.mcbews_v2.transport import COMMAND_PREFIX, ReassemblyPool, fragment


def probe_assets() -> None:
    assert sdk.McbeServerFacade().settings.dispatch.mode == "legacy"
    assert sdk.McbeServer().settings.dispatch.mode == "reliable"
    assert sdk.V2Bridge is V2Bridge
    assert sdk.IncomingStream.__name__ == "IncomingStream"
    assert sdk.StreamError.__name__ == "StreamError"
    assert MCBEWS_V1_MANIFEST["protocol_line"] == "MCBEWS/1"
    assert set(MCBEWS_V1_WIRE_VECTORS["behavior"]) == {"chunking", "text_response"}
    request = sdk.SessionRequest(request_id="wheel-1", action="list", player_name="Steve")
    assert '"request_id":"wheel-1"' in encode_session_request_chat(request)
    flow = sdk.FlowControlMiddleware(sdk.FlowControlSettings(command_line_byte_budget=180))
    payloads = flow.chunk_framed_scriptevent(
        "你好 😀" * 30,
        message_id="mcbews:text_resp",
        encode_frame=lambda content, index, total: json.dumps(
            {"id": "wheel-1", "i": index, "n": total, "p": "Steve", "r": "assistant", "c": content},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        emit_empty=True,
    )
    content = []
    for payload in payloads:
        command = json.loads(payload)["body"]["commandLine"]
        assert len(command.encode()) <= 180
        content.append(decode_text_response_frame(json.loads(command.split(" ", 2)[2])).content)
    assert "".join(content) == "你好 😀" * 30
    package = files("mcbe_ws_sdk.profiles.mcbews_v2")
    assert json.loads(package.joinpath("manifest.json").read_text()) == MANIFEST
    vectors = json.loads(package.joinpath("vectors.json").read_text())
    for vector in vectors["valid"]:
        assert decode(encode(vector["message"])) == vector["message"]
    for vector in vectors["invalid"] + vectors["raw_invalid"]:
        try:
            decode(vector["wire"]) if "wire" in vector else encode(vector["message"])
        except V2ProtocolError as exc:
            assert exc.code == vector["expected"]
        else:
            raise AssertionError(f"invalid vector accepted: {vector['name']}")


async def probe_runtime() -> None:
    bridge = V2Bridge(negotiate=False)
    state = sdk.ConnectionState()
    pool = ReassemblyPool()
    outbound: asyncio.Queue[dict] = asyncio.Queue()

    async def transport(payload: str) -> None:
        command = json.loads(payload)["body"]["commandLine"]
        assert len(command.encode()) <= 461
        message = pool.receive("wheel", "server", command[len(COMMAND_PREFIX) :], "down")
        if message is not None:
            await outbound.put(message)

    runtime = sdk.ConnectionRuntime(
        connection_id=state.id,
        dispatch=sdk.DispatchSettings(mode="reliable"),
        send=sdk.SendSchedulerSettings(),
        flow=sdk.FlowControlSettings(),
        transport=transport,
    )
    state.runtime = runtime
    state.requests = runtime.requests
    endpoint = bridge.activate(state, epoch="wheel")
    sequence = 0

    async def receive(message: dict) -> None:
        nonlocal sequence
        sequence += 1
        for frame in fragment(message, f"a.wheel.{sequence}", "up"):
            await endpoint.receive(frame, source="MCBEWS_BRIDGE")

    try:
        metadata = {"vendor": {"nested": [None, "玩家😀"]}}
        call = asyncio.create_task(endpoint.request("echo", {"n": 1}, metadata=metadata))
        request = await asyncio.wait_for(outbound.get(), 3)
        await receive(
            {
                "v": "MCBEWS/2",
                "kind": "response",
                "id": request["id"],
                "result": request["params"],
                "meta": request["meta"],
            }
        )
        response = await asyncio.wait_for(call, 3)
        assert response["result"] == {"n": 1} and response["meta"] == metadata
        async with await endpoint.stream("items", None) as stream:
            opening = await asyncio.wait_for(outbound.get(), 3)
            assert opening["kind"] == "stream_open"
            await receive(
                {
                    "v": "MCBEWS/2",
                    "kind": "stream_item",
                    "id": opening["id"],
                    "seq": 0,
                    "item": "首段😀" * 400,
                }
            )
            assert await asyncio.wait_for(anext(stream), 3) == "首段😀" * 400
            await receive({"v": "MCBEWS/2", "kind": "stream_end", "id": opening["id"], "seq": 1})
            assert [item async for item in stream] == []
        async with await endpoint.stream("cancel", None) as stream:
            opening = await asyncio.wait_for(outbound.get(), 3)
            await stream.aclose()
            cancelled = await asyncio.wait_for(outbound.get(), 3)
            assert cancelled["kind"] == "stream_cancel" and cancelled["id"] == opening["id"]
    finally:
        await bridge.close_connection(state.id)
        await runtime.close()


def main() -> None:
    assert sdk.__version__ == "0.2.1"
    probe_assets()
    asyncio.run(probe_runtime())
    print(f"installed v1/v2 assets, codecs, request and stream contracts: ok ({sdk.__file__})")


if __name__ == "__main__":
    main()
