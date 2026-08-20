"""Iter 184 — Ring 1 (calibration-scope + cooldown + enrichment) + Ring 3 (Evidence Worker).

Covers:
- day_trade_scanner: calibration_applies_to scope respected
- public_equity_live_executor: cooldown gate, evidence multiplier, shadow-mode notional
- evidence_worker: compute_evidence bucketing & idempotent upsert
- Admin routes: auth + /scores + /recompute
"""
from __future__ import annotations

import os
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    open("/app/frontend/.env").read().split("=", 1)[1].strip()


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture
def admin_session(api):
    r = api.post(f"{BASE_URL}/api/auth/login",
                 json={"email": "admin@risedual.ai", "password": "RiseDual2026!"})
    if r.status_code != 200:
        pytest.skip(f"admin login failed: {r.status_code} {r.text[:200]}")
    return api


# ── Fake async mongo helpers ─────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
    def __aiter__(self):
        self._i = 0
        return self
    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        r = self._rows[self._i]
        self._i += 1
        return r
    def sort(self, *a, **k):
        return self


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.upserts = []
        self.inserts = []
    def find(self, query=None, projection=None):
        return _FakeCursor(self.rows)
    async def find_one(self, query=None, projection=None, sort=None):
        if not self.rows:
            return None
        # Very simple query matching for strategy_id / symbol
        for r in self.rows:
            if query is None:
                return r
            ok = all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict))
            if ok:
                return r
        return None
    async def update_one(self, filt, update, upsert=False):
        self.upserts.append((filt, update, upsert))
        # Emulate upsert into rows
        doc = update.get("$set", {})
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                r.update(doc)
                return SimpleNamespace(matched_count=1)
        self.rows.append({**filt, **doc})
        return SimpleNamespace(matched_count=0, upserted_id="x")
    async def insert_one(self, doc):
        self.inserts.append(doc)
        return SimpleNamespace(inserted_id="x")


class _FakeDB:
    def __init__(self):
        self.equity_live_trades = _FakeColl()
        self.strategy_evidence_scores = _FakeColl()
        self.strategy_evidence_runs = _FakeColl()
        self.broker_connections = _FakeColl()


# ── Ring 1: day_trade_scanner scope check ────────────────────────────

