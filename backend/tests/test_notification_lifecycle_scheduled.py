"""Smoke test: APScheduler-triggered notification lifecycle sweep stamps
``trigger=scheduled_cron`` on the receipt row.

Pinned because the receipt's ``trigger`` field is the operator's audit
breadcrumb for "no stale alerts? when did we last check, and what
fired the check?".
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.notification_lifecycle import supersede_stale_alerts


class _FakeNotificationsCollection:
    def find(self, *args, **kwargs):  # noqa: D401
        cursor = MagicMock()
        cursor.__aiter__ = lambda self: _aiter([])
        return cursor

    async def update_one(self, *args, **kwargs):
        return MagicMock(modified_count=0)


def _aiter(items):
    async def gen():
        for item in items:
            yield item
    return gen().__aiter__()


@pytest.mark.asyncio
async def test_scheduled_sweep_stamps_trigger_field():
    """The APScheduler job calls ``supersede_stale_alerts(db,
    trigger='scheduled_cron')``. Receipt row must carry that exact
    string so the operator can distinguish scheduled runs from manual
    admin / backfill / one-shot triggers."""
    db = MagicMock()
    db.notifications = _FakeNotificationsCollection()
    db.predictions = MagicMock()
    db.predictions.count_documents = AsyncMock(return_value=0)

    receipt_inserts: list[dict] = []
    db.notification_lifecycle_runs = MagicMock()

    async def _capture(doc):
        receipt_inserts.append(doc)
        return MagicMock(inserted_id="fake")

    db.notification_lifecycle_runs.insert_one = _capture

    # Mirror what the scheduler job does in server.py.
    summary = await supersede_stale_alerts(db, trigger="scheduled_cron")

    assert "totals" in summary
    assert "by_type" in summary
    assert len(receipt_inserts) == 1, "exactly one receipt row per dispatcher call"
    assert receipt_inserts[0]["trigger"] == "scheduled_cron"


@pytest.mark.asyncio
async def test_default_trigger_is_unknown():
    """Sanity check: callers that forget to pass a trigger get the
    fallback string. Receipt is still written so the receipt trail is
    never silently skipped."""
    db = MagicMock()
    db.notifications = _FakeNotificationsCollection()
    db.predictions = MagicMock()
    db.predictions.count_documents = AsyncMock(return_value=0)

    receipt_inserts: list[dict] = []
    db.notification_lifecycle_runs = MagicMock()

    async def _capture(doc):
        receipt_inserts.append(doc)
        return MagicMock(inserted_id="fake")

    db.notification_lifecycle_runs.insert_one = _capture

    await supersede_stale_alerts(db)

    assert receipt_inserts[0]["trigger"] == "unknown"
