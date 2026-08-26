"""Tests for the Bot Status endpoint. Regression coverage for the
HIGH defect where the endpoint was reading collection/field names
that no writer populates, making the verdict permanently wrong.

Each test seeds the REAL collection Alpha's writers use, calls the
endpoint's inner logic, and asserts the verdict + sub-tiles.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from routes import admin_bot_status as ab


class _FakeCursor:
    def __init__(self, docs): self._docs = list(docs)
    def sort(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self
    def __aiter__(self):
        self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]; self._i += 1
        return d
    async def to_list(self, _n): return self._docs


class _FakeColl:
    def __init__(self, docs=None):
        self._docs = list(docs or [])
    def find(self, *_a, **_kw): return _FakeCursor(self._docs)
    async def find_one(self, *_a, **_kw):
        return self._docs[0] if self._docs else None
    async def count_documents(self, *_a, **_kw): return len(self._docs)
    def aggregate(self, pipeline):
        # Minimal $match+$group+$sort+$limit support.
        docs = list(self._docs)
        for stage in pipeline:
            if "$group" in stage:
                key = stage["$group"]["_id"].lstrip("$")
                buckets = {}
                for d in docs:
                    k = d.get(key)
                    buckets[k] = buckets.get(k, 0) + 1
                docs = [{"_id": k, "count": v} for k, v in buckets.items()]
            elif "$sort" in stage:
                (fld, direction), = stage["$sort"].items()
                docs.sort(key=lambda d: d.get(fld, 0), reverse=(direction < 0))
            elif "$limit" in stage:
                docs = docs[: stage["$limit"]]
        return _FakeCursor(docs)


class _FakeDB:
    def __init__(self, **colls):
        self._colls = {k: _FakeColl(v) for k, v in colls.items()}
    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


async def _fake_admin(request):
    return {"role": "owner", "email": "admin@risedual.ai"}


@pytest.mark.asyncio
async def test_verdict_ok_when_universe_and_regime_are_healthy(monkeypatch):
    """Real collection names must be used. Regression for HIGH #1."""
    now = datetime.now(timezone.utc)
    db = _FakeDB(
        top_universe=[
            {"symbol": "NVDA", "tier": 1, "updated_at": now},
            {"symbol": "AAPL", "tier": 1, "updated_at": now},
        ],
        alpha_regime_state=[
            {"_id": "current", "label": "momentum_expansion",
             "probability": 0.85, "updated_at": now},
        ],
        alpha_fast_regime_state=[
            {"_id": "current", "label": "session_trend", "updated_at": now},
        ],
        equity_live_trades=[
            {"symbol": "NVDA", "intent_kind": "open_long",
             "notional": 25.0, "opened_at": now - timedelta(minutes=15)},
        ],
        day_trade_scan_log=[
            {"scan_id": "s1", "asset_class": "equity", "started_at": now,
             "total_scanned": 20, "blocked_count": 3,
             "chosen": {"symbol": "NVDA"}},
        ],
    )
    ab.set_db(db)
    monkeypatch.setattr(ab, "_require_admin", _fake_admin)

    result = await ab.bot_status(SimpleNamespace())
    assert result["universe"]["count"] == 2
    assert result["regime"]["slow"]["label"] == "momentum_expansion"
    assert result["regime"]["fast"]["label"] == "session_trend"
    assert result["last_fill"]["symbol"] == "NVDA"
    assert result["last_fill"]["kind"] == "open_long"
    assert result["last_fill"]["minutes_ago"] is not None
    assert len(result["recent_scans"]) == 1
    verdict = result["verdict"]
    # NOT "broken/universe empty" any more.
    assert verdict["severity"] != "broken"
    assert "empty" not in (verdict.get("headline") or "").lower()


@pytest.mark.asyncio
async def test_verdict_chop_regime_arms_mean_reversion_playbook(monkeypatch):
    """Chop regime is no longer a stand-down state.

    Since 2026-02 Alpha has a mean-reversion pattern family that arms
    specifically during chop, so the verdict must NOT be "holding".
    The headline must mention the mean-reversion playbook so the
    operator sees why Alpha will still trade in chop.
    """
    now = datetime.now(timezone.utc)
    db = _FakeDB(
        top_universe=[{"symbol": "NVDA", "updated_at": now}],
        alpha_regime_state=[
            {"_id": "current", "label": "choppy_meanrevert",
             "probability": 1.0, "updated_at": now},
        ],
        alpha_fast_regime_state=[
            {"_id": "current", "label": "session_chop", "updated_at": now},
        ],
    )
    ab.set_db(db)
    monkeypatch.setattr(ab, "_require_admin", _fake_admin)
    result = await ab.bot_status(SimpleNamespace())
    # Severity is warn (no live fill on record) but NOT holding —
    # holding is the old "standing down" state we removed.
    assert result["verdict"]["severity"] != "holding"
    headline = result["verdict"]["headline"].lower()
    details = " ".join(result["verdict"].get("details") or []).lower()
    # Either the headline or a details line must mention mean-reversion
    assert "mean-reversion" in headline or "mean-reversion" in details
    # And the regime label must still be surfaced somewhere
    assert "chop" in headline or "chop" in details


@pytest.mark.asyncio
async def test_verdict_broken_when_universe_is_actually_empty(monkeypatch):
    """Universe-empty verdict must fire only when the REAL collection
    ``top_universe`` is empty."""
    db = _FakeDB(top_universe=[])
    ab.set_db(db)
    monkeypatch.setattr(ab, "_require_admin", _fake_admin)
    result = await ab.bot_status(SimpleNamespace())
    assert result["universe"]["count"] == 0
    assert result["verdict"]["severity"] == "broken"
    assert "empty" in result["verdict"]["headline"].lower()


@pytest.mark.asyncio
async def test_skip_reasons_bucketed_from_intent_skip_log_ts(monkeypatch):
    """Skip reasons come from ``intent_skip_log`` with a ``ts`` field.
    Regression for HIGH #1 sub-defect."""
    now = datetime.now(timezone.utc)
    db = _FakeDB(
        top_universe=[{"symbol": "NVDA", "updated_at": now}],
        intent_skip_log=[
            {"reason": "regime_chop", "ts": now - timedelta(hours=1)},
            {"reason": "regime_chop", "ts": now - timedelta(hours=2)},
            {"reason": "confidence_floor", "ts": now - timedelta(hours=3)},
        ],
    )
    ab.set_db(db)
    monkeypatch.setattr(ab, "_require_admin", _fake_admin)
    result = await ab.bot_status(SimpleNamespace())
    reasons = {r["reason"]: r["count"] for r in result["skip_reasons_24h"]}
    assert reasons.get("regime_chop") == 2
    assert reasons.get("confidence_floor") == 1
