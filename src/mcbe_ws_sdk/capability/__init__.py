"""Inbound addon capability registry seam.

Override point for servicing inbound ``scriptevent mcbeai:bridge_request``
calls. Implement :class:`CapabilityHandler` and register per capability name with
a :class:`CapabilityRegistry`; the registry's :meth:`~CapabilityRegistry.handle`
returns the dict that becomes the matching
:class:`~mcbe_ws_sdk.protocol.addon.AddonBridgeResponse` ``payload`` (shipping
is the host's responsibility). An unconfigured capability falls back to
:class:`LoggingStubHandler`, which warns and returns a safe error payload.
"""

from mcbe_ws_sdk.capability.registry import (
    CapabilityContext,
    CapabilityHandler,
    CapabilityRegistry,
    LoggingStubHandler,
)

__all__ = [
    "CapabilityContext",
    "CapabilityHandler",
    "CapabilityRegistry",
    "LoggingStubHandler",
]
