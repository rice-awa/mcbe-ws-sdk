"""Shared request/response lifecycle for the SDK.

MCBEWS/2 capability calls and the native MCBE command client register a pending
request *before* the outbound frame is sent, then resolve it exactly once. A
:class:`RequestTable` is scoped to one connection and partitions entries by
category, so an id reused across connections, categories or directions can
never resolve the wrong waiting call.

The table deliberately keeps the lifecycle small: it owns registration,
once-only terminal state, cancellation and close. It does not encode, send or
interpret payloads.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from mcbe_ws_sdk.errors import (
    RequestClosedError,
    RequestDuplicateError,
    RequestLimitError,
)
from mcbe_ws_sdk.tracing import TraceContext, current_trace

logger = structlog.get_logger(__name__)

# Request categories. MCBEWS/2 and native command requests share one table but
# must never resolve each other, even if both use the same id.
CATEGORY_COMMAND = "command.native"

_DEFAULT_MAX_PENDING = 128


def _consume_future_exception(future: asyncio.Future[Any]) -> None:
    """Mark an unawaited exception as retrieved to avoid GC warnings.

    Awaiting the future still raises the exception; this callback only prevents
    ``Future exception was never retrieved`` noise for abandoned requests.
    """

    if future.cancelled():
        return
    if future.exception() is not None:
        logger.debug("request_future_failed_unawaited")


@dataclass(slots=True)
class PendingRequest:
    """One registered request; terminal state is set at most once."""

    request_id: str
    category: str
    future: asyncio.Future[Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    trace: TraceContext = field(default_factory=current_trace)

    def __post_init__(self) -> None:
        self.future.add_done_callback(_consume_future_exception)

    @property
    def done(self) -> bool:
        return self.future.done()

    def resolve(self, value: Any) -> bool:
        """Complete with ``value``; return ``False`` if already terminal."""
        if self.future.done():
            return False
        self.future.set_result(value)
        return True

    def fail(self, error: BaseException) -> bool:
        """Fail with ``error``; return ``False`` if already terminal."""
        if self.future.done():
            return False
        self.future.set_exception(error)
        return True

    def cancel(self) -> bool:
        """Cancel the future; return ``False`` if already terminal."""
        if self.future.done():
            return False
        self.future.cancel()
        return True


class RequestTable:
    """Per-connection pending-request table partitioned by category."""

    def __init__(
        self,
        *,
        max_pending: int = _DEFAULT_MAX_PENDING,
        clock: Callable[[], float] = time.monotonic,
        name: str = "requests",
    ) -> None:
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError("max_pending must be a positive integer")
        self._max_pending = max_pending
        self._clock = clock
        self._name = name
        self._entries: dict[str, dict[str, PendingRequest]] = {}
        self._count = 0
        self._closed = False

    # -- introspection ------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return self._count

    @property
    def max_pending(self) -> int:
        return self._max_pending

    @property
    def is_closed(self) -> bool:
        return self._closed

    def category_count(self, category: str) -> int:
        return len(self._entries.get(category, {}))

    def entries(self, category: str) -> dict[str, PendingRequest]:
        """Return the live mapping for ``category`` (internal compatibility view)."""
        return self._entries.get(category, {})

    # -- lifecycle ----------------------------------------------------------

    def reserve(
        self,
        category: str,
        *,
        request_id: str,
        future: asyncio.Future[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PendingRequest:
        """Register a pending request before its frame is sent.

        Raises:
            RequestClosedError: The table was closed.
            RequestDuplicateError: ``request_id`` is already pending in
                ``category``. The existing entry is left untouched.
            RequestLimitError: The pending-request limit was reached.
        """
        if self._closed:
            raise RequestClosedError(request_id=request_id, reason="request table is closed")
        bucket = self._entries.get(category)
        if bucket is not None and request_id in bucket:
            raise RequestDuplicateError(request_id, category)
        if self._count >= self._max_pending:
            raise RequestLimitError(
                f"maximum pending requests exceeded ({self._max_pending}) for {self._name}"
            )
        if bucket is None:
            bucket = {}
            self._entries[category] = bucket
        if future is None:
            future = asyncio.get_running_loop().create_future()
        entry = PendingRequest(
            request_id=request_id,
            category=category,
            future=future,
            metadata=metadata if metadata is not None else {},
            created_at=self._clock(),
        )
        bucket[request_id] = entry
        self._count += 1
        return entry

    def get(self, category: str, request_id: str) -> PendingRequest | None:
        return self._entries.get(category, {}).get(request_id)

    def resolve(self, category: str, request_id: str, value: Any) -> bool:
        """Resolve a pending entry once. Unknown/duplicate/late ids return ``False``."""
        entry = self._pop(category, request_id)
        if entry is None:
            return False
        return entry.resolve(value)

    def fail(self, category: str, request_id: str, error: BaseException) -> bool:
        """Fail a pending entry once. Unknown/duplicate/late ids return ``False``."""
        entry = self._pop(category, request_id)
        if entry is None:
            return False
        return entry.fail(error)

    def cancel(self, entry: PendingRequest) -> bool:
        """Cancel ``entry`` and remove it from the table.

        The entry is removed only when it is still the entry registered under
        its id. A stale ``PendingRequest`` whose id was reused after it reached
        a terminal state can therefore never cancel the newer request.
        """
        bucket = self._entries.get(entry.category)
        if bucket is None or bucket.get(entry.request_id) is not entry:
            return False
        if self._pop(entry.category, entry.request_id) is None:
            return False
        return entry.cancel()

    def discard(self, category: str, request_id: str) -> bool:
        """Remove an entry without changing its terminal state (idempotent)."""
        return self._pop(category, request_id) is not None

    def fail_all(
        self,
        error_factory: Callable[[str], BaseException] | None = None,
    ) -> int:
        """Fail every pending entry; return how many were failed."""
        failed = 0
        for category in list(self._entries):
            for request_id in list(self._entries.get(category, {})):
                entry = self._pop(category, request_id)
                if entry is None:
                    continue
                error = (
                    error_factory(request_id)
                    if error_factory is not None
                    else RequestClosedError(request_id)
                )
                if entry.fail(error):
                    failed += 1
        return failed

    def close(
        self,
        error_factory: Callable[[str], BaseException] | None = None,
    ) -> int:
        """Close the table (idempotent) and fail every pending entry."""
        if self._closed:
            return 0
        self._closed = True
        failed = self.fail_all(error_factory)
        if failed:
            logger.info(
                "request_table_closed",
                table=self._name,
                failed=failed,
            )
        return failed

    def _pop(self, category: str, request_id: str) -> PendingRequest | None:
        bucket = self._entries.get(category)
        if not bucket:
            return None
        entry = bucket.pop(request_id, None)
        if entry is None:
            return None
        self._count -= 1
        if not bucket:
            self._entries.pop(category, None)
        return entry


__all__ = [
    "CATEGORY_COMMAND",
    "PendingRequest",
    "RequestTable",
]
