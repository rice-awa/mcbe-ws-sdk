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
        "MCBEWS_V1",
        "McbeOutboundDelivery",
        "McbeServerFacade",
        "McbeWsSdkError",
        "McbewsV1Delivery",
        "McbewsV1Profile",
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
        "configure_logging",
        "encode_text_response_commands",
        "enqueue_response",
    }
