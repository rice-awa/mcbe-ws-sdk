"""Minimal structlog logger factory (no main-repo import)."""

from typing import cast

import structlog


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Return a structlog BoundLogger for the given name."""
    return cast(structlog.BoundLogger, structlog.get_logger(name))
