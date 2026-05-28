"""Tests — Resolution loop drift fallback integration (2026-02-26, P0).

These tests exercise the integration between
``sovereign_resolution_loop._resolve_one_horizon`` and the new drift
resolver: when no linked paper_trade exists for a sovereign decision,
the loop must fall through into ``drift_resolve_one`` and bump the
``drift_resolved`` counter rather than just leaving the row in
``skipped_no_trade``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.sovereign_resolution_loop import _resolve_one_horizon


class _DecisionsCollection:
    def __init__(self, rows):
        self._rows = rows
        self.updates: list[dict] = []

    def find(self, flt, projection=None):
        # Implements the tiny filter the loop uses.
        def _match(r):
            for k, v in flt.items():
                if k == "asset_type" and r.get(k) != v:
                    return False
                if isinstance(v, dict) and "$exists" in v:
                    parts = k.split(".")
                    cur = r
                    exists = True
                    for p in parts:
                        if not isinstance(cur, dict) or p not in cur:
                            exists = False
                            break
                        cur = cur[p]
                    if exists != v["$exists"]:
                        return False
                if isinstance(v, dict) and "$lte" in v:
                    if not (r.get(k) is not None and r.get(k) <= v["$lte"]):
                        return False
            return True

        matched = [r for r in self._rows if _match(r)]

        class _Cur:
            def __init__(self, items):
                self._items = items

            def limit(self, n):
                self._items = self._items[:n]
                return self

            async def to_list(self, length=None):
                return list(self._items)
        return _Cur(matched)

    async def update_one(self, flt, update, upsert=False):
        self.updates.append({"filter": flt, "update": update})

        class _R:
            modified_count = 1
            matched_count = 1
        return _R()


class _PaperTradesCollection:
    """Always returns None so we always hit the drift fallback."""

    async def find_one(self, flt, projection=None):
        return None


class _FakeDB:
    def __init__(self, decisions_rows):
        self._dec = _DecisionsCollection(decisions_rows)
        self._pt = _PaperTradesCollection()

    def __getitem__(self, name):
        if name == "sovereign_decisions":
            return self._dec
        return self._pt


class _FakeMarketData:
    def __init__(self, price):
        self._price = price

    async def get_quote(self, symbol):
        return {"price": self._price}

    async def get_crypto_quote(self, symbol):
        return {"price": self._price}


@pytest.mark.asyncio
async def test_unmatched_decision_resolves_via_drift_when_entry_price_present():
    now = datetime.now(timezone.utc)
    decisions = [
        {
            "asset_type": "equity",
            "action": "LONG",
            "symbol": "AAPL",
            "decision_id": "needs-drift-1",
            "created_at": now - timedelta(hours=2),
            "feature_snapshot": {"entry_price": 100.0},
        },
    ]
    db = _FakeDB(decisions)
    md = _FakeMarketData(price=105.0)  # +5% drift → LONG was right

    result = await _resolve_one_horizon(
        db, "60m", asset_type="equity", trade_coll="paper_trades",
        market_data=md,
    )

    assert result["scanned"] == 1
    assert result["resolved"] == 0  # no trade-join path
    assert result["skipped_no_trade"] == 1  # but it WAS counted as no-trade
    assert result["drift_resolved"] == 1  # and then drift-resolved
    assert result["drift_skipped"] == {}


@pytest.mark.asyncio
async def test_legacy_row_without_entry_price_skips_with_stable_reason():
    now = datetime.now(timezone.utc)
    decisions = [
        {
            "asset_type": "equity",
            "action": "HOLD",
            "symbol": "MSFT",
            "decision_id": "legacy-1",
            "created_at": now - timedelta(hours=2),
            "feature_snapshot": {},  # no entry_price
        },
    ]
    db = _FakeDB(decisions)
    md = _FakeMarketData(price=300.0)

    result = await _resolve_one_horizon(
        db, "60m", asset_type="equity", trade_coll="paper_trades",
        market_data=md,
    )

    assert result["drift_resolved"] == 0
    assert result["drift_skipped"].get("no_entry_price") == 1


@pytest.mark.asyncio
async def test_hold_decision_resolves_right_when_market_quiet():
    now = datetime.now(timezone.utc)
    decisions = [
        {
            "asset_type": "equity",
            "action": "HOLD",
            "symbol": "GOOG",
            "decision_id": "quiet-hold-1",
            "created_at": now - timedelta(hours=2),
            "feature_snapshot": {"entry_price": 100.0},
        },
    ]
    db = _FakeDB(decisions)
    md = _FakeMarketData(price=100.10)  # 0.1% drift << 0.5% tol → right

    result = await _resolve_one_horizon(
        db, "60m", asset_type="equity", trade_coll="paper_trades",
        market_data=md,
    )

    assert result["drift_resolved"] == 1
    # The update_one on decisions collection should have been called
    # with was_right=True.
    update = db._dec.updates[-1]["update"]["$set"]
    horizon_payload = update["outcomes.60m"]
    assert horizon_payload["was_right"] is True
