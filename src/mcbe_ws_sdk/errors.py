"""Public exception hierarchy for mcbe-ws-sdk."""


class McbeWsSdkError(Exception):
    """Base class for SDK errors."""


class ConfigurationError(McbeWsSdkError, ValueError):
    """Raised when SDK settings are invalid."""


class ProtocolError(McbeWsSdkError, ValueError):
    """Raised when a protocol contract is violated."""


class FrameTooLargeError(ProtocolError):
    """Raised when a frame cannot fit within its configured byte budget."""


class ReassemblyLimitError(ProtocolError):
    """Raised when chunk reassembly exceeds a configured limit."""


class CapabilityError(McbeWsSdkError):
    """Base class for capability registry errors."""


class CapabilityAlreadyRegisteredError(CapabilityError, ValueError):
    """Raised when a capability name is registered twice without replacement."""

    def __init__(self, name: str) -> None:
        super().__init__(f"capability already registered: {name!r} (pass replace=True to override)")
        self.name = name


class ServerLifecycleError(McbeWsSdkError, RuntimeError):
    """Raised for invalid server lifecycle transitions."""


class RequestError(McbeWsSdkError):
    """Base class for SDK request-lifecycle errors."""


class RequestTimeoutError(RequestError, TimeoutError):
    """Raised when a request does not receive a reply within its timeout.

    The timeout covers only the wait for the remote reply, measured from the
    moment the outbound frame was accepted by the send path. Time spent queued
    for send capacity is reported separately by :class:`SendQueueFullError`.
    """

    def __init__(self, request_id: str, timeout: float | None = None) -> None:
        detail = f" after {timeout:g}s" if timeout is not None else ""
        super().__init__(f"Request timed out{detail}: {request_id}")
        self.request_id = request_id
        self.timeout = timeout


class RequestClosedError(RequestError, RuntimeError):
    """Raised when a request (or its table) is closed before completion."""

    def __init__(self, request_id: str | None = None, reason: str = "connection closed") -> None:
        detail = f" ({request_id})" if request_id else ""
        super().__init__(f"Request closed{detail}: {reason}")
        self.request_id = request_id
        self.reason = reason


class RequestLimitError(RequestError, RuntimeError):
    """Raised when a request table has reached its pending-request limit."""

    def __init__(self, message: str = "maximum pending requests exceeded") -> None:
        super().__init__(message)


class RequestDuplicateError(RequestError, ValueError):
    """Raised when an id is registered twice in the same request category.

    Rejecting the duplicate keeps exactly one future attached to the id: the
    original request stays cancellable and can still be resolved or failed by
    its reply. A later registration may reuse the id only after the first entry
    reached a terminal state.
    """

    def __init__(self, request_id: str, category: str) -> None:
        super().__init__(f"duplicate pending request id {request_id!r} in category {category!r}")
        self.request_id = request_id
        self.category = category


class SendError(McbeWsSdkError):
    """Base class for outbound send-scheduler errors."""


class SendQueueFullError(SendError, RuntimeError):
    """Raised when the outbound send queue cannot admit a frame in time."""


class SendClosedError(SendError, RuntimeError):
    """Raised when sending on a connection whose send path is closed."""

    def __init__(self, reason: str = "connection closed") -> None:
        super().__init__(f"Send path closed: {reason}")
        self.reason = reason


class RemoteCallError(McbeWsSdkError):
    """Raised when the remote side reports failure for a tracked request."""

    def __init__(
        self,
        request_id: str,
        *,
        code: object | None = None,
        message: str = "remote call failed",
    ) -> None:
        code_text = code if isinstance(code, str) and code else "ERROR"
        super().__init__(f"Remote call failed ({request_id}): [{code_text}] {message}")
        self.request_id = request_id
        self.code = code
        self.message = message
