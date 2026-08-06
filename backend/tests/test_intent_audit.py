"""Iteration 189 — Intent audit observability.

Tests for the ``intent_skip_log`` write-path in
``public_equity_live_executor.maybe_route_live`` and the
``/api/admin/intent-audit`` endpoints. Prior to this iteration the
executor swallowed every gate rejection silently — 100% of scanner
targets ended up ``executor_skipped`` with an empty reason string.

Coverage:
  (1) Each gate return-None writes to intent_skip_log with the
      exact reason tag (live_exec_disabled, confidence_floor,
      non_directional, empty_symbol, symbol_cooldown, chasing_filter,
      not_in_allowlist).
  (2) _log_skip is non-blocking when db=None and when Mongo write
      raises (never propagates the exception).
  (3) GET /summary aggregates by reason; 403 for non-admin.
  (4) GET /events filters by reason and symbol.
  (5) Skip doc shape carries ts/symbol/reason/detail/strategy_id/
      confidence/direction.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import datetime, timezone

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


# ── Fake async Mongo collection / db ────────────────────────────────


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.raise_on_insert = False

    async def insert_one(self, doc):
        if self.raise_on_insert:
            raise RuntimeError("simulated mongo write failure")
        self.docs.append(dict(doc))
        return types.SimpleNamespace(inserted_id="x")

    def find(self, q=None, projection=None):
        return _FakeCursor(self.docs)

    def aggregate(self, pipeline):
        return _FakeCursor([])

    async def find_one(self, *a, **kw):
        return None

    async def count_documents(self, *a, **kw):
        return 0


class _FakeDB:
    def __init__(self):
        self.intent_skip_log = _FakeCollection()
        self.equity_live_trades = _FakeCollection()
        self.broker_connections = _FakeCollection()
        self.strategy_evidence_scores = _FakeCollection()


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def enable_live_exec(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.5")
    monkeypatch.setenv("PUBLIC_LIVE_SYMBOLS", "")
    monkeypatch.setenv("PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT", "4.0")
    monkeypatch.setenv("PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN", "0")
    yield


def _patch_market_pool(monkeypatch, current, prev_close):
    fake_mod = types.ModuleType("services.market_data_pool")

    async def market_quote(symbol):
        return {"price": current} if current is not None else None

    async def market_daily(symbol, outputsize="compact"):
        if prev_close is None:
            return None
        return [{"close": prev_close}, {"close": current or prev_close}]

    fake_mod.market_quote = market_quote
    fake_mod.market_daily = market_daily
    monkeypatch.setitem(sys.modules, "services.market_data_pool", fake_mod)


# ── Section 1: _log_skip primitives ─────────────────────────────────


class TestLogSkipPrimitive:
    def test_no_op_when_db_none(self):
        from services import public_equity_live_executor as pex
        # Must not raise
        asyncio.run(pex._log_skip(
            None, symbol="AAPL", reason="test", intent={"confidence": 0.4}))

    def test_write_failure_is_swallowed(self, fake_db):
        from services import public_equity_live_executor as pex
        fake_db.intent_skip_log.raise_on_insert = True
        # Must not raise even when insert_one blows up
        asyncio.run(pex._log_skip(
            fake_db, symbol="AAPL", reason="boom", intent={"confidence": 0.1}))
        assert fake_db.intent_skip_log.docs == []

    def test_doc_shape_carries_all_fields(self, fake_db):
        from services import public_equity_live_executor as pex
        intent = {
            "confidence": 0.42,
            "direction": "BUY",
            "strategy_id": "sig:v1",
            "scan_id": "scan-123",
            "prediction_id": "pred-999",
            "source_signal": "alpha_scanner",
        }
        asyncio.run(pex._log_skip(
            fake_db, symbol="MRVL", reason="confidence_floor",
            intent=intent, detail={"confidence": 0.42, "floor": 0.65}))
        assert len(fake_db.intent_skip_log.docs) == 1
        d = fake_db.intent_skip_log.docs[0]
        assert d["symbol"] == "MRVL"
        assert d["reason"] == "confidence_floor"
        assert isinstance(d["ts"], datetime)
        assert d["detail"] == {"confidence": 0.42, "floor": 0.65}
        assert d["strategy_id"] == "sig:v1"
        assert d["scan_id"] == "scan-123"
        assert d["prediction_id"] == "pred-999"
        assert d["source_signal"] == "alpha_scanner"
        assert d["confidence"] == 0.42
        assert d["direction"] == "BUY"


# ── Section 2: gate-by-gate skip tagging ────────────────────────────


class TestSkipTagsPerGate:
    def test_live_exec_disabled(self, monkeypatch, fake_db):
        monkeypatch.delenv("RISEDUAL_PUBLIC_LIVE_EXEC", raising=False)
        from services import public_equity_live_executor as pex
        result = asyncio.run(pex.maybe_route_live(
            db=fake_db,
            intent={"symbol": "AAPL", "direction": "BUY", "confidence": 0.9},
        ))
        assert result is None
        docs = fake_db.intent_skip_log.docs
        assert len(docs) == 1
        assert docs[0]["reason"] == "live_exec_disabled"
        assert docs[0]["symbol"] == "AAPL"

    def test_empty_symbol(self, monkeypatch, fake_db, enable_live_exec):
        from services import public_equity_live_executor as pex
        result = asyncio.run(pex.maybe_route_live(
            db=fake_db,
            intent={"symbol": "", "direction": "BUY", "confidence": 0.9},
        ))
        assert result is None
        reasons = [d["reason"] for d in fake_db.intent_skip_log.docs]
        assert "empty_symbol" in reasons

    def test_non_directional(self, monkeypatch, fake_db, enable_live_exec):
        from services import public_equity_live_executor as pex
        result = asyncio.run(pex.maybe_route_live(
            db=fake_db,
            intent={"symbol": "TSLA", "direction": "HOLD", "confidence": 0.9},
        ))
        assert result is None
        doc = fake_db.intent_skip_log.docs[-1]
        assert doc["reason"] == "non_directional"
        assert doc["detail"].get("direction") == "HOLD"

    def test_confidence_floor(self, monkeypatch, fake_db, enable_live_exec):
        monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.65")
        from services import public_equity_live_executor as pex
        result = asyncio.run(pex.maybe_route_live(
            db=fake_db,
            intent={"symbol": "AAPL", "direction": "BUY", "confidence": 0.30},
        ))
        assert result is None
        doc = fake_db.intent_skip_log.docs[-1]
        assert doc["reason"] == "confidence_floor"
        assert doc["detail"]["confidence"] == pytest.approx(0.30)
        assert doc["detail"]["floor"] == pytest.approx(0.65)

    def test_not_in_allowlist(self, monkeypatch, fake_db, enable_live_exec):
        monkeypatch.setenv("PUBLIC_LIVE_SYMBOLS", "SPY,QQQ")
        from services import public_equity_live_executor as pex
        result = asyncio.run(pex.maybe_route_live(
            db=fake_db,
            intent={"symbol": "MRVL", "direction": "BUY", "confidence": 0.9},
        ))
        assert result is None
        doc = fake_db.intent_skip_log.docs[-1]
        assert doc["reason"] == "not_in_allowlist"

    def test_symbol_cooldown(self, monkeypatch, fake_db, enable_live_exec):
        monkeypatch.setenv("PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN", "30")
        from services import public_equity_live_executor as pex

        async def fake_last_fire(db, symbol):
            return datetime.now(timezone.utc)

        monkeypatch.setattr(pex, "_last_symbol_fire_at", fake_last_fire)
        result = asyncio.run(pex.maybe_route_live(
            db=fake_db,
            intent={"symbol": "AAPL", "direction": "BUY", "confidence": 0.9},
        ))
        assert result is None
        doc = fake_db.intent_skip_log.docs[-1]
        assert doc["reason"] == "symbol_cooldown"
        assert "delta_min" in doc["detail"]
        assert doc["detail"]["required_min"] == 30

    def test_chasing_filter(self, monkeypatch, fake_db, enable_live_exec):
        _patch_market_pool(monkeypatch, current=100.0, prev_close=90.0)  # +11%
        from services import public_equity_live_executor as pex
        result = asyncio.run(pex.maybe_route_live(
            db=fake_db,
            intent={"symbol": "MRVL", "direction": "BUY", "confidence": 0.9},
        ))
        assert result is None
        doc = fake_db.intent_skip_log.docs[-1]
        assert doc["reason"] == "chasing_filter"
        assert doc["detail"]["cap_pct"] == pytest.approx(4.0)
        assert doc["detail"]["move_pct"] > 4.0

    def test_no_broker_creds(self, monkeypatch, fake_db, enable_live_exec):
        _patch_market_pool(monkeypatch, current=100.0, prev_close=99.5)  # tiny
        from services import public_equity_live_executor as pex

        async def fake_creds(db):
            return None

        monkeypatch.setattr(pex, "_aresolve_connect_creds", fake_creds)
        result = asyncio.run(pex.maybe_route_live(
            db=fake_db,
            intent={"symbol": "AAPL", "direction": "BUY", "confidence": 0.9},
        ))
        assert result is None
        reasons = [d["reason"] for d in fake_db.intent_skip_log.docs]
        assert "no_broker_creds" in reasons


# ── Section 3: HTTP endpoint tests ──────────────────────────────────


@pytest.fixture(scope="module")
def admin_session():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL not set")
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"admin login failed status={r.status_code}")
    return s


@pytest.fixture(scope="module")
def anon_session():
    return requests.Session()


class TestIntentAuditEndpoints:
    def test_summary_requires_admin(self, anon_session):
        r = anon_session.get(
            f"{BASE_URL}/api/admin/intent-audit/summary?hours=24", timeout=15)
        assert r.status_code in (401, 403), (
            f"expected 401/403, got {r.status_code}: {r.text[:200]}")

    def test_events_requires_admin(self, anon_session):
        r = anon_session.get(
            f"{BASE_URL}/api/admin/intent-audit/events?hours=24", timeout=15)
        assert r.status_code in (401, 403)

    def test_summary_admin_success(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/intent-audit/summary?hours=24", timeout=15)
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert data["window_hours"] == 24
        assert "since" in data
        assert "total_skips" in data
        assert "total_fires" in data
        assert isinstance(data["reasons"], list)
        for row in data["reasons"]:
            assert "reason" in row
            assert "count" in row and isinstance(row["count"], int)
            assert "symbols" in row and isinstance(row["symbols"], list)
            assert len(row["symbols"]) <= 20

    def test_events_admin_success_with_filters(self, admin_session):
        # No filter
        r = admin_session.get(
            f"{BASE_URL}/api/admin/intent-audit/events?hours=24&limit=10",
            timeout=15,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["window_hours"] == 24
        assert isinstance(data["events"], list)
        assert data["count"] == len(data["events"])
        assert len(data["events"]) <= 10
        # Ensure no _id leakage
        for ev in data["events"]:
            assert "_id" not in ev

        # Reason filter
        r2 = admin_session.get(
            f"{BASE_URL}/api/admin/intent-audit/events"
            f"?hours=24&reason=confidence_floor&limit=50",
            timeout=15,
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["filter"]["reason"] == "confidence_floor"
        for ev in d2["events"]:
            assert ev["reason"] == "confidence_floor"

        # Symbol filter (uppercased server-side)
        r3 = admin_session.get(
            f"{BASE_URL}/api/admin/intent-audit/events"
            f"?hours=24&symbol=aapl&limit=50",
            timeout=15,
        )
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["filter"]["symbol"] == "aapl"
        for ev in d3["events"]:
            assert ev["symbol"] == "AAPL"

    def test_summary_hours_validation(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/admin/intent-audit/summary?hours=0", timeout=15)
        assert r.status_code == 422
        r2 = admin_session.get(
            f"{BASE_URL}/api/admin/intent-audit/summary?hours=999", timeout=15)
        assert r2.status_code == 422
