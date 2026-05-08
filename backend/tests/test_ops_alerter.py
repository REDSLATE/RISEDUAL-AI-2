"""Tests for services.ops_alerter — the wedge detector.

Covers:
- is_configured reads env at call time (so tests can flip).
- _partition_dedup correctly buckets fresh vs suppressed.
- "All gauges nominal." is filtered both ways and never alerts.
- First run with no state never resolves anything (no false
  resolved-message at startup).
- New note → posted, history updated.
- Same note next tick → suppressed (dedup).
- Same note past dedup window → re-posted.
- Note disappears → resolved-message posted, history entry cleared.
- Webhook failure does not raise; state still persists.
- Webhook unconfigured → no posts but state still persists.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import ops_alerter as alerter


# ── Fake DB ───────────────────────────────────────────────────────


class _FakeColl:
    def __init__(self):
        self.docs = {}

    async def find_one(self, flt, projection=None):
        for d in self.docs.values():
            if all(d.get(k) == v for k, v in flt.items()):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    async def update_one(self, flt, update, upsert=False):
        key = flt.get("_id")
        existing = self.docs.get(key, {**flt})
        existing.update(update.get("$set", {}))
        self.docs[key] = existing


class _FakeDB:
    def __init__(self):
        self._coll = _FakeColl()

    def __getitem__(self, key):
        assert key == "ops_alerter_state"
        return self._coll


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def webhook_on(monkeypatch):
    monkeypatch.setenv("OPS_ALERT_WEBHOOK_URL", "https://hooks.example.com/test")


@pytest.fixture
def webhook_off(monkeypatch):
    monkeypatch.delenv("OPS_ALERT_WEBHOOK_URL", raising=False)


# ── is_configured ─────────────────────────────────────────────────


def test_is_configured_reads_env_live(monkeypatch):
    monkeypatch.delenv("OPS_ALERT_WEBHOOK_URL", raising=False)
    assert alerter.is_configured() is False
    monkeypatch.setenv("OPS_ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    assert alerter.is_configured() is True


def test_is_configured_treats_whitespace_as_unconfigured(monkeypatch):
    monkeypatch.setenv("OPS_ALERT_WEBHOOK_URL", "   ")
    assert alerter.is_configured() is False


# ── _partition_dedup pure helper ──────────────────────────────────


def test_partition_dedup_fresh_when_no_history():
    fresh, supp = alerter._partition_dedup(
        ["A", "B"], {}, now=datetime.now(timezone.utc), dedup_hours=4,
    )
    assert fresh == ["A", "B"]
    assert supp == []


def test_partition_dedup_suppresses_recent():
    now = datetime.now(timezone.utc)
    history = {"A": (now - timedelta(hours=1)).isoformat()}
    fresh, supp = alerter._partition_dedup(
        ["A", "B"], history, now=now, dedup_hours=4,
    )
    assert fresh == ["B"]
    assert supp == ["A"]


def test_partition_dedup_re_alerts_after_window():
    now = datetime.now(timezone.utc)
    history = {"A": (now - timedelta(hours=5)).isoformat()}
    fresh, _supp = alerter._partition_dedup(
        ["A"], history, now=now, dedup_hours=4,
    )
    assert fresh == ["A"]


def test_partition_dedup_tolerates_naive_history_strings():
    """Older versions wrote naive datetimes; must not crash."""
    now = datetime.now(timezone.utc)
    history = {"A": (now - timedelta(hours=1))
               .replace(tzinfo=None).isoformat()}
    fresh, supp = alerter._partition_dedup(
        ["A"], history, now=now, dedup_hours=4,
    )
    assert fresh == [] and supp == ["A"]


def test_partition_dedup_unparseable_history_falls_through():
    """Garbage timestamp → treat as if never alerted, fire fresh."""
    now = datetime.now(timezone.utc)
    history = {"A": "not-a-date"}
    fresh, _supp = alerter._partition_dedup(
        ["A"], history, now=now, dedup_hours=4,
    )
    assert fresh == ["A"]


# ── End-to-end run_tick ───────────────────────────────────────────


def _patched_snapshot(notes):
    """Build a fake collect_ops_snapshot return that yields the
    given notes list. Returned as an AsyncMock side_effect so the
    monkeypatched coroutine resolves to a real dict."""
    return AsyncMock(return_value={
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": list(notes),
    })


@pytest.mark.asyncio
async def test_first_run_no_alerts_when_already_nominal(fake_db, webhook_on):
    """Boot a fresh instance with everything green — must NOT
    post anything (no false-positive 'resolved' message)."""
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot(["All gauges nominal."])):
        post = AsyncMock(return_value=True)
        with patch.object(alerter, "_post_webhook", post):
            out = await alerter.run_tick(fake_db)
    assert out["posted_alert"] is False
    assert out["posted_resolved"] is False
    assert out["fresh_alerts"] == []
    assert out["resolved_alerts"] == []
    post.assert_not_called()


@pytest.mark.asyncio
async def test_new_note_triggers_post(fake_db, webhook_on):
    note = "Mongo ping failed — backend cannot persist trades."
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot([note])):
        post = AsyncMock(return_value=True)
        with patch.object(alerter, "_post_webhook", post):
            out = await alerter.run_tick(fake_db)
    assert out["posted_alert"] is True
    assert out["fresh_alerts"] == [note]
    post.assert_awaited_once()
    # State persisted with the alert in history.
    state = await fake_db._coll.find_one({"_id": "state"})
    assert note in (state.get("alert_history") or {})
    assert note in (state.get("last_notes") or [])


@pytest.mark.asyncio
async def test_same_note_next_tick_is_suppressed(fake_db, webhook_on):
    note = "USPTO_API_KEY not set — Patent Watch refreshes will short-circuit."
    post = AsyncMock(return_value=True)
    with patch.object(alerter, "collect_ops_snapshot", new=_patched_snapshot([note])):
        with patch.object(alerter, "_post_webhook", post):
            await alerter.run_tick(fake_db)  # first time → post
    with patch.object(alerter, "collect_ops_snapshot", new=_patched_snapshot([note])):
        with patch.object(alerter, "_post_webhook", post):
            out = await alerter.run_tick(fake_db)  # second tick same note
    # Second tick: note didn't transition (still in last_notes), so
    # no fresh alert and no suppression — it just doesn't fire.
    assert out["fresh_alerts"] == []
    assert out["suppressed_dedup"] == []
    assert post.await_count == 1  # only the first call posted


@pytest.mark.asyncio
async def test_note_disappears_posts_resolved(fake_db, webhook_on):
    note = "Scheduler appears stalled (last signal 3.0h ago)."
    post = AsyncMock(return_value=True)
    # First tick: note present → posts alert.
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot([note])):
        with patch.object(alerter, "_post_webhook", post):
            await alerter.run_tick(fake_db)
    # Second tick: note gone → posts resolved.
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot(["All gauges nominal."])):
        with patch.object(alerter, "_post_webhook", post):
            out = await alerter.run_tick(fake_db)
    assert out["posted_resolved"] is True
    assert out["resolved_alerts"] == [note]
    # Resolved → history entry cleared so re-occurrence will fire.
    state = await fake_db._coll.find_one({"_id": "state"})
    assert note not in (state.get("alert_history") or {})


@pytest.mark.asyncio
async def test_webhook_unconfigured_no_post_but_state_persists(fake_db, webhook_off):
    note = "USPTO_API_KEY not set."
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot([note])):
        post = AsyncMock(return_value=True)
        with patch.object(alerter, "_post_webhook", post):
            out = await alerter.run_tick(fake_db)
    assert out["configured"] is False
    assert out["posted_alert"] is False
    post.assert_not_called()
    # State still saved so when the webhook later goes live, the
    # alerter doesn't suddenly fire on every old note.
    state = await fake_db._coll.find_one({"_id": "state"})
    assert note in (state.get("last_notes") or [])


@pytest.mark.asyncio
async def test_webhook_failure_does_not_raise_state_still_saves(fake_db, webhook_on):
    """A 500 from the webhook must not crash the scheduler. State
    advances either way so we don't loop on the same condition."""
    note = "Mongo ping failed."
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot([note])):
        post = AsyncMock(return_value=False)
        with patch.object(alerter, "_post_webhook", post):
            out = await alerter.run_tick(fake_db)
    assert out["posted_alert"] is False  # webhook said no
    state = await fake_db._coll.find_one({"_id": "state"})
    assert note in (state.get("last_notes") or [])
    # When the post fails, history is NOT updated — that way the
    # next tick will retry instead of being silently dedup'd.
    assert note not in (state.get("alert_history") or {})


