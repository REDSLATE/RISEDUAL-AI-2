"""Tests for live feature extraction + Camaro→Shelly bridge."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from services.ml.camaro_shelly_bridge import (
    _build_regime_payload,
    _classify_outcome,
    get_bridge_status,
    ingest_one_lane,
    run_bridge,
)
from services.ml.feature_extraction import extract_live_features


# ── Camaro classify_outcome ──────────────────────────────────────


def test_classify_outcome_positive():
    assert _classify_outcome({"realized_pnl_usd": 12.5}) == "positive"


def test_classify_outcome_negative():
    assert _classify_outcome({"realized_pnl_usd": -3.4}) == "negative"


def test_classify_outcome_neutral_explicit_zero():
    assert _classify_outcome({"realized_pnl_usd": 0.0}) == "neutral"


def test_classify_outcome_falls_back_to_price_delta_buy():
    assert _classify_outcome({"side": "BUY", "entry_price": 100, "close_price": 105}) == "positive"


def test_classify_outcome_falls_back_to_price_delta_sell():
    # SHORT side, entry > close → positive (short profited)
    assert _classify_outcome({"side": "SHORT", "entry_price": 100, "close_price": 95}) == "positive"


def test_classify_outcome_unknown_returns_neutral():
    assert _classify_outcome({}) == "neutral"


# ── Camaro payload shape ─────────────────────────────────────────


def test_camaro_payload_has_source_camaro():
    payload = _build_regime_payload(
        {"trade_id": "abc", "symbol": "AAPL", "side": "BUY",
         "realized_pnl_usd": 5.0,
         "closed_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        lane="equity",
    )
    assert payload["source"] == "camaro"
    assert payload["lane"] == "equity"
    assert payload["symbol"] == "AAPL"
    assert payload["outcome"] == "positive"
    assert payload["prediction_id"].startswith("camaro::abc::")
    assert payload["schema_version"] == 1


def test_camaro_payload_idempotent_id():
    a = _build_regime_payload(
        {"trade_id": "x1", "symbol": "AAPL", "closed_at": "2026-01-01T00:00:00Z"},
        lane="equity",
    )
    b = _build_regime_payload(
        {"trade_id": "x1", "symbol": "AAPL", "closed_at": "2026-01-01T00:00:00Z"},
        lane="equity",
    )
    assert a["prediction_id"] == b["prediction_id"]


# ── ingest_one_lane (stubbed DB) ─────────────────────────────────


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)
    def __aiter__(self):
        return self
    async def __anext__(self):
        if not self._rows:
            raise StopAsyncIteration
        return self._rows.pop(0)
    async def to_list(self, length=None):
        out, self._rows = self._rows, []
        return out


class _StubColl:
    def __init__(self, db, name):
        self.db = db
        self.name = name
    def find(self, *_a, **_kw):
        return _StubCursor(self.db.rows.get(self.name, []))
    async def insert_one(self, doc):
        self.db.audits.setdefault(self.name, []).append(doc)


class _StubCursor:
    def __init__(self, rows):
        self._rows = list(rows)
    def limit(self, _n):
        return self
    def sort(self, *_a, **_kw):
        return self
    async def to_list(self, length=None):
        out, self._rows = self._rows, []
        return out
    def __aiter__(self):
        return _Cursor(self._rows).__aiter__()


class _StubDB:
    def __init__(self, rows: Dict[str, List[Dict[str, Any]]]):
        self.rows = rows
        self.audits: Dict[str, list] = {}
    def __getitem__(self, name):
        return _StubColl(self, name)


@pytest.mark.asyncio
async def test_ingest_one_lane_no_rows(monkeypatch):
    db = _StubDB(rows={"paper_trades": []})
    counts = await ingest_one_lane(
        db, collection="paper_trades", lane="equity", lookback_hours=24,
    )
    assert counts["seen"] == 0
    assert counts["ingested"] == 0


@pytest.mark.asyncio
async def test_ingest_one_lane_persists_audit(monkeypatch):
    rows = [
        {"trade_id": "t1", "symbol": "AAPL", "side": "BUY",
         "source": "day_trade_scanner", "status": "closed",
         "realized_pnl_usd": 4.0,
         "closed_at": datetime.now(timezone.utc).isoformat()},
    ]
    db = _StubDB(rows={"paper_trades": rows})

    # Stub market_memory_service.save_regime so we don't write Chroma.
    saved = []
    async def fake_save_regime(payload):
        saved.append(payload)
    import services.market_memory_service as mms
    monkeypatch.setattr(mms, "save_regime", fake_save_regime, raising=False)

    counts = await ingest_one_lane(
        db, collection="paper_trades", lane="equity", lookback_hours=24,
    )
    assert counts["seen"] == 1
    assert counts["ingested"] == 1
    assert counts["positive"] == 1
    # Audit row landed in camaro_shelly_bridge_log
    assert len(db.audits.get("camaro_shelly_bridge_log", [])) == 1
    assert saved[0]["source"] == "camaro"


# ── Live feature extraction (degrades cleanly) ──────────────────


@pytest.mark.asyncio
async def test_extract_live_features_returns_dict_when_db_none():
    out = await extract_live_features(symbol="AAPL", lane="equity", db=None)
    assert isinstance(out, dict)
    # Even with no live data, we get system_health + pacing defaults.
    assert "broker_uptime" in out
    assert "intraday_progress" in out


@pytest.mark.asyncio
async def test_extract_live_features_base_overrides_win():
    base = {"vix": 99.0, "rel_volume": 5.0}
    out = await extract_live_features(
        symbol="AAPL", lane="equity", db=None, base=base,
    )
    assert out["vix"] == 99.0
    assert out["rel_volume"] == 5.0


@pytest.mark.asyncio
async def test_extract_live_features_crypto_lane_path():
    """Crypto lane path runs without raising even when no quote service."""
    out = await extract_live_features(symbol="BTC-USD", lane="crypto", db=None)
    assert isinstance(out, dict)
    # crypto path doesn't require equity quote
    assert "broker_uptime" in out
