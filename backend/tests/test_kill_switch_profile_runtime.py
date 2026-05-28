"""Tests — Kill-Switch Profile runtime helpers (2026-02-26, P2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.kill_switch_profile_runtime import (
    _session_start_utc,
    check_session_halt,
    compute_session_stats,
)


# ── Fake Mongo collections used by the runtime ────────────────────


class _FakeAggregateCursor:
    def __init__(self, items):
        self._items = items

    async def to_list(self, length=None):
        return list(self._items)


class _FakeFindCursor:
    def __init__(self, items):
        self._items = items

    def sort(self, field, direction):
        reverse = (direction == -1)
        self._items = sorted(
            self._items, key=lambda r: r.get(field), reverse=reverse,
        )
        return self

    def limit(self, n):
        self._items = self._items[:n]
        return self

    def __aiter__(self):
        async def gen():
            for it in self._items:
                yield it
        return gen()


class _TradesCollection:
    """In-memory closed-trade store with the minimum surface the
    runtime expects."""

    def __init__(self, rows):
        self._rows = rows

    def aggregate(self, pipe):
        match = {}
        for s in pipe:
            if "$match" in s:
                match = s["$match"]
                break

        def _ok(r):
            for k, v in match.items():
                if isinstance(v, dict):
                    if "$gte" in v and not (r.get(k) is not None and r.get(k) >= v["$gte"]):
                        return False
                    if "$exists" in v and ((k in r) != v["$exists"]):
                        return False
                    if "$ne" in v and r.get(k) == v["$ne"]:
                        return False
                    if "$in" in v and r.get(k) not in v["$in"]:
                        return False
                else:
                    if r.get(k) != v:
                        return False
            return True

        matched = [r for r in self._rows if _ok(r)]
        total = sum(float(r["pnl_usd"]) for r in matched)
        n = len(matched)
        return _FakeAggregateCursor(
            [{"_id": None, "total": total, "n": n}] if matched else []
        )

    def find(self, flt, projection=None):
        def _ok(r):
            for k, v in flt.items():
                if isinstance(v, dict):
                    if "$gte" in v and not (r.get(k) is not None and r.get(k) >= v["$gte"]):
                        return False
                    if "$in" in v and r.get(k) not in v["$in"]:
                        return False
                else:
                    if r.get(k) != v:
                        return False
            return True

        return _FakeFindCursor([r for r in self._rows if _ok(r)])

    async def find_one(self, flt, projection=None):
        for r in self._rows:
            if all(r.get(k) == v for k, v in flt.items() if not isinstance(v, dict)):
                return r
        return None


class _ConfigCollection:
    def __init__(self, docs=None):
        self._docs = docs or {}

    async def find_one(self, flt, projection=None):
        key = flt.get("_id")
        return self._docs.get(key)

    async def update_one(self, flt, update, upsert=False):
        key = flt.get("_id")
        new = update.get("$set", {})
        self._docs[key] = {**(self._docs.get(key) or {}), **new}

        class _R:
            modified_count = 1
            matched_count = 1
            upserted_id = key
        return _R()

    async def delete_one(self, flt):
        key = flt.get("_id")
        existed = self._docs.pop(key, None) is not None

        class _R:
            deleted_count = 1 if existed else 0
        return _R()


class _FakeDB:
    def __init__(self, *, trades=None, config=None):
        self._trades = _TradesCollection(trades or [])
        self._config = _ConfigCollection(config or {})

    def __getitem__(self, name):
        if name == "kill_switch_profile_global":
            return self._config
        return self._trades


# ── Session boundary ─────────────────────────────────────────────


def test_session_start_returns_utc_midnight():
    now = datetime(2026, 5, 27, 14, 30, tzinfo=timezone.utc)
    start = _session_start_utc(now)
    assert start == datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)


# ── Session stats ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_stats_sums_today_pnl_and_counts_consecutive_losses():
    now = datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)
    # 3 closes today: -$40, -$30, -$25 (most recent → 3 consec losses)
    today = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
    rows = [
        {"status": "closed", "closed_at": today.replace(hour=10),
         "pnl_usd": -40.0, "outcome": "loss"},
        {"status": "closed", "closed_at": today.replace(hour=11),
         "pnl_usd": -30.0, "outcome": "loss"},
        {"status": "closed", "closed_at": today.replace(hour=15),
         "pnl_usd": -25.0, "outcome": "loss"},
        # Yesterday — must be excluded entirely
        {"status": "closed",
         "closed_at": datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc),
         "pnl_usd": -500.0, "outcome": "loss"},
    ]
    db = _FakeDB(trades=rows)
    stats = await compute_session_stats(
        db, asset_type="equity", starting_equity_usd=1000.0, now=now,
    )
    assert stats["realized_pnl_usd_today"] == -95.0
    assert stats["consecutive_losses_today"] == 3
    assert stats["closes_today"] == 3
    assert stats["starting_equity_usd"] == 1000.0


@pytest.mark.asyncio
async def test_consecutive_losses_stops_at_first_win():
    now = datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)
    today = datetime(2026, 5, 27, tzinfo=timezone.utc)
    # Closed-at order desc: loss, loss, win, loss → streak = 2 (stops at win)
    rows = [
        {"status": "closed", "closed_at": today.replace(hour=8),
         "pnl_usd": -10.0, "outcome": "loss"},
        {"status": "closed", "closed_at": today.replace(hour=10),
         "pnl_usd": +50.0, "outcome": "win"},
        {"status": "closed", "closed_at": today.replace(hour=12),
         "pnl_usd": -15.0, "outcome": "loss"},
        {"status": "closed", "closed_at": today.replace(hour=14),
         "pnl_usd": -20.0, "outcome": "loss"},
    ]
    db = _FakeDB(trades=rows)
    stats = await compute_session_stats(
        db, asset_type="equity", starting_equity_usd=1000.0, now=now,
    )
    assert stats["consecutive_losses_today"] == 2
    assert stats["realized_pnl_usd_today"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_session_stats_empty_when_no_trades():
    db = _FakeDB(trades=[])
    stats = await compute_session_stats(
        db, asset_type="equity", starting_equity_usd=1000.0,
    )
    assert stats["realized_pnl_usd_today"] == 0.0
    assert stats["consecutive_losses_today"] == 0
    assert stats["closes_today"] == 0


# ── Live halt gate ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_session_halt_no_config_is_noop():
    db = _FakeDB(trades=[], config={})
    assert await check_session_halt(db, "equity") is None


@pytest.mark.asyncio
async def test_check_session_halt_disabled_config_is_noop():
    db = _FakeDB(trades=[], config={
        "equity:current": {
            "asset_type": "equity",
            "profile_key": "small_account_warrior",
            "starting_equity_usd": 1000.0,
            "enabled": False,
        },
    })
    assert await check_session_halt(db, "equity") is None


@pytest.mark.asyncio
async def test_check_session_halt_fires_warrior_three_loser_rule():
    now = datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)
    today = datetime(2026, 5, 27, tzinfo=timezone.utc)
    rows = [
        {"status": "closed", "closed_at": today.replace(hour=10),
         "pnl_usd": -5.0, "outcome": "loss"},
        {"status": "closed", "closed_at": today.replace(hour=11),
         "pnl_usd": -5.0, "outcome": "loss"},
        {"status": "closed", "closed_at": today.replace(hour=12),
         "pnl_usd": -5.0, "outcome": "loss"},
    ]
    db = _FakeDB(
        trades=rows,
        config={
            "equity:current": {
                "asset_type": "equity",
                "profile_key": "small_account_warrior",
                "starting_equity_usd": 1000.0,
                "enabled": True,
            },
        },
    )
    # Force "now" by patching the runtime's session-start view via
    # direct call to compute_session_stats: pin via env? simpler —
    # the rows are within "today" relative to wallclock UTC; if the
    # test runs on a different day the fixture day-shifts. To keep
    # things robust, shift rows to today by computing them relative
    # to wallclock.
    real_now = datetime.now(timezone.utc)
    today_real = datetime(real_now.year, real_now.month, real_now.day, tzinfo=timezone.utc)
    db._trades = _TradesCollection([
        {"status": "closed", "closed_at": today_real.replace(hour=10),
         "pnl_usd": -5.0, "outcome": "loss"},
        {"status": "closed", "closed_at": today_real.replace(hour=11),
         "pnl_usd": -5.0, "outcome": "loss"},
        {"status": "closed", "closed_at": today_real.replace(hour=12),
         "pnl_usd": -5.0, "outcome": "loss"},
    ])
    ev = await check_session_halt(db, "equity")
    assert ev is not None
    assert ev.halt is True
    rules = {t.rule for t in ev.triggers}
    assert "rule_3_consecutive_losses" in rules
