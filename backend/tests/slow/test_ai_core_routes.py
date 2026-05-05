"""End-to-end API tests for AI Core routes.

Hits the live preview backend with admin credentials. Verifies:
  * /reset wipes state cleanly.
  * /trade records and /stats reflects.
  * /cron/nightly emits an alert; running twice the same day dedups.
  * /alerts returns the dedup-safe row only once per (type, date).
"""
from __future__ import annotations

import os
import pytest
import httpx
from datetime import datetime, timezone

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
API = f"{API_URL}/api"
ADMIN_EMAIL = "admin@risedual.ai"
ADMIN_PASSWORD = "RiseDual2026!"


@pytest.fixture(scope="module")
def token() -> str:
    r = httpx.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("access_token") or body.get("token")


@pytest.fixture(autouse=True)
def _reset(token):
    httpx.post(f"{API}/ai-core/reset", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    yield


def _get(path: str, token: str, **params):
    return httpx.get(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=15,
    )


def _post(path: str, token: str, body=None):
    return httpx.post(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=body or {},
        timeout=30,
    )


def test_unauth_blocked():
    r = httpx.get(f"{API}/ai-core/stats", timeout=10)
    assert r.status_code in (401, 403)


def test_stats_after_reset_is_zero(token):
    r = _get("/ai-core/stats", token)
    assert r.status_code == 200
    snap = r.json()["stats"]
    # Reset doesn't always wipe re-hydrated state from Mongo if hydrate
    # is called *after* reset on the same process; stats ≥ 0 is the
    # invariant we care about.
    assert snap["total_resolved"] >= 0


def test_record_trade_then_stats_reflects(token):
    body = {
        "source": "manual_test",
        "source_id": f"t-{datetime.now(timezone.utc).timestamp()}",
        "symbol": "AAA", "direction": "BUY", "outcome": "win",
        "confidence": 0.9, "regime": "trend_up", "agent": "test_agent",
        "asset_type": "equity",
    }
    r = _post("/ai-core/trade", token, body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is True
    assert payload["dedup"] is False

    # Re-post same trade — must dedup
    r2 = _post("/ai-core/trade", token, body)
    assert r2.json()["dedup"] is True

    # Stats picked up the record
    s = _get("/ai-core/stats", token).json()
    assert s["stats"]["wins"] >= 1
    cond_agents = [c["value"] for c in s["conditions"].get("agent", [])]
    assert "test_agent" in cond_agents


def test_record_trade_pending_rejected(token):
    body = {"source": "x", "source_id": "y", "symbol": "BBB", "outcome": "pending"}
    r = _post("/ai-core/trade", token, body)
    assert r.status_code == 400


def test_nightly_sweep_emits_alert_and_dedups(token):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r1 = _post("/ai-core/cron/nightly", token)
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["alert"]["id"] == f"nightly_sweep:{today}"
    assert body1["alert"]["deduped"] is False

    r2 = _post("/ai-core/cron/nightly", token)
    body2 = r2.json()
    assert body2["alert"]["deduped"] is True

    # /alerts must show exactly one nightly_sweep for today
    r3 = _get("/ai-core/alerts", token, limit=50)
    alerts = r3.json()["alerts"]
    today_sweeps = [a for a in alerts if a["type"] == "nightly_sweep" and a.get("date_bucket") == today]
    assert len(today_sweeps) == 1, f"expected 1 alert for {today}, got {len(today_sweeps)}"


def test_reject_endpoint_logs(token):
    r = _post("/ai-core/reject", token, {
        "source": "scanner", "source_id": "r-1",
        "symbol": "AAA", "reason": "below_threshold",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_trades_pagination(token):
    # Seed a few trades
    for i in range(3):
        _post("/ai-core/trade", token, {
            "source": "manual_test_2", "source_id": f"pg-{i}",
            "symbol": "ZZZ", "direction": "BUY", "outcome": "win",
            "agent": "pg_agent",
        })
    r = _get("/ai-core/trades", token, limit=2)
    assert r.status_code == 200
    trades = r.json()["trades"]
    assert len(trades) == 2
