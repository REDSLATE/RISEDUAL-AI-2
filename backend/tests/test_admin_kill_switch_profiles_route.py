"""Route tests — admin_kill_switch_profiles (2026-02-26, P2)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """Build a tiny FastAPI app with the kill-switch admin router and
    a stubbed auth dependency that grants 'owner' so we can exercise
    the happy paths without dragging real auth in."""

    async def _fake_owner(request):
        return {"role": "owner", "email": "admin@risedual.ai"}

    # ``routes.auth.get_current_user`` is imported lazily inside the
    # router's _require_owner. Patch the symbol at the source module
    # before mounting the router.
    import routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_current_user", _fake_owner)

    from routes.admin_kill_switch_profiles import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_list_profiles_route_returns_warrior(client: TestClient):
    r = client.get("/api/admin/kill-switch/profiles")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    keys = {p["key"] for p in body["profiles"]}
    assert "small_account_warrior" in keys


def test_get_single_profile_happy_path(client: TestClient):
    r = client.get("/api/admin/kill-switch/profiles/small_account_warrior")
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "small_account_warrior"
    assert body["rules"]["consecutive_loss_limit"] == 3
    assert body["rules"]["daily_max_loss_pct"] == 0.10
    assert body["rules"]["daily_max_loss_usd"] == 100.0


def test_get_single_profile_404(client: TestClient):
    r = client.get("/api/admin/kill-switch/profiles/bogus_key")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_profile"


def test_evaluate_quiet_day_returns_no_halt(client: TestClient):
    r = client.post(
        "/api/admin/kill-switch/profiles/small_account_warrior/evaluate",
        json={
            "starting_equity_usd": 1000,
            "realized_pnl_usd_today": 20,
            "consecutive_losses_today": 0,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["halt"] is False
    assert body["triggers"] == []


def test_evaluate_triple_loss_halts(client: TestClient):
    r = client.post(
        "/api/admin/kill-switch/profiles/small_account_warrior/evaluate",
        json={
            "starting_equity_usd": 1000,
            "realized_pnl_usd_today": -30,
            "consecutive_losses_today": 3,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["halt"] is True
    rules = {t["rule"] for t in body["triggers"]}
    assert "rule_3_consecutive_losses" in rules


def test_evaluate_rejects_negative_equity(client: TestClient):
    r = client.post(
        "/api/admin/kill-switch/profiles/small_account_warrior/evaluate",
        json={
            "starting_equity_usd": -500,
            "realized_pnl_usd_today": 0,
            "consecutive_losses_today": 0,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "negative_starting_equity"


def test_evaluate_unknown_profile_returns_404(client: TestClient):
    r = client.post(
        "/api/admin/kill-switch/profiles/nope/evaluate",
        json={
            "starting_equity_usd": 1000,
            "realized_pnl_usd_today": 0,
            "consecutive_losses_today": 0,
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_profile"


def test_owner_role_enforced(monkeypatch):
    async def _fake_visitor(request):
        return {"role": "visitor"}

    import routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_current_user", _fake_visitor)

    from routes.admin_kill_switch_profiles import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)

    r = c.get("/api/admin/kill-switch/profiles")
    assert r.status_code == 403
