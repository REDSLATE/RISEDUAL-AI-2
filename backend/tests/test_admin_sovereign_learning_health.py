"""Tests — Sovereign Learning-Health diagnostic route (2026-02-26, P0).

The endpoint is a read-only aggregation surface. We stub auth +
provide a tiny in-memory ``sovereign_decisions`` collection so the
counts are deterministic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def now_utc():
    return datetime.now(timezone.utc)


@pytest.fixture
def client(monkeypatch, now_utc):
    # ── Stub owner auth so we exercise the happy paths.
    async def _fake_owner(request):
        return {"role": "owner", "email": "admin@risedual.ai"}

    import routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_current_user", _fake_owner)

    # ── Tiny fake collection. Supports the calls the route makes.
    class _Coll:
        def __init__(self, rows):
            self._rows = rows

        async def count_documents(self, flt):
            return sum(1 for r in self._rows if _matches(r, flt))

        def aggregate(self, pipe):
            # We only support the {$match} + {$group on $action} the
            # route uses.
            match = {}
            for stage in pipe:
                if "$match" in stage:
                    match = stage["$match"]
                    break
            matched = [r for r in self._rows if _matches(r, match)]
            counts: dict[str, int] = {}
            for r in matched:
                key = r.get("action", "UNKNOWN")
                counts[key] = counts.get(key, 0) + 1
            results = [{"_id": k, "n": v} for k, v in counts.items()]

            class _Cur:
                def __init__(self, items):
                    self._items = items

                async def to_list(self, length=None):
                    return list(self._items[: length or len(self._items)])
            return _Cur(results)

    def _matches(row, flt):
        for k, v in flt.items():
            if isinstance(v, dict) and "$lte" in v:
                if not (row.get(k) is not None and row.get(k) <= v["$lte"]):
                    return False
            elif isinstance(v, dict) and "$gte" in v:
                if not (row.get(k) is not None and row.get(k) >= v["$gte"]):
                    return False
            elif isinstance(v, dict) and "$exists" in v:
                # supports nested dotted keys
                parts = k.split(".")
                cur = row
                exists = True
                for p in parts:
                    if not isinstance(cur, dict) or p not in cur:
                        exists = False
                        break
                    cur = cur[p]
                want = v["$exists"]
                if "$ne" in v and exists and cur == v["$ne"]:
                    exists = False
                if exists != want:
                    return False
            elif isinstance(v, dict) and "$ne" in v:
                if row.get(k) == v["$ne"]:
                    return False
            else:
                if "." in k:
                    parts = k.split(".")
                    cur = row
                    for p in parts:
                        if not isinstance(cur, dict):
                            cur = None
                            break
                        cur = cur.get(p)
                    if cur != v:
                        return False
                else:
                    if row.get(k) != v:
                        return False
        return True

    rows = [
        # 3 equity decisions: 2 LONG, 1 HOLD; one resolved at 60m
        {
            "asset_type": "equity", "action": "LONG", "symbol": "AAPL",
            "decision_id": "a", "created_at": now_utc - timedelta(hours=2),
            "feature_snapshot": {"entry_price": 100.0},
            "outcomes": {"60m": {"was_right": True}},
        },
        {
            "asset_type": "equity", "action": "LONG", "symbol": "MSFT",
            "decision_id": "b", "created_at": now_utc - timedelta(hours=3),
            "feature_snapshot": {"entry_price": 300.0},
            "outcomes": {},
        },
        {
            "asset_type": "equity", "action": "HOLD", "symbol": "GOOG",
            "decision_id": "c", "created_at": now_utc - timedelta(minutes=30),
            "feature_snapshot": {},  # legacy: no entry_price
            "outcomes": {},
        },
        # 1 crypto LONG
        {
            "asset_type": "crypto", "action": "SHORT", "symbol": "BTC",
            "decision_id": "d", "created_at": now_utc - timedelta(hours=10),
            "feature_snapshot": {"entry_price": 50000.0},
            "outcomes": {},
        },
    ]

    class _FakeDB:
        def __init__(self):
            self._coll = _Coll(rows)

        def __getitem__(self, name):
            return self._coll

    fake_db = _FakeDB()

    # Stub the promotion-gate compute so the test doesn't need the
    # whole gate module wired.
    async def _fake_promotion(db, asset_type):
        return {
            "promoted": False,
            "blocker": "need 500 more resolved rows",
            "asset_type": asset_type,
        }

    import services.sovereign_promotion_gate as gate_mod
    monkeypatch.setattr(
        gate_mod, "compute_sovereign_promotion_status", _fake_promotion,
    )

    from routes import admin_sovereign_learning_health as route_mod
    route_mod.set_db(fake_db)

    app = FastAPI()
    app.include_router(route_mod.router)
    return TestClient(app)


def test_learning_health_returns_full_shape(client):
    r = client.get("/api/admin/sovereign/learning-health")
    assert r.status_code == 200
    body = r.json()
    assert "generated_at" in body
    assert body["horizons"] == ["60m", "4h", "eod"]
    for asset_type in ("equity", "crypto"):
        section = body[asset_type]
        for key in (
            "decisions_total",
            "decisions_by_action",
            "decisions_last_24h",
            "decisions_with_entry_price",
            "per_horizon",
            "promotion_gate",
        ):
            assert key in section, f"{asset_type} missing {key}"


def test_equity_action_distribution(client):
    body = client.get("/api/admin/sovereign/learning-health").json()
    eq = body["equity"]
    assert eq["decisions_total"] == 3
    assert eq["decisions_by_action"] == {"LONG": 2, "HOLD": 1}
    # 2 equity decisions have feature_snapshot.entry_price set
    assert eq["decisions_with_entry_price"] == 2


def test_per_horizon_resolution_counts(client):
    body = client.get("/api/admin/sovereign/learning-health").json()
    eq60 = body["equity"]["per_horizon"]["60m"]
    # One resolved (was_right=True) decision exists at 60m
    assert eq60["resolved"] == 1
    assert eq60["was_right"] == 1
    # Aged past 60m: the 2-hour-old + 3-hour-old rows = 2
    assert eq60["aged_past_horizon"] == 2
    # Of those aged, 1 is still unresolved (the 3-hour LONG MSFT)
    assert eq60["unresolved_aged"] == 1


def test_promotion_gate_is_inlined_per_asset(client):
    body = client.get("/api/admin/sovereign/learning-health").json()
    for asset_type in ("equity", "crypto"):
        gate = body[asset_type]["promotion_gate"]
        assert gate["asset_type"] == asset_type
        assert gate["promoted"] is False
        assert "500" in gate["blocker"]


def test_owner_role_enforced(monkeypatch):
    async def _fake_visitor(request):
        return {"role": "visitor"}

    import routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_current_user", _fake_visitor)

    from routes import admin_sovereign_learning_health as route_mod
    app = FastAPI()
    app.include_router(route_mod.router)
    c = TestClient(app)
    r = c.get("/api/admin/sovereign/learning-health")
    assert r.status_code == 403
