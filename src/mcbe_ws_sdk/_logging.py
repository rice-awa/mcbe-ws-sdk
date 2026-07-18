"""Minimal structlog logger factory (no main-repo import)."""

import structlog


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Return a structlog BoundLogger for the given name."""
    return structlog.get_logger(name)
