"""RED: Public API snapshot test.

The snapshot set below is authoritative — every addition or removal of a
top-level public symbol in the SDK must update this single assertion.
"""

import mcbe_ws_sdk


def test_public_api_snapshot() -> None:
    assert set(mcbe_ws_sdk.__all__) == {
        "__version__",
        "AddonBridgeClient",
        "AddonBridgeProfile",
        "AddonBridgeService",
        "AddonBridgeSettings",
        "AddonMessageResult",
        "BridgeClosedError",
        "BridgeError",
        "BridgeLimitError",
        "BridgeTimeoutError",
        "CommandRegistry",
        "ConfigurationError",
        "ConnectionAddonBridgeClient",
        "ConnectionHook",
        "ConnectionManager",
        "ConnectionState",
        "DefaultResponseSink",
        "EventBus",
        "FacadeLifecycleError",
        "FlowControlMiddleware",
        "FlowControlSettings",
        "FrameTooLargeError",
        "GatewaySettings",
        "LEGACY_MCBEAI_V1",
        "LegacyMcbeAiV1Delivery",
        "LegacyMcbeAiV1Profile",
        "McbeOutboundDelivery",
        "McbeServerFacade",
        "McbeWsSdkError",
        "MessageSurfaceConfig",
        "MinecraftCommandResponse",
        "MinecraftErrorFrame",
        "MinecraftProtocolHandler",
        "NoOpHook",
        "OutboundText",
        "PlayerMessageEvent",
        "ProtocolError",
        "ResponseKind",
        "ResponseSink",
        "RouteEnvelope",
        "SubscriptionToken",
        "SystemNotification",
        "WebsocketTransportConfig",
        "WsEventType",
        "encode_legacy_response_commands",
    }
