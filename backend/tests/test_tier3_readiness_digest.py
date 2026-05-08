"""Tests for Tier 3 readiness daily digest.

Core coverage:
  * Subject formatter handles unlocked, up-delta, down-delta, flat,
    and no-prior cases.
  * Body HTML embeds the correct score, blocker count, and per-gate
    numbers.
  * Blocker chip enricher turns bare reason strings into numbers the
    owner can read at a glance.
  * Idempotent record: re-running in the same UTC day upserts the
    same row rather than creating duplicates.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.tier3_readiness_digest import (
    _format_blocker_chip,
    _format_body_html,
    _format_subject,
    _utc_date_str,
)


# ── _utc_date_str ─────────────────────────────────────────────────────────────

def test_utc_date_str_format():
    assert _utc_date_str(datetime(2026, 2, 20, 3, 15, tzinfo=timezone.utc)) == "2026-02-20"


def test_utc_date_str_defaults_to_now():
    # Should not raise and should return a YYYY-MM-DD string.
    s = _utc_date_str()
    assert len(s) == 10
    assert s.count("-") == 2


# ── _format_blocker_chip ──────────────────────────────────────────────────────

def test_blocker_chip_live_days():
    stats = {"days": 8, "total_trades": 82, "high_conf_trades": 5}
    assert _format_blocker_chip("Insufficient live days", stats) == "live days 8/30"


def test_blocker_chip_trades():
    stats = {"days": 8, "total_trades": 77, "high_conf_trades": 5}
    assert _format_blocker_chip("Insufficient trade count", stats) == "trades 77/100"


def test_blocker_chip_high_conf():
    stats = {"days": 8, "total_trades": 77, "high_conf_trades": 3}
    assert _format_blocker_chip(
        "Not enough high-confidence samples", stats
    ) == "high-conf samples 3/30"


def test_blocker_chip_other_reason_passthrough():
    assert _format_blocker_chip("Too many strong misses", {}) == "Too many strong misses"


# ── _format_subject ───────────────────────────────────────────────────────────

def test_subject_unlocked_emphasises_unlock():
    subject = _format_subject(95.5, +2.0, unlocked=True)
    assert "UNLOCKED" in subject
    assert "95.5/100" in subject


def test_subject_up_delta_has_triangle_up():
    subject = _format_subject(83.2, +1.9, unlocked=False)
    assert "83.2" in subject
    assert "▲" in subject
    assert "+1.9" in subject


def test_subject_down_delta_has_triangle_down():
    subject = _format_subject(78.1, -3.2, unlocked=False)
    assert "▼" in subject
    assert "-3.2" in subject


def test_subject_flat_delta_no_arrow_noise():
    subject = _format_subject(81.3, 0.0, unlocked=False)
    assert "▲" not in subject
    assert "▼" not in subject


def test_subject_no_prior_skips_delta():
    subject = _format_subject(81.3, None, unlocked=False)
    assert "▲" not in subject
    assert "▼" not in subject
    assert "81.3" in subject


# ── _format_body_html ─────────────────────────────────────────────────────────

def _sample_payload():
    stats = {
        "days": 7,
        "total_trades": 82,
        "high_conf_trades": 5,
        "high_conf_win_rate": 1.0,
        "avg_confidence": 70.8,
        "strong_miss_rate": 0.032,
        "overall_win_rate": 0.816,
        "last_7d_win_rate": 1.0,
        "clamp_total": 0,
    }
    unlock = {
        "unlocked": False,
        "reasons": [
            "Insufficient live days",
            "Insufficient trade count",
            "Not enough high-confidence samples",
        ],
        "confidence_score": 81.33,
    }
    return stats, unlock


def test_body_renders_score_and_blocker_chips():
    stats, unlock = _sample_payload()
    html = _format_body_html(81.33, +1.9, stats, unlock)
    # Score + blocker count surface
    assert "81.3" in html
    assert "Blockers (3)" in html
    # Enriched chips — sample-size blockers must be numeric
    assert "live days 7/30" in html
    assert "trades 82/100" in html
    assert "high-conf samples 5/30" in html
    # Delta badge is green for up-delta
    assert "▲" in html
    # Per-gate rows
    assert "Strong-miss rate" in html
    assert "Clamp canary" in html


def test_body_renders_unlocked_badge_when_cleared():
    stats, unlock = _sample_payload()
    unlock = {**unlock, "unlocked": True, "reasons": [], "confidence_score": 100.0}
    html = _format_body_html(100.0, +1.2, stats, unlock)
    assert "ALL 6 GATES CLEAR" in html
    assert "Blockers (0)" in html
    assert "all 6 gates clear" in html


def test_body_handles_no_prior_snapshot():
    stats, unlock = _sample_payload()
    html = _format_body_html(81.33, None, stats, unlock)
    assert "first snapshot" in html


def test_body_down_delta_shown_with_amber():
    stats, unlock = _sample_payload()
    html = _format_body_html(70.0, -5.3, stats, unlock)
    assert "▼" in html
    assert "-5.3" in html


# ── Async DB path — idempotent upsert ─────────────────────────────────────────

class _FakeHistoryCollection:
    """Minimal `update_one`/`find`/`find_one` stub that preserves
    the upsert semantics we rely on."""

    def __init__(self):
        self.rows: list[dict] = []

    async def update_one(self, query, update, upsert=False):
        _set = update.get("$set", {})
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                row.update(_set)
                return
        if upsert:
            self.rows.append(dict(_set))

    async def find_one(self, query, projection=None):  # noqa: ARG002
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                return row
        return None

    def find(self, query, projection=None):  # noqa: ARG002
        ne_date = (query or {}).get("date", {}).get("$ne")
        matching = [r for r in self.rows if r.get("date") != ne_date]
        return _FakeCursor(matching)


class _FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, key, direction):
        reverse = direction == -1
        self.rows.sort(key=lambda r: r.get(key) or "", reverse=reverse)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, length=None):
        return list(self.rows if length is None else self.rows[:length])


class _FakeDB:
    def __init__(self):
        self._coll = _FakeHistoryCollection()

    def __getitem__(self, name):
        assert name == "tier3_readiness_history"
        return self._coll


@pytest.mark.asyncio
async def test_record_snapshot_is_idempotent():
    from services.tier3_readiness_digest import _record_snapshot
    db = _FakeDB()
    await _record_snapshot(db, "2026-02-20", 81.3, {"days": 7}, {"reasons": []})
    await _record_snapshot(db, "2026-02-20", 82.5, {"days": 8}, {"reasons": []})
    # Second call overwrites, does NOT append.
    assert len(db._coll.rows) == 1
    assert db._coll.rows[0]["score"] == 82.5
    assert db._coll.rows[0]["stats"]["days"] == 8


@pytest.mark.asyncio
async def test_latest_prior_returns_yesterday():
    from services.tier3_readiness_digest import (
        _latest_prior_snapshot,
        _record_snapshot,
    )
    db = _FakeDB()
    await _record_snapshot(db, "2026-02-18", 79.0, {}, {})
    await _record_snapshot(db, "2026-02-19", 80.0, {}, {})
    await _record_snapshot(db, "2026-02-20", 81.0, {}, {})
    prior = await _latest_prior_snapshot(db, "2026-02-20")
    assert prior is not None
    assert prior["date"] == "2026-02-19"
    assert prior["score"] == 80.0


@pytest.mark.asyncio
async def test_latest_prior_returns_none_when_empty():
    from services.tier3_readiness_digest import _latest_prior_snapshot
    db = _FakeDB()
    assert await _latest_prior_snapshot(db, "2026-02-20") is None
