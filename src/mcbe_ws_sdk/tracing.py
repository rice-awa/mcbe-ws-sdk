"""Operation context shared across managed tasks without changing wire IDs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from mcbe_ws_sdk.diagnostics import Diagnostics


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Correlation identifiers; a trace may contain several logical requests."""

    connection_id: str | None = None
    player: str | None = None
    request_id: str | None = None
    trace_id: str | None = None


_EMPTY_CONTEXT = TraceContext()
_context: ContextVar[TraceContext] = ContextVar("mcbe_trace", default=_EMPTY_CONTEXT)
_diagnostics: ContextVar[Diagnostics | None] = ContextVar("mcbe_diagnostics", default=None)


def current_trace() -> TraceContext:
    """Return the current immutable operation context."""
    return _context.get()


@contextmanager
def trace_scope(
    *,
    context: TraceContext | None = None,
    connection_id: str | None = None,
    player: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    diagnostics: Diagnostics | None = None,
) -> Iterator[TraceContext]:
    """Inherit context, override supplied identifiers and restore on exit.

    Trace IDs are local metadata and never inserted into MCBEWS/1 packets.
    Explicit contexts restore reply correlation, including an absent player.
    """
    value = context if context is not None else current_trace()
    updates = {
        k: v
        for k, v in {
            "connection_id": connection_id,
            "player": player,
            "request_id": request_id,
            "trace_id": trace_id,
        }.items()
        if v is not None
    }
    value = replace(value, **updates)
    if value.trace_id is None:
        value = replace(value, trace_id=uuid4().hex)
    token = _context.set(value)
    sink_token = _diagnostics.set(diagnostics if diagnostics is not None else _diagnostics.get())
    try:
        yield value
    finally:
        _diagnostics.reset(sink_token)
        _context.reset(token)


def observe(stage: str, **fields: Any) -> None:
    """Submit a body-free lifecycle record to the active diagnostic lane."""
    sink = _diagnostics.get()
    if sink is not None:
        sink.record(stage, **fields)
