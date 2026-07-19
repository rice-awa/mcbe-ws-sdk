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


class BridgeClosedError(BridgeError):
    """Raised when an add-on bridge is closed."""


class BridgeLimitError(ProtocolError):
    """Raised when an add-on bridge protocol limit is exceeded."""


class FacadeLifecycleError(McbeWsSdkError, RuntimeError):
    """Raised for invalid server facade lifecycle transitions."""
