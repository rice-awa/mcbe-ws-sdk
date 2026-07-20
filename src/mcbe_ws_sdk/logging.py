"""Optional console logging helpers for hosts and examples.

The SDK itself only *emits* structured events via ``structlog.get_logger``.
Hosts that want a readable console should call :func:`configure_logging` once
at process start. Defaults match the compact style used by the parent MCBE AI
Agent host: no level/event column padding, local timestamps, key=value fields.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


def configure_logging(
    level: LogLevel | str = "INFO",
    *,
    colors: bool | None = None,
) -> None:
    """Configure structlog + stdlib logging for compact console output.

    Parameters
    ----------
    level:
        Root log level name (default ``INFO``).
    colors:
        Force colour on/off. ``None`` (default) enables colour only when stdout
        is a TTY.
    """
    level_name = str(level).upper()
    log_level = getattr(logging, level_name, None)
    if not isinstance(log_level, int):
        raise ValueError(f"unknown log level: {level!r}")

    use_colors = sys.stdout.isatty() if colors is None else colors

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    renderer: structlog.types.Processor
    if use_colors:
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            pad_level=False,
            pad_event_to=0,
        )
    else:
        # Keep the same key=value layout when piping / redirecting; hosts that
        # want JSON can wire their own renderer.
        renderer = structlog.dev.ConsoleRenderer(
            colors=False,
            pad_level=False,
            pad_event_to=0,
        )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    root.addHandler(handler)
