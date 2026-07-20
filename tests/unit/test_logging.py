"""Tests for mcbe_ws_sdk.logging.configure_logging."""

from __future__ import annotations

import logging

import pytest
import structlog

from mcbe_ws_sdk.logging import configure_logging


def test_configure_logging_is_compact_without_level_padding(capsys: pytest.CaptureFixture[str]) -> None:
    """Console lines should look like ``[info] event key=value`` with no padding."""
    configure_logging("INFO", colors=False)
    logger = structlog.get_logger("test.logging")
    logger.info("sample_event", host="0.0.0.0", port=8081)

    # Force handlers to flush so capture sees the line.
    for handler in logging.getLogger().handlers:
        handler.flush()

    out = capsys.readouterr().out
    assert "[info]" in out
    assert "[info " not in out  # no pad_level blanks after the level name
    assert "sample_event" in out
    assert "host=0.0.0.0" in out
    assert "port=8081" in out
    # pad_event_to=0 should leave no long run of spaces after the event name
    assert "sample_event                          " not in out


def test_configure_logging_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        configure_logging("NOPE")
