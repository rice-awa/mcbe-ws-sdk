"""Public exception hierarchy for mcbe-ws-sdk."""


class McbeWsSdkError(Exception):
    """Base class for SDK errors."""


class ConfigurationError(McbeWsSdkError, ValueError):
    """Raised when SDK settings are invalid."""


class ProtocolError(McbeWsSdkError, ValueError):
    """Raised when a protocol contract is violated."""


class FrameTooLargeError(ProtocolError):
    """Raised when a frame cannot fit within its configured byte budget."""


class BridgeError(McbeWsSdkError):
    """Base class for add-on bridge errors."""


class BridgeTimeoutError(BridgeError):
    """Raised when an add-on bridge request times out."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"Bridge request timed out: {request_id}")
        self.request_id = request_id


class BridgeClosedError(BridgeError):
    """Raised when an add-on bridge is closed."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"Bridge connection closed: {request_id}")
        self.request_id = request_id


class BridgeLimitError(BridgeError, ProtocolError):
    """Raised when an add-on bridge protocol limit is exceeded."""


class FacadeLifecycleError(McbeWsSdkError, RuntimeError):
    """Raised for invalid server facade lifecycle transitions."""
