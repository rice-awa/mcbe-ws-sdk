"""R02: shared request lifecycle (registration, once-only terminal state)."""

from __future__ import annotations

import asyncio

import pytest

from mcbe_ws_sdk.errors import (
    RequestClosedError,
    RequestDuplicateError,
    RequestLimitError,
)
from mcbe_ws_sdk.requests import CATEGORY_ADDON, CATEGORY_COMMAND, RequestTable


@pytest.mark.asyncio
async def test_resolve_before_await_is_visible_to_awaiter() -> None:
    """Registration happens before send; an immediate reply must not be lost."""
    table = RequestTable()
    entry = table.reserve(CATEGORY_COMMAND, request_id="cmd-1")

    assert table.resolve(CATEGORY_COMMAND, "cmd-1", "reply") is True

    assert await asyncio.wait_for(entry.future, timeout=1) == "reply"
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_categories_do_not_cross_resolve() -> None:
    table = RequestTable()
    addon = table.reserve(CATEGORY_ADDON, request_id="shared-id")
    command = table.reserve(CATEGORY_COMMAND, request_id="shared-id")

    assert table.resolve(CATEGORY_ADDON, "shared-id", {"ok": True}) is True
    assert addon.future.done()
    assert not command.future.done()

    assert table.resolve(CATEGORY_COMMAND, "shared-id", "done") is True
    assert await command.future == "done"


@pytest.mark.asyncio
async def test_tables_are_connection_scoped() -> None:
    first = RequestTable()
    second = RequestTable()
    first_entry = first.reserve(CATEGORY_COMMAND, request_id="same")
    second_entry = second.reserve(CATEGORY_COMMAND, request_id="same")

    assert first.resolve(CATEGORY_COMMAND, "same", "first") is True
    assert first_entry.future.done()
    assert not second_entry.future.done()

    assert second.resolve(CATEGORY_COMMAND, "same", "second") is True
    assert await second_entry.future == "second"


@pytest.mark.asyncio
async def test_duplicate_unknown_and_late_replies_are_ignored() -> None:
    table = RequestTable()
    entry = table.reserve(CATEGORY_ADDON, request_id="r1")

    assert table.resolve(CATEGORY_ADDON, "r1", "first") is True
    assert await entry.future == "first"

    # Duplicate and late replies must not revive the completed request.
    assert table.resolve(CATEGORY_ADDON, "r1", "again") is False
    assert table.fail(CATEGORY_ADDON, "r1", RuntimeError("late")) is False
    assert table.resolve(CATEGORY_ADDON, "never-registered", "x") is False
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_fail_sets_exception_once() -> None:
    table = RequestTable()
    entry = table.reserve(CATEGORY_COMMAND, request_id="r1")

    assert table.fail(CATEGORY_COMMAND, "r1", LookupError("remote")) is True
    assert table.fail(CATEGORY_COMMAND, "r1", LookupError("again")) is False

    with pytest.raises(LookupError, match="remote"):
        await entry.future
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_cancel_removes_entry_and_cancels_future() -> None:
    table = RequestTable()
    entry = table.reserve(CATEGORY_COMMAND, request_id="r1")

    assert table.cancel(entry) is True
    assert table.cancel(entry) is False
    assert entry.future.cancelled()
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_close_fails_every_pending_entry_once() -> None:
    table = RequestTable()
    addon = table.reserve(CATEGORY_ADDON, request_id="a1")
    command = table.reserve(CATEGORY_COMMAND, request_id="c1")

    failed = table.close(lambda request_id: RequestClosedError(request_id, "gone"))

    assert failed == 2
    assert table.is_closed
    with pytest.raises(RequestClosedError):
        await addon.future
    with pytest.raises(RequestClosedError):
        await command.future
    assert table.close() == 0

    with pytest.raises(RequestClosedError):
        table.reserve(CATEGORY_COMMAND, request_id="late")


@pytest.mark.asyncio
async def test_duplicate_id_is_rejected_without_losing_the_first_entry() -> None:
    """A repeated id never overwrites the pending entry or double-counts it."""
    table = RequestTable()
    first = table.reserve(CATEGORY_ADDON, request_id="same")

    with pytest.raises(RequestDuplicateError) as caught:
        table.reserve(CATEGORY_ADDON, request_id="same")
    assert caught.value.request_id == "same"
    assert caught.value.category == CATEGORY_ADDON

    # Exactly one live entry; closing fails that one and leaves nothing behind.
    assert table.pending_count == 1
    assert table.get(CATEGORY_ADDON, "same") is first
    assert table.close(lambda request_id: RequestClosedError(request_id)) == 1
    with pytest.raises(RequestClosedError):
        await first.future
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_stale_entry_cancel_does_not_remove_reused_id() -> None:
    """Cancelling an old entry must not touch a newer entry with the same id."""
    table = RequestTable()
    old = table.reserve(CATEGORY_COMMAND, request_id="same")
    assert table.resolve(CATEGORY_COMMAND, "same", "done") is True
    await old.future

    new = table.reserve(CATEGORY_COMMAND, request_id="same")
    assert table.cancel(old) is False
    assert table.get(CATEGORY_COMMAND, "same") is new
    assert not new.future.done()

    assert table.cancel(new) is True
    assert new.future.cancelled()
    assert table.pending_count == 0


@pytest.mark.asyncio
async def test_limit_is_enforced_before_registration() -> None:
    table = RequestTable(max_pending=1)
    table.reserve(CATEGORY_ADDON, request_id="a1")

    with pytest.raises(RequestLimitError):
        table.reserve(CATEGORY_ADDON, request_id="a2")
    assert table.pending_count == 1


@pytest.mark.asyncio
async def test_discard_does_not_change_terminal_state() -> None:
    table = RequestTable()
    entry = table.reserve(CATEGORY_COMMAND, request_id="r1")

    assert table.discard(CATEGORY_COMMAND, "r1") is True
    assert table.discard(CATEGORY_COMMAND, "r1") is False
    assert not entry.future.done()
    entry.cancel()


def test_invalid_limit_rejected() -> None:
    with pytest.raises(ValueError):
        RequestTable(max_pending=0)
