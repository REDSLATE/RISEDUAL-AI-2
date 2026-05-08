"""
Tests for ``services.ticker_abandonment_history``.

Pin:
1. ``compute_action_delta`` covers all 4 spec'd transitions
   (improved / degraded / unchanged / new).
2. Action ordering matches the operator's spec
   (ABANDON < COOLDOWN < KEEP).
3. ``write_snapshot`` upserts on (date, lane, symbol) — same-day
   re-run does not duplicate.
4. ``fetch_prior_action`` returns the most recent snapshot strictly
   before today, even if the daily snapshot job missed a day.
5. ``snapshot_today`` discovers the same union of symbols the bulk
   endpoint does (paper_trades + crypto_paper_trades + agent_activity
   skip events) and writes one row per (lane, symbol).
6. Re-running ``snapshot_today`` on the same day is idempotent —
   no duplicate (date, lane, symbol) rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.ticker_abandonment_history import (
    ACTION_RANK, COLLECTION, compute_action_delta,
)


# ── In-memory fakes (same shape used elsewhere) ───────────────────


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []

    def _matches(self, doc, query):
        for k, v in query.items():
            if isinstance(v, dict):
                if "$gte" in v and (doc.get(k) is None or doc[k] < v["$gte"]):
                    return False
                if "$lt" in v and (doc.get(k) is None or doc[k] >= v["$lt"]):
                    return False
                if "$in" in v and doc.get(k) not in v["$in"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    async def find_one(self, query, projection=None, sort=None):
        rows = [d for d in self._docs if self._matches(d, query)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda d: d.get(key) or "", reverse=(direction == -1))
        if not rows:
            return None
        return dict(rows[0])

    async def update_one(self, query, update, upsert=False):
        for d in self._docs:
            if self._matches(d, query):
                if "$set" in update:
                    d.update(update["$set"])
                return
        if upsert and "$set" in update:
            self._docs.append(dict(update["$set"]))

    async def insert_one(self, doc):
        self._docs.append(dict(doc))

    async def distinct(self, field, query):
        out = []
        seen = set()
        for d in self._docs:
            if not self._matches(d, query):
                continue
            v = d.get(field)
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]


@pytest.fixture
def db():
    return _FakeDB()


# ── compute_action_delta ──────────────────────────────────────────


def test_action_rank_matches_spec():
    """ABANDON=0, COOLDOWN=1, KEEP=2 — operator-pinned ordering."""
    assert ACTION_RANK["ABANDON"] == 0
    assert ACTION_RANK["COOLDOWN"] == 1
    assert ACTION_RANK["KEEP"] == 2


def test_delta_new_when_no_prior():
    out = compute_action_delta("KEEP", None)
    assert out["delta"] == "new"
    assert out["arrow"] == "•"
    assert out["prior_action"] is None


def test_delta_improved_keep_from_cooldown():
    out = compute_action_delta("KEEP", "COOLDOWN")
    assert out == {"delta": "improved", "arrow": "↑", "prior_action": "COOLDOWN"}


def test_delta_improved_keep_from_abandon():
    out = compute_action_delta("KEEP", "ABANDON")
    assert out == {"delta": "improved", "arrow": "↑", "prior_action": "ABANDON"}


def test_delta_improved_cooldown_from_abandon():
    out = compute_action_delta("COOLDOWN", "ABANDON")
    assert out == {"delta": "improved", "arrow": "↑", "prior_action": "ABANDON"}


def test_delta_degraded_keep_to_cooldown():
    out = compute_action_delta("COOLDOWN", "KEEP")
    assert out == {"delta": "degraded", "arrow": "↓", "prior_action": "KEEP"}


def test_delta_degraded_keep_to_abandon():
    out = compute_action_delta("ABANDON", "KEEP")
    assert out == {"delta": "degraded", "arrow": "↓", "prior_action": "KEEP"}


def test_delta_degraded_cooldown_to_abandon():
    out = compute_action_delta("ABANDON", "COOLDOWN")
    assert out == {"delta": "degraded", "arrow": "↓", "prior_action": "COOLDOWN"}


def test_delta_unchanged_same_action():
    for action in ("KEEP", "COOLDOWN", "ABANDON"):
        out = compute_action_delta(action, action)
        assert out == {"delta": "unchanged", "arrow": "→", "prior_action": action}


def test_delta_unknown_action_falls_to_unchanged():
    """Defensive — never throw on an unexpected action string."""
    out = compute_action_delta("WTF_NEW_ACTION", "KEEP")
    # Unknown is rank=-1, KEEP is rank=2 → degraded.
    assert out["delta"] == "degraded"
    out2 = compute_action_delta("KEEP", "WTF_PRIOR")
    assert out2["delta"] == "improved"


# ── write_snapshot / fetch_prior_action ───────────────────────────


@pytest.mark.asyncio
async def test_write_snapshot_idempotent_on_same_day(db):
    from services.ticker_abandonment_history import (
        fetch_prior_action, write_snapshot,
    )
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    inputs = {"recent_signals": 5, "recent_rejections": 1}

    await write_snapshot(
        db, lane="equity", symbol="AAPL", action="KEEP",
        reason="ok", cooldown_minutes=0, inputs_view=inputs, now=now,
    )
    await write_snapshot(
        db, lane="equity", symbol="AAPL", action="KEEP",
        reason="ok", cooldown_minutes=0, inputs_view=inputs, now=now,
    )
    rows = db[COLLECTION]._docs
    assert len([r for r in rows if r["symbol"] == "AAPL"]) == 1


@pytest.mark.asyncio
async def test_fetch_prior_action_returns_most_recent_before_today(db):
    from services.ticker_abandonment_history import (
        fetch_prior_action, write_snapshot,
    )
    today = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)

    await write_snapshot(
        db, lane="equity", symbol="AAPL", action="ABANDON",
        reason="x", cooldown_minutes=1440, inputs_view={}, now=two_days_ago,
    )
    await write_snapshot(
        db, lane="equity", symbol="AAPL", action="COOLDOWN",
        reason="y", cooldown_minutes=240, inputs_view={}, now=yesterday,
    )
    # Today's snapshot should NOT be returned by fetch_prior_action.
    await write_snapshot(
        db, lane="equity", symbol="AAPL", action="KEEP",
        reason="now", cooldown_minutes=0, inputs_view={}, now=today,
    )

    prior = await fetch_prior_action(db, lane="equity", symbol="AAPL", now=today)
    assert prior == "COOLDOWN"


@pytest.mark.asyncio
async def test_fetch_prior_action_falls_back_when_yesterday_missing(db):
    """If the snapshot job missed yesterday, prior should still
    return the most recent snapshot we DO have."""
    from services.ticker_abandonment_history import (
        fetch_prior_action, write_snapshot,
    )
    today = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    five_days_ago = today - timedelta(days=5)
    await write_snapshot(
        db, lane="equity", symbol="AAPL", action="ABANDON",
        reason="x", cooldown_minutes=1440, inputs_view={}, now=five_days_ago,
    )
    prior = await fetch_prior_action(db, lane="equity", symbol="AAPL", now=today)
    assert prior == "ABANDON"


@pytest.mark.asyncio
async def test_fetch_prior_returns_none_for_unknown_symbol(db):
    from services.ticker_abandonment_history import fetch_prior_action
    out = await fetch_prior_action(db, lane="equity", symbol="UNKNOWN")
    assert out is None


# ── snapshot_today integration ────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_today_walks_discovered_symbols(db):
    """Seed paper_trades + crypto_paper_trades + agent_activity and
    confirm snapshot_today writes one row per (lane, symbol)."""
    from services.ticker_abandonment_history import snapshot_today
    from datetime import datetime as _dt

    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    recent = now - timedelta(days=2)

    db["paper_trades"]._docs.append({
        "ticker": "AAPL", "opened_at": recent, "outcome": "loss",
        "pnl_pct": -0.01, "r_multiple": -0.5,
    })
    db["crypto_paper_trades"]._docs.append({
        "symbol": "BTC", "opened_at": recent, "outcome": "win",
        "pnl_pct": 0.02, "r_multiple": 1.5, "closed_at": recent,
    })
    db["agent_activity"]._docs.append({
        "type": "paper_trade_skip", "symbol": "TSLA",
        "created_at": recent, "metadata": {"confidence": 0.45},
    })

    counts = await snapshot_today(db, now=now)
    # All 3 symbols should map to KEEP (insufficient signals each).
    assert counts["keep"] >= 3
    assert counts["abandon"] == 0
    assert counts["errors"] == 0

    rows = db[COLLECTION]._docs
    assert len(rows) == 3
    syms = {r["symbol"] for r in rows}
    assert syms == {"AAPL", "BTC", "TSLA"}


@pytest.mark.asyncio
async def test_snapshot_today_idempotent(db):
    from services.ticker_abandonment_history import snapshot_today
    now = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    recent = now - timedelta(days=2)
    db["paper_trades"]._docs.append({
        "ticker": "AAPL", "opened_at": recent, "outcome": "loss",
    })

    await snapshot_today(db, now=now)
    await snapshot_today(db, now=now)
    rows = db[COLLECTION]._docs
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_snapshot_today_handles_none_db():
    from services.ticker_abandonment_history import snapshot_today
    out = await snapshot_today(None)
    assert out == {"abandon": 0, "cooldown": 0, "keep": 0, "errors": 0}