@pytest.mark.asyncio
async def test_nominal_sentinel_filtered_both_ways(fake_db, webhook_on):
    """The 'All gauges nominal.' string must never appear in
    fresh_alerts or resolved_alerts under any state transition."""
    post = AsyncMock(return_value=True)
    # Tick 1: nominal.
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot(["All gauges nominal."])):
        with patch.object(alerter, "_post_webhook", post):
            await alerter.run_tick(fake_db)
    # Tick 2: still nominal — no churn whatsoever.
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot(["All gauges nominal."])):
        with patch.object(alerter, "_post_webhook", post):
            out2 = await alerter.run_tick(fake_db)
    assert out2["fresh_alerts"] == []
    assert out2["resolved_alerts"] == []
    post.assert_not_called()


@pytest.mark.asyncio
async def test_db_unavailable_does_not_crash(webhook_on):
    """run_tick(None) must not raise — alerter should report
    no-state and skip persistence."""
    with patch.object(alerter, "collect_ops_snapshot",
                      new=_patched_snapshot(["Mongo ping failed."])):
        post = AsyncMock(return_value=True)
        with patch.object(alerter, "_post_webhook", post):
            out = await alerter.run_tick(None)
    # Without a DB the snapshot will already say Mongo failed; that's
    # a real condition we want surfaced.
    assert isinstance(out["fresh_alerts"], list)
