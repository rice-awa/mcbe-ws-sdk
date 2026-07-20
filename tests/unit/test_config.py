import pytest

from mcbe_ws_sdk.config import (
    AddonBridgeSettings,
    FlowControlSettings,
    GatewaySettings,
    WebsocketTransportConfig,
)
from mcbe_ws_sdk.errors import ConfigurationError
from mcbe_ws_sdk.profiles.mcbews_v1.profile import McbewsV1Profile


def test_flow_control_default_byte_budget_is_461():
    s = FlowControlSettings()
    assert s.command_line_byte_budget == 461


def test_flow_control_frozen():
    s = FlowControlSettings()
    import pytest

    with pytest.raises(AttributeError):
        s.command_line_byte_budget = 500  # type: ignore[misc]


def test_gateway_settings_default_nested():
    g = GatewaySettings()
    assert g.flow.command_line_byte_budget == 461
    assert g.addon.timeout_seconds == 5.0
    assert isinstance(g.addon.profile, McbewsV1Profile)


def test_websocket_transport_defaults():
    t = WebsocketTransportConfig()
    assert t.host == "0.0.0.0"
    assert t.port == 8080
    assert t.ping_interval == 30.0
    assert t.ping_timeout == 15.0
    assert t.close_timeout == 15.0
    assert t.max_size == 10 * 1024 * 1024
    assert t.max_queue == 32


def test_websocket_transport_frozen():
    t = WebsocketTransportConfig()
    with pytest.raises(AttributeError):
        t.port = 9090  # type: ignore[misc]


def test_gateway_settings_includes_websocket_transport():
    g = GatewaySettings()
    assert isinstance(g.websocket, WebsocketTransportConfig)
    assert g.websocket.port == 8080


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_websocket_transport_rejects_invalid_port(port: int):
    with pytest.raises(ConfigurationError, match="websocket.port"):
        WebsocketTransportConfig(port=port)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", 0),
        ("buffer_ttl_seconds", 0),
        ("max_pending_requests", 0),
        ("max_buffer_ids", 0),
        ("max_chunks_per_message", 0),
        ("max_message_bytes", 0),
        ("max_total_buffer_bytes", 0),
    ],
)
def test_addon_bridge_settings_reject_non_positive_limits(field: str, value: int):
    with pytest.raises(ConfigurationError, match=f"addon.{field}"):
        AddonBridgeSettings(**{field: value})  # type: ignore[arg-type]


def test_flow_control_rejects_invalid_budget_and_delay():
    with pytest.raises(ConfigurationError, match="flow.command_line_byte_budget"):
        FlowControlSettings(command_line_byte_budget=0)
    with pytest.raises(ConfigurationError, match="flow.chunk_delays.tellraw"):
        FlowControlSettings(chunk_delays={"tellraw": -0.1})


@pytest.mark.parametrize("field", ["max_size", "max_queue"])
@pytest.mark.parametrize("value", [0, -1])
def test_websocket_transport_rejects_non_positive_maximums(field: str, value: int):
    with pytest.raises(ConfigurationError, match=f"websocket.{field}"):
        WebsocketTransportConfig(**{field: value})  # type: ignore[arg-type]


def test_websocket_transport_allows_unbounded_maximums():
    settings = WebsocketTransportConfig(max_size=None, max_queue=None)
    assert settings.max_size is None
    assert settings.max_queue is None


def test_flow_control_rejects_zero_maximum_chunk_content_length():
    with pytest.raises(ConfigurationError, match="flow.max_chunk_content_length"):
        FlowControlSettings(max_chunk_content_length=0)


def test_flow_control_copies_and_freezes_chunk_delays():
    delays = {"tellraw": 0.1}
    settings = FlowControlSettings(chunk_delays=delays)
    delays["tellraw"] = 2.0
    assert settings.chunk_delays["tellraw"] == 0.1
    with pytest.raises(TypeError):
        settings.chunk_delays["tellraw"] = 3.0  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", float("nan")),
        ("buffer_ttl_seconds", None),
        ("timeout_seconds", "five"),
        ("max_pending_requests", 1.5),
        ("max_buffer_ids", True),
        ("max_chunks_per_message", "64"),
        ("max_message_bytes", None),
        ("max_total_buffer_bytes", 1.0),
    ],
)
def test_addon_bridge_settings_reject_invalid_runtime_value_types(field: str, value: object):
    with pytest.raises(ConfigurationError, match=f"addon.{field}"):
        AddonBridgeSettings(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_line_byte_budget", float("nan")),
        ("command_line_byte_budget", None),
        ("max_chunk_content_length", "400"),
        ("max_chunk_content_length", 1.5),
    ],
)
def test_flow_control_rejects_invalid_runtime_limit_types(field: str, value: object):
    with pytest.raises(ConfigurationError, match=f"flow.{field}"):
        FlowControlSettings(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("delay", [float("nan"), None, "slow"])
def test_flow_control_rejects_invalid_delay_values(delay: object):
    with pytest.raises(ConfigurationError, match="flow.chunk_delays.tellraw"):
        FlowControlSettings(chunk_delays={"tellraw": delay})  # type: ignore[dict-item]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("port", float("nan")),
        ("port", None),
        ("port", "8080"),
        ("port", True),
        ("ping_interval", float("nan")),
        ("ping_timeout", "15"),
        ("close_timeout", None),
        ("max_size", 1.5),
        ("max_queue", True),
    ],
)
def test_websocket_transport_rejects_invalid_runtime_value_types(field: str, value: object):
    with pytest.raises(ConfigurationError, match=f"websocket.{field}"):
        WebsocketTransportConfig(**{field: value})  # type: ignore[arg-type]