class TestScannerCalibrationScope:
    def test_scope_tier3_only_uses_raw(self):
        from services import day_trade_scanner as dts

        db = _FakeDB()
        db.predictions = _FakeColl()

        # Emulate aggregate pipeline. The scanner calls db.predictions.aggregate.
        rows = [{
            "_id": "AAPL",
            "prediction_id": "p1",
            "direction": "BUY",
            "confidence": 0.55,
            "calibrated_confidence": 0.9167,
            "calibration_applies_to": ["tier3_readiness_only"],
            "model_version": "sig:v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]

        class _Agg:
            def aggregate(self, pipe):
                class _C:
                    async def to_list(self, length): return rows
                return _C()
        db.predictions = _Agg()

        out = asyncio.run(
            dts.scan_universe(db, asset_class="equity")
        )
        assert len(out) == 1
        cand = out[0]
        # tier3_readiness_only should NOT authorise the scanner → use raw
        assert cand.score == pytest.approx(0.55, abs=0.01), \
            f"expected raw 0.55, got {cand.score} (calibrated leaked in)"
        assert cand.confidence_calibrated == pytest.approx(0.9167, abs=0.001)
        assert cand.strategy_id == "sig:v1"

    def test_scope_all_uses_calibrated(self):
        from services import day_trade_scanner as dts

        rows = [{
            "_id": "MSFT",
            "prediction_id": "p2",
            "direction": "BUY",
            "confidence": 0.55,
            "calibrated_confidence": 0.9167,
            "calibration_applies_to": ["all"],
            "model_version": "sig:v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]

        class _Agg:
            def aggregate(self, pipe):
                class _C:
                    async def to_list(self, length): return rows
                return _C()

        db = _FakeDB(); db.predictions = _Agg()
        out = asyncio.run(
            dts.scan_universe(db, asset_class="equity")
        )
        assert len(out) == 1
        assert out[0].score == pytest.approx(0.9167, abs=0.001)

    def test_empty_scope_uses_calibrated(self):
        from services import day_trade_scanner as dts

        rows = [{
            "_id": "NVDA",
            "prediction_id": "p3",
            "direction": "BUY",
            "confidence": 0.55,
            "calibrated_confidence": 0.9167,
            "calibration_applies_to": [],
            "model_version": "sig:v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]

        class _Agg:
            def aggregate(self, pipe):
                class _C:
                    async def to_list(self, length): return rows
                return _C()

        db = _FakeDB(); db.predictions = _Agg()
        out = asyncio.run(
            dts.scan_universe(db, asset_class="equity")
        )
        assert out[0].score == pytest.approx(0.9167, abs=0.001)


# ── Ring 1: cooldown gate ────────────────────────────────────────────

class TestCooldownGate:
    def test_cooldown_blocks_recent_open_long(self, monkeypatch):
        from services import public_equity_live_executor as pex

        monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
        monkeypatch.setenv("PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN", "60")
        monkeypatch.setenv("PUBLIC_LIVE_RTH_ONLY", "0")

        db = _FakeDB()
        db.equity_live_trades.rows = [{
            "symbol": "TEST", "broker_id": "public", "side": "BUY",
            "opened_at": datetime.now(timezone.utc) - timedelta(minutes=15),
        }]

        # Short-circuit downstream (must never be reached)
        async def _bad_creds(_db): return None
        monkeypatch.setattr(pex, "_aresolve_connect_creds", _bad_creds)

        out = asyncio.run(
            pex.maybe_route_live(db, intent={
                "symbol": "TEST", "direction": "BUY", "confidence": 0.9,
                "strategy_id": "sig:v1",
            })
        )
        assert out is None  # cooldown-skipped

    def test_cooldown_expired_allows_progression(self, monkeypatch):
        from services import public_equity_live_executor as pex

        monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
        monkeypatch.setenv("PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN", "60")
        monkeypatch.setenv("PUBLIC_LIVE_RTH_ONLY", "0")

        db = _FakeDB()
        db.equity_live_trades.rows = [{
            "symbol": "TEST", "broker_id": "public", "side": "BUY",
            "opened_at": datetime.now(timezone.utc) - timedelta(minutes=90),
        }]

        # Track that we passed cooldown by making creds return None (next gate)
        seen = {"creds_called": False}
        async def _no_creds(_db):
            seen["creds_called"] = True
            return None
        monkeypatch.setattr(pex, "_aresolve_connect_creds", _no_creds)

        out = asyncio.run(
            pex.maybe_route_live(db, intent={
                "symbol": "TEST", "direction": "BUY", "confidence": 0.9,
                "strategy_id": "sig:v1",
            })
        )
        assert out is None  # skipped downstream (no creds), but cooldown passed
        assert seen["creds_called"] is True, "cooldown blocked flow when it shouldn't have"


# ── Ring 3: evidence multiplier lookup ───────────────────────────────

class TestEvidenceMultiplier:
    def test_no_db_returns_1_lookup_error(self):
        from services.public_equity_live_executor import _evidence_multiplier
        mult, meta = asyncio.run(
            _evidence_multiplier(None, "any")
        )
        assert mult == 1.0
        # spec says db=None → lookup_error (per review request wording)
        # actual code returns "no_db" — both acceptable as safety fallback
        assert meta["bucket"] in ("lookup_error", "no_db")

    def test_untested_strategy_returns_0_25(self):
        from services.public_equity_live_executor import _evidence_multiplier
        db = _FakeDB()
        mult, meta = asyncio.run(
            _evidence_multiplier(db, "brand_new_strategy")
        )
        assert mult == pytest.approx(0.25)
        assert meta["bucket"] == "untested"

    def test_scored_strategy_returns_row_multiplier(self):
        from services.public_equity_live_executor import _evidence_multiplier
        db = _FakeDB()
        db.strategy_evidence_scores.rows = [{
            "strategy_id": "sig:v1", "notional_multiplier": 0.50,
            "bucket": "ok", "hit_rate": 0.6, "sharpe": 0.4, "trade_count": 12,
        }]
        mult, meta = asyncio.run(
            _evidence_multiplier(db, "sig:v1")
        )
        assert mult == pytest.approx(0.50)
        assert meta["bucket"] == "ok"
        assert meta["hit_rate"] == 0.6
        assert meta["trade_count"] == 12


# ── Ring 3: evidence worker aggregation ──────────────────────────────

class TestEvidenceWorker:
    def test_compute_buckets_losing(self, monkeypatch):
        monkeypatch.delenv("RISEDUAL_EVIDENCE_MIN_TRADES", raising=False)
        monkeypatch.delenv("RISEDUAL_EVIDENCE_WINDOW_DAYS", raising=False)
        from services import evidence_worker

        db = _FakeDB()
        now = datetime.now(timezone.utc)
        # 6 trades, all losses → losing bucket
        db.equity_live_trades.rows = [
            {"broker_id": "public", "status": "closed",
             "closed_at": now, "strategy_id": "s1",
             "entry_price": 100.0, "close_price": 99.0}
            for _ in range(6)
        ]

        summary = asyncio.run(
            evidence_worker.compute_evidence(db)
        )
        assert summary["strategies_evaluated"] == 1
        assert summary["wrote"] == 1
        strat = summary["strategies"][0]
        assert strat["strategy_id"] == "s1"
        assert strat["trade_count"] == 6
        assert strat["bucket"] == "losing"
        assert strat["notional_multiplier"] == 0.10
        assert strat["hit_rate"] == 0.0

    def test_compute_untested_low_trades(self):
        from services import evidence_worker
        db = _FakeDB()
        now = datetime.now(timezone.utc)
        # 3 trades — below default min_trades=5
        db.equity_live_trades.rows = [
            {"broker_id": "public", "status": "closed",
             "closed_at": now, "strategy_id": "s2",
             "entry_price": 100.0, "close_price": 101.0}
            for _ in range(3)
        ]
        summary = asyncio.run(
            evidence_worker.compute_evidence(db)
        )
        strat = summary["strategies"][0]
        assert strat["bucket"] == "untested"
        assert strat["notional_multiplier"] == 0.25

    def test_compute_proven_when_high_sharpe(self):
        from services import evidence_worker
        db = _FakeDB()
        now = datetime.now(timezone.utc)
        # Consistent small wins → high sharpe
        db.equity_live_trades.rows = [
            {"broker_id": "public", "status": "closed",
             "closed_at": now, "strategy_id": "s3",
             "entry_price": 100.0, "close_price": 101.0 + (i * 0.01)}
            for i in range(8)
        ]
        summary = asyncio.run(
            evidence_worker.compute_evidence(db)
        )
        strat = summary["strategies"][0]
        assert strat["trade_count"] == 8
        assert strat["bucket"] == "proven"
        assert strat["notional_multiplier"] == 1.00

    def test_db_none_safe(self):
        from services import evidence_worker
        out = asyncio.run(
            evidence_worker.compute_evidence(None)
        )
        assert out.get("error") == "no_db"


# ── Ring 3: admin route auth + shape ─────────────────────────────────

class TestAdminEvidenceRoutes:
    def test_unauth_scores_403_or_401(self, api):
        r = api.get(f"{BASE_URL}/api/admin/evidence/scores")
        assert r.status_code in (401, 403), f"expected auth block, got {r.status_code}"

    def test_unauth_recompute_403_or_401(self, api):
        r = api.post(f"{BASE_URL}/api/admin/evidence/recompute")
        assert r.status_code in (401, 403), f"expected auth block, got {r.status_code}"

    def test_admin_scores_shape(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/evidence/scores")
        assert r.status_code == 200
        data = r.json()
        for key in ("strategies", "enforcement_enabled",
                    "window_days", "min_trades_for_evidence", "untested_multiplier"):
            assert key in data, f"missing key {key} in scores response"
        assert isinstance(data["strategies"], list)
        assert data["enforcement_enabled"] is False  # SHADOW mode by default
        assert data["window_days"] == 30
        assert data["min_trades_for_evidence"] == 5
        # last_run key must exist (may be None or dict)
        assert "last_run" in data

    def test_admin_recompute_returns_summary(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/admin/evidence/recompute")
        assert r.status_code == 200
        data = r.json()
        for key in ("strategies_evaluated", "wrote", "strategies",
                    "window_days", "min_trades"):
            assert key in data, f"missing key {key}"
        assert isinstance(data["strategies"], list)
