"""Tests — Sovereign Drift Resolver (2026-02-26, P0).

Pinned behaviour: HOLD scores by tolerance, LONG/SHORT score by sign,
missing entry_price skips with a stable reason string.
"""
from __future__ import annotations

import pytest

from services.sovereign_drift_resolver import drift_resolve_one, score_drift


# ── Pure verdict ──────────────────────────────────────────────────


def test_long_right_when_drift_positive():
    assert score_drift("LONG", 0.02, "equity") is True


def test_long_wrong_when_drift_negative():
    assert score_drift("LONG", -0.01, "equity") is False


def test_short_right_when_drift_negative():
    assert score_drift("SHORT", -0.015, "equity") is True


def test_short_wrong_when_drift_positive():
    assert score_drift("SHORT", 0.005, "equity") is False


def test_hold_right_inside_equity_tolerance():
    # 0.5% default equity tolerance
    assert score_drift("HOLD", 0.003, "equity") is True
    assert score_drift("HOLD", -0.003, "equity") is True


def test_hold_wrong_outside_equity_tolerance():
    assert score_drift("HOLD", 0.012, "equity") is False
    assert score_drift("HOLD", -0.012, "equity") is False


def test_hold_uses_wider_crypto_tolerance():
    # 1% default crypto tolerance
    assert score_drift("HOLD", 0.008, "crypto") is True
    assert score_drift("HOLD", -0.008, "crypto") is True
    assert score_drift("HOLD", 0.015, "crypto") is False


def test_unknown_action_is_not_resolved():
    assert score_drift("WAFFLE", 0.0, "equity") is False
    assert score_drift("", 0.0, "equity") is False


# ── Async resolver — fake DB + fake market data ───────────────────


class _FakeCollection:
    """Trap update_one calls so the test can assert resolution
    landed in the right shape."""

    def __init__(self):
        self.updates: list[dict] = []

    async def update_one(self, flt, update, upsert=False):
        self.updates.append({"filter": flt, "update": update})

        class _R:
            modified_count = 1
            matched_count = 1
        return _R()


class _FakeDB:
    def __init__(self):
        self._coll = _FakeCollection()

    def __getitem__(self, name):
        # All collection lookups go to the same trap so we can read
        # them off ``_coll.updates`` after the call.
        return self._coll


class _FakeMarketData:
    def __init__(self, equity_price=None, crypto_price=None):
        self._eq = equity_price
        self._cr = crypto_price

    async def get_quote(self, symbol):
        return {"price": self._eq} if self._eq is not None else None

    async def get_crypto_quote(self, symbol):
        return {"price": self._cr} if self._cr is not None else None


@pytest.mark.asyncio
async def test_resolves_long_via_drift():
    db = _FakeDB()
    md = _FakeMarketData(equity_price=110.0)
    decision = {
        "decision_id": "abc-123",
        "symbol": "AAPL",
        "asset_type": "equity",
        "action": "LONG",
        "feature_snapshot": {"entry_price": 100.0},
    }
    receipt = await drift_resolve_one(
        db, decision=decision, horizon="60m", market_data=md,
    )
    assert receipt["status"] == "resolved"
    assert receipt["was_right"] is True
    assert receipt["drift_pct"] == 0.1
    assert receipt["via"] == "market_drift"


@pytest.mark.asyncio
async def test_resolves_hold_as_right_when_market_quiet():
    db = _FakeDB()
    md = _FakeMarketData(equity_price=100.20)
    decision = {
        "decision_id": "xyz-9",
        "symbol": "MSFT",
        "asset_type": "equity",
        "action": "HOLD",
        "feature_snapshot": {"entry_price": 100.0},
    }
    receipt = await drift_resolve_one(
        db, decision=decision, horizon="60m", market_data=md,
    )
    assert receipt["status"] == "resolved"
    assert receipt["was_right"] is True  # 0.2% < 0.5% tolerance


@pytest.mark.asyncio
async def test_resolves_hold_as_wrong_when_market_moved():
    db = _FakeDB()
    md = _FakeMarketData(equity_price=102.5)  # +2.5% drift
    decision = {
        "decision_id": "xyz-10",
        "symbol": "MSFT",
        "asset_type": "equity",
        "action": "HOLD",
        "feature_snapshot": {"entry_price": 100.0},
    }
    receipt = await drift_resolve_one(
        db, decision=decision, horizon="60m", market_data=md,
    )
    assert receipt["status"] == "resolved"
    assert receipt["was_right"] is False


@pytest.mark.asyncio
async def test_skips_when_no_entry_price():
    db = _FakeDB()
    md = _FakeMarketData(equity_price=100.0)
    decision = {
        "decision_id": "legacy-1",
        "symbol": "FOO",
        "asset_type": "equity",
        "action": "LONG",
        "feature_snapshot": {},  # missing entry_price
    }
    receipt = await drift_resolve_one(
        db, decision=decision, horizon="60m", market_data=md,
    )
    assert receipt["status"] == "skipped"
    assert receipt["reason"] == "no_entry_price"


@pytest.mark.asyncio
async def test_skips_when_market_data_unavailable():
    db = _FakeDB()
    md = _FakeMarketData(equity_price=None)
    decision = {
        "decision_id": "noprice-1",
        "symbol": "BAR",
        "asset_type": "equity",
        "action": "LONG",
        "feature_snapshot": {"entry_price": 100.0},
    }
    receipt = await drift_resolve_one(
        db, decision=decision, horizon="60m", market_data=md,
    )
    assert receipt["status"] == "skipped"
    assert receipt["reason"] == "no_current_price"


@pytest.mark.asyncio
async def test_crypto_path_uses_crypto_quote_method():
    db = _FakeDB()
    md = _FakeMarketData(crypto_price=50000.0)
    decision = {
        "decision_id": "btc-1",
        "symbol": "BTC",
        "asset_type": "crypto",
        "action": "SHORT",
        "feature_snapshot": {"entry_price": 51000.0},
    }
    receipt = await drift_resolve_one(
        db, decision=decision, horizon="60m", market_data=md,
    )
    assert receipt["status"] == "resolved"
    assert receipt["was_right"] is True  # SHORT + negative drift = right
