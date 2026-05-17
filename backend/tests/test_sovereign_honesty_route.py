"""Pytest coverage for ``GET /api/sovereign/honesty-mirror`` — the
owner-only proxy to MC's ``/api/admin/intents/honesty`` aggregate.

Locks in three guard rails:

  * Owner-only — non-owner users get 403.
  * When ``MC_BASE_URL`` is unset, we return a structured
    ``mc_status: "unconfigured"`` payload instead of crashing or
    silently 200-ing with zeros.
  * When MC is unreachable / returns a non-200, the operator sees an
    honest ``mc_status`` and the failure cause, not a blank table.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.sovereign_honesty import router as honesty_router


@pytest.fixture
def app_with_router(monkeypatch):
    app = FastAPI()
    app.include_router(honesty_router)
    return app


def _mock_owner(monkeypatch):
    """Patch get_current_user inside the route module so the owner
    gate passes deterministically."""
    from routes import sovereign_honesty as mod
    monkeypatch.setattr(
        mod, "get_current_user",
        AsyncMock(return_value={"role": "owner", "email": "admin@risedual.ai"}),
    )


def _mock_non_owner(monkeypatch):
    from routes import sovereign_honesty as mod
    monkeypatch.setattr(
        mod, "get_current_user",
        AsyncMock(return_value={"role": "user", "email": "user@x.com"}),
    )


def test_honesty_mirror_rejects_non_owner(app_with_router, monkeypatch):
    _mock_non_owner(monkeypatch)
    monkeypatch.setenv("MC_BASE_URL", "https://mc.test")
    client = TestClient(app_with_router)
    r = client.get("/api/sovereign/honesty-mirror?stack=alpha&hours=24")
    assert r.status_code == 403


def test_honesty_mirror_returns_unconfigured_when_base_unset(
    app_with_router, monkeypatch,
):
    _mock_owner(monkeypatch)
    monkeypatch.delenv("MC_BASE_URL", raising=False)
    client = TestClient(app_with_router)
    r = client.get("/api/sovereign/honesty-mirror?stack=alpha&hours=24")
    assert r.status_code == 200
    payload = r.json()
    assert payload["mc_status"] == "unconfigured"
    assert payload["total_intents"] == 0
    assert payload["blocked_directional"] == 0
    assert payload["stack"] == "alpha"
    assert payload["hours"] == 24


def test_honesty_mirror_passes_through_mc_payload(
    app_with_router, monkeypatch,
):
    _mock_owner(monkeypatch)
    monkeypatch.setenv("MC_BASE_URL", "https://mc.test")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok-alpha")

    mc_payload = {
        "total_intents": 234,
        "blocked_directional": 47,
        "by_reason": {"MIN_CONFIDENCE_TO_TRADE": 22, "PDT_GATE": 6},
        "penalty_distribution": [0.0, 0.05, 0.08, 0.12],
    }

    class _FakeResp:
        def __init__(self, status_code: int, body: dict, text: str = ""):
            self.status_code = status_code
            self._body = body
            self.text = text

        def json(self):
            return self._body

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, params=None, headers=None):  # noqa: ARG002
            assert url == "https://mc.test/api/admin/intents/honesty"
            assert params == {"stack": "alpha", "hours": 24}
            assert headers == {"X-Runtime-Token": "tok-alpha"}
            return _FakeResp(200, mc_payload)

    with patch("routes.sovereign_honesty.httpx.AsyncClient", return_value=_FakeClient()):
        client = TestClient(app_with_router)
        r = client.get("/api/sovereign/honesty-mirror?stack=alpha&hours=24")
    assert r.status_code == 200
    payload = r.json()
    assert payload["mc_status"] == "live"
    assert payload["total_intents"] == 234
    assert payload["blocked_directional"] == 47
    assert payload["by_reason"]["MIN_CONFIDENCE_TO_TRADE"] == 22


def test_honesty_mirror_surfaces_mc_non_200_status(
    app_with_router, monkeypatch,
):
    """An MC route mismatch or auth failure should produce a clear
    ``mc_status: mc_returned_NNN`` payload so the operator can
    diagnose without grep-ing logs."""
    _mock_owner(monkeypatch)
    monkeypatch.setenv("MC_BASE_URL", "https://mc.test")

    class _Resp:
        status_code = 403
        text = "forbidden"

        def json(self):
            return {}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *a, **kw):  # noqa: ARG002
            return _Resp()

    with patch("routes.sovereign_honesty.httpx.AsyncClient", return_value=_FakeClient()):
        client = TestClient(app_with_router)
        r = client.get("/api/sovereign/honesty-mirror")
    payload = r.json()
    assert payload["mc_status"] == "mc_returned_403"
    assert "forbidden" in payload["note"]


def test_honesty_mirror_surfaces_unreachable_mc(
    app_with_router, monkeypatch,
):
    """An HTTP error (DNS failure, TLS error, timeout) must not crash
    the route — the operator gets a ``mc_status: unreachable`` payload."""
    import httpx

    _mock_owner(monkeypatch)
    monkeypatch.setenv("MC_BASE_URL", "https://mc.test")

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *a, **kw):  # noqa: ARG002
            raise httpx.ConnectError("name resolution failed")

    with patch("routes.sovereign_honesty.httpx.AsyncClient", return_value=_FakeClient()):
        client = TestClient(app_with_router)
        r = client.get("/api/sovereign/honesty-mirror")
    payload = r.json()
    assert payload["mc_status"] == "unreachable"
    assert "ConnectError" in payload["note"]


@pytest.mark.parametrize("bad_hours", [0, -1, 999])
def test_honesty_mirror_validates_hours_range(
    app_with_router, monkeypatch, bad_hours,
):
    _mock_owner(monkeypatch)
    monkeypatch.setenv("MC_BASE_URL", "https://mc.test")
    client = TestClient(app_with_router)
    r = client.get(f"/api/sovereign/honesty-mirror?hours={bad_hours}")
    assert r.status_code == 422  # FastAPI Query validation
