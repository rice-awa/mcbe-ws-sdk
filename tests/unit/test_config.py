from mcbe_ws_sdk.config import FlowControlSettings, GatewaySettings


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
