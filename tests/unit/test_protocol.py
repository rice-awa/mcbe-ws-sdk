"""Tests for the relocated MCBE protocol models."""
from mcbe_ws_sdk.protocol.addon import (
    AddonBridgeChunk,
    AddonBridgeResponse,
    UiChatChunk,
    UiChatMessage,
)
from mcbe_ws_sdk.protocol.minecraft import (
    MCColor,
    MCPrefix,
    MinecraftCommand,
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    MinecraftMessage,
    MinecraftSubscribe,
    PlayerMessageEvent,
)


def test_minecraft_subscribe_player_message():
    sub = MinecraftSubscribe.player_message()
    assert sub.header.messagePurpose == "subscribe"
    # The PlayerMessage event name is carried on the subscribe body, not the header.
    assert sub.body.eventName == "PlayerMessage"


def test_mc_color_primary_is_green_section():
    assert MCColor.GREEN == "§a"
    assert MCColor.YELLOW == "§e"
    assert MCColor.RED == "§c"


def test_minecraft_header_event_name_optional():
    # header.eventName is the lower-cased event name slot; defaults to None.
    sub = MinecraftSubscribe.player_message()
    assert sub.header.eventName is None


def test_mc_prefix_public_members():
    # MCPrefix only exposes public constant members.
    assert MCPrefix.TOOL_CALL == "● "
    assert MCPrefix.THINKING == "✻ "
    assert MCPrefix.ERROR == "✖ "
    assert MCPrefix.SUCCESS == "✓ "


def test_player_message_event_from_event_body():
    body = {"sender": "Steve", "message": "Hi", "type": "chat", "receiver": "Alex"}
    ev = PlayerMessageEvent.from_event_body(body)
    assert ev.sender == "Steve"
    assert ev.message == "Hi"
    assert ev.type == "chat"
    assert ev.receiver == "Alex"


def test_player_message_event_model_validate():
    data = {"sender": "Steve", "message": "Hi", "type": "chat"}
    ev = PlayerMessageEvent.model_validate(data)
    assert ev.sender == "Steve"
    assert ev.message == "Hi"
    assert ev.type == "chat"


def test_addon_bridge_chunk_construction():
    chunk = AddonBridgeChunk(
        request_id="abc-123",
        chunk_index=0,
        total_chunks=2,
        content="hello",
    )
    assert chunk.request_id == "abc-123"
    assert chunk.chunk_index == 0
    assert chunk.total_chunks == 2
    assert chunk.content == "hello"


def test_addon_bridge_response_construction():
    resp = AddonBridgeResponse(request_id="abc", payload={"ok": True})
    assert resp.request_id == "abc"
    assert resp.payload == {"ok": True}


def test_ui_chat_chunk_construction():
    chunk = UiChatChunk(msg_id="m1", chunk_index=1, total_chunks=3, content="world")
    assert chunk.msg_id == "m1"
    assert chunk.chunk_index == 1
    assert chunk.total_chunks == 3
    assert chunk.content == "world"


def test_ui_chat_message_construction():
    msg = UiChatMessage(msg_id="m1", player_name="Steve", message="hello world")
    assert msg.msg_id == "m1"
    assert msg.player_name == "Steve"
    assert msg.message == "hello world"


def test_minecraft_command_create_tellraw_builds_valid_command():
    cmd = MinecraftCommand.create_tellraw("Hello", color="§a", target="@a")
    assert cmd.header.messagePurpose == "commandRequest"
    data = cmd.model_dump()
    assert data["body"]["commandLine"].startswith("tellraw ")
    assert "§aHello" in data["body"]["commandLine"]


def test_wire_models_preserve_unknown_header_and_body_fields() -> None:
    frame = MinecraftMessage.model_validate(
        {
            "header": {
                "messagePurpose": "event",
                "requestId": "r-extra",
                "futureHeader": {"x": 1},
            },
            "body": {"eventName": "FutureEvent", "futureBody": [1, 2]},
            "futureEnvelope": True,
        }
    )
    dumped = frame.model_dump()
    assert dumped["header"]["futureHeader"] == {"x": 1}
    assert dumped["body"]["futureBody"] == [1, 2]
    assert dumped["futureEnvelope"] is True


def test_command_response_and_error_frame_models_allow_extension_fields() -> None:
    response = MinecraftCommandResponse.model_validate(
        {
            "request_id": "r-1",
            "header": {"messagePurpose": "commandResponse", "futureHeader": {"x": 1}},
            "body": {"statusCode": 0, "details": {"count": 2}},
            "futureResponse": True,
        }
    )
    error = MinecraftErrorFrame.model_validate(
        {
            "request_id": "r-2",
            "header": {"messagePurpose": "error", "futureHeader": "ok"},
            "body": {"statusCode": 500, "futureBody": ["x"]},
            "futureError": {"present": True},
        }
    )

    assert response.model_dump()["futureResponse"] is True
    assert response.model_dump()["header"]["futureHeader"] == {"x": 1}
    assert response.body["details"] == {"count": 2}
    dumped_error = error.model_dump()
    assert dumped_error["header"]["futureHeader"] == "ok"
    assert dumped_error["body"]["futureBody"] == ["x"]
    assert dumped_error["futureError"] == {"present": True}
