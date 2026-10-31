"""R06 fragment-core and optional-extension boundaries."""

from __future__ import annotations

import ast
import builtins
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from mcbe_ws_sdk.addon.service import AddonBridgeService
from mcbe_ws_sdk.addon.session import AddonBridgeSession
from mcbe_ws_sdk.config import AddonBridgeSettings, FlowControlSettings
from mcbe_ws_sdk.delivery.outbound import McbeOutboundDelivery
from mcbe_ws_sdk.errors import BridgeLimitError, ProtocolError
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.reassembly import ReassemblyCore


def core(**kwargs: Any) -> ReassemblyCore[str]:
    return ReassemblyCore(AddonBridgeSettings(**kwargs), content_of=lambda item: item)


def push(store: ReassemblyCore[str], channel: str, key: str, i: int, n: int, text: str) -> Any:
    return store.accept(channel, buffer_id=key, index=i, total=n, content=text, item=text)


def test_core_opaque_payload_order_duplicates_and_conflicts() -> None:
    store = core()
    buffer = push(store, "custom", "id", 2, 2, "审批")
    push(store, "custom", "id", 2, 2, "审批")
    push(store, "custom", "id", 1, 2, "opaque")
    assert [buffer.chunks[i] for i in (1, 2)] == ["opaque", "审批"]
    assert buffer.byte_size == len("opaque审批".encode())
    with pytest.raises(ProtocolError, match="duplicate"):
        push(store, "custom", "id", 2, 2, "changed")
    assert store._buffer_count() == store._total_buffer_bytes() == 0
    push(store, "custom", "id", 1, 2, "x")
    with pytest.raises(ProtocolError, match="total changed"):
        push(store, "custom", "id", 2, 3, "y")
    assert not store.channel("custom")


def test_core_budget_is_shared_across_channels_and_keys() -> None:
    store = core(max_buffer_ids=2, max_message_bytes=8, max_total_buffer_bytes=4)
    push(store, "a", "same", 1, 2, "中")
    push(store, "b", "same", 1, 2, "x")
    with pytest.raises(BridgeLimitError, match="maximum buffer ids"):
        push(store, "c", "third", 1, 2, "")
    with pytest.raises(BridgeLimitError, match="total buffer byte"):
        push(store, "b", "same", 2, 2, "y")
    assert store._buffer_count() == 1
    assert store._total_buffer_bytes() == 3
    assert store.channel("a")["same"].chunks == {1: "中"}


def test_core_expiry_duplicate_does_not_refresh_lifetime() -> None:
    now = [0.0]
    store = ReassemblyCore[str](
        AddonBridgeSettings(buffer_ttl_seconds=1),
        content_of=lambda item: item,
        clock=lambda: now[0],
    )
    push(store, "a", "id", 1, 2, "x")
    now[0] = 0.9
    push(store, "a", "id", 1, 2, "x")
    now[0] = 1
    assert store.prune_expired() == [("a", "id")]
    assert store._total_buffer_bytes() == 0


@pytest.mark.asyncio
async def test_disabled_extensions_connect_send_native_and_custom_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled paths must not load an extension decoder, even for malformed frames."""
    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("mcbe_ws_sdk.extensions"):
            raise AssertionError(f"optional extension imported: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    service = AddonBridgeService(AddonBridgeSettings(), extensions=False)
    sent: list[dict[str, Any]] = []
    connected: list[UUID] = []
    results: list[dict[str, Any]] = []

    async def send_capability(command: str) -> None:
        body = json.loads(command.split(" ", 2)[2])
        result = await service.handle_player_message(
            connected[0],
            "MCBEWS_BRIDGE",
            f'MCBEWS|BRIDGE|{body["request_id"]}|1/1|{{"custom":true}}',
        )
        assert result.handled

    class Hook(NoOpHook):
        async def on_connected(self, state: Any) -> None:
            connected.append(state.id)
            outbound = McbeOutboundDelivery(
                connection_id=state.id,
                send_payload=state.send_payload,
                settings=FlowControlSettings(),
            )
            await outbound.send_raw_command("say native")
            results.append(
                await service.request_capability(state.id, "custom", {}, send_capability)
            )

    class Transport:
        async def send(self, payload: str) -> None:
            sent.append(json.loads(payload))

        async def __aiter__(self) -> Any:
            if False:
                yield ""

    await McbeServerFacade(addon=service, hook=Hook())._on_connection(Transport())
    assert len(connected) == 1
    assert any(frame.get("body", {}).get("commandLine") == "say native" for frame in sent)
    assert results == [{"custom": True}]
    for prefix in ("MCBEWS|UI_CHAT", "MCBEWS|SESSION", "MCBEWS|TOOL_APPROVE", "MCBEWS|TOOL_DENY"):
        result = await service.handle_player_message(connected[0], "MCBEWS_BRIDGE", prefix + "|bad")
        assert result.handled and result.control_message is None and result.ui_message is None
    with pytest.raises(ProtocolError, match="disabled"):
        AddonBridgeSession(AddonBridgeSettings(), extensions=False).handle_ui_chat_chunk("invalid")


def test_core_and_bridge_have_no_business_extension_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "src/mcbe_ws_sdk/reassembly.py",
        "src/mcbe_ws_sdk/profiles/mcbews_v1/bridge_codec.py",
        "src/mcbe_ws_sdk/profiles/mcbews_v1/bridge_models.py",
    ):
        tree = ast.parse((root / relative).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "extensions" not in (node.module or "")
                assert not (node.module or "").endswith(
                    (".codec", ".models", ".classifier", ".delivery")
                )
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "role",
                    "usage",
                    "approval_id",
                    "action",
                    "session_request",
                }
    for relative in ("src/mcbe_ws_sdk/addon/service.py", "src/mcbe_ws_sdk/addon/session.py"):
        tree = ast.parse((root / relative).read_text())
        assert not any(
            isinstance(node, ast.ImportFrom) and "extensions" in (node.module or "")
            for node in tree.body
        )
    ts_core = (root / "addon/scripts/bridge/reassembly.ts").read_text()
    assert "extensions" not in ts_core
    assert all(field not in ts_core for field in (".role", ".usage", ".approval", ".playerName"))


def test_old_imports_are_extension_identity_aliases() -> None:
    from mcbe_ws_sdk import SessionResponse, TextResponseChunk, UiChatMessage
    from mcbe_ws_sdk.extensions.mcbews_v1 import codec, models
    from mcbe_ws_sdk.profiles.mcbews_v1.codec import encode_session_response_command
    from mcbe_ws_sdk.profiles.mcbews_v1.models import SessionResponse as OldResponse

    assert SessionResponse is OldResponse is models.SessionResponse
    assert TextResponseChunk is models.TextResponseChunk
    assert UiChatMessage is models.UiChatMessage
    assert encode_session_response_command is codec.encode_session_response_command
