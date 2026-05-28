"""Tests — Kill-Switch Profile runtime ADMIN ROUTES (2026-02-26, P2)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    async def _fake_owner(request):
        return {"role": "owner", "email": "admin@risedual.ai"}

    import routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_current_user", _fake_owner)

    # In-memory config + trades collections (same fakes as the
    # runtime tests, but trimmed since we exercise the HTTP shell).
    class _ConfigColl:
        def __init__(self):
            self._docs: dict = {}

        async def find_one(self, flt, projection=None):
            return self._docs.get(flt.get("_id"))

        async def update_one(self, flt, update, upsert=False):
            key = flt.get("_id")
            self._docs[key] = {
                **(self._docs.get(key) or {}), **update.get("$set", {}),
            }

            class _R:
                modified_count = 1
                matched_count = 1
                upserted_id = key
            return _R()

        async def delete_one(self, flt):
            existed = self._docs.pop(flt.get("_id"), None) is not None

            class _R:
                deleted_count = 1 if existed else 0
            return _R()

    class _TradesColl:
        def aggregate(self, pipe):
            class _Cur:
                async def to_list(self, length=None):
                    return []
            return _Cur()

        def find(self, flt, projection=None):
            class _Cur:
                def sort(self, *a, **k): return self
                def limit(self, n): return self
                def __aiter__(self):
                    async def gen():
                        if False:
                            yield None
                    return gen()
            return _Cur()

    class _FakeDB:
        def __init__(self):
            self._cfg = _ConfigColl()
            self._tr = _TradesColl()

        def __getitem__(self, name):
            return self._cfg if name == "kill_switch_profile_global" else self._tr

    fake_db = _FakeDB()
    from routes import admin_kill_switch_profile_runtime as route_mod
    route_mod.set_db(fake_db)

    app = FastAPI()
    app.include_router(route_mod.router)
    return TestClient(app)


def test_status_when_inactive(client):
    r = client.get("/api/admin/kill-switch/runtime/equity/status")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] is False
    assert body["config"] is None
    assert body["halt"] is False


def test_activate_then_status_shows_config(client):
    r = client.post(
        "/api/admin/kill-switch/runtime/equity/activate",
        json={
            "profile_key": "small_account_warrior",
            "starting_equity_usd": 1000,
            "note": "test run",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["asset_type"] == "equity"
    assert body["profile_key"] == "small_account_warrior"
    assert body["starting_equity_usd"] == 1000.0

    r2 = client.get("/api/admin/kill-switch/runtime/equity/status")
    assert r2.status_code == 200
    s = r2.json()
    assert s["active"] is True
    assert s["config"]["profile_key"] == "small_account_warrior"
    assert s["session"]["realized_pnl_usd_today"] == 0.0
    assert s["halt"] is False


def test_activate_rejects_unknown_profile(client):
    r = client.post(
        "/api/admin/kill-switch/runtime/equity/activate",
        json={"profile_key": "bogus", "starting_equity_usd": 1000},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_profile"


def test_activate_rejects_non_positive_equity(client):
    r = client.post(
        "/api/admin/kill-switch/runtime/equity/activate",
        json={"profile_key": "small_account_warrior", "starting_equity_usd": 0},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "non_positive_starting_equity"


def test_activate_rejects_invalid_asset_type(client):
    r = client.post(
        "/api/admin/kill-switch/runtime/forex/activate",
        json={"profile_key": "small_account_warrior", "starting_equity_usd": 1000},
    )
    assert r.status_code == 422


def test_deactivate_clears_config(client):
    client.post(
        "/api/admin/kill-switch/runtime/equity/activate",
        json={"profile_key": "small_account_warrior", "starting_equity_usd": 1000},
    )
    r = client.delete("/api/admin/kill-switch/runtime/equity/active")
    assert r.status_code == 200
    assert r.json()["cleared"] is True
    r2 = client.get("/api/admin/kill-switch/runtime/equity/status")
    assert r2.json()["active"] is False


def test_owner_role_enforced(monkeypatch):
    async def _fake_visitor(request):
        return {"role": "visitor"}

    import routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_current_user", _fake_visitor)

    from routes import admin_kill_switch_profile_runtime as route_mod
    app = FastAPI()
    app.include_router(route_mod.router)
    c = TestClient(app)
    r = c.get("/api/admin/kill-switch/runtime/equity/status")
    assert r.status_code == 403
