"""Tests — mc_checkin process_identity payload field (Phase 4).

Confirms the 2026-02-27 fix: every checkin POST body includes a
`process_identity` block with pid, hostname, and process_boot_at.
This lets MC's audit trail disambiguate "two pods POSTing as the
same brain_id" — the failure mode confirmed via pip_fingerprint
divergence between preview and prod alpha pods.
"""
from __future__ import annotations

import os

import httpx
import pytest


@pytest.mark.asyncio
async def test_checkin_payload_includes_process_identity(monkeypatch):
    """The POST body MUST carry pid + hostname + boot_time."""
    monkeypatch.setenv("RISEDUAL_APP_NAME", "alpha")
    monkeypatch.setenv("RISEDUAL_ENV", "prod")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("RISEDUAL_DB_NAME", "risedual_db")
    monkeypatch.setenv("RISEDUAL_BROKER_MODE", "paper")
    monkeypatch.setenv("GIT_SHA", "test-sha")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "test-token")

    captured = {}

    class _FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "verdict": "prod", "errors": []}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    from services.mc_checkin import checkin_now
    await checkin_now()

    payload = captured["json"]
    # Schema
    assert "stamp" in payload
    assert "process_identity" in payload
    ident = payload["process_identity"]
    assert ident["pid"] == os.getpid()
    assert isinstance(ident["hostname"], str) and ident["hostname"]
    assert isinstance(ident["process_boot_at"], str)
    assert ident["process_boot_at"].endswith("+00:00")  # tz-aware ISO


@pytest.mark.asyncio
async def test_checkin_process_identity_stable_within_process(monkeypatch):
    """Two checkins from the same process MUST report the same
    pid + boot_time — that's the invariant MC will rely on to
    detect "two pods masquerading as alpha"."""
    monkeypatch.setenv("RISEDUAL_APP_NAME", "alpha")
    monkeypatch.setenv("RISEDUAL_ENV", "prod")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("RISEDUAL_DB_NAME", "risedual_db")
    monkeypatch.setenv("RISEDUAL_BROKER_MODE", "paper")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "test-token")

    captured: list[dict] = []

    class _FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "verdict": "prod", "errors": []}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json, headers):
            captured.append(json["process_identity"])
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    from services.mc_checkin import checkin_now
    await checkin_now()
    await checkin_now()

    assert len(captured) == 2
    assert captured[0]["pid"] == captured[1]["pid"]
    assert captured[0]["hostname"] == captured[1]["hostname"]
    assert captured[0]["process_boot_at"] == captured[1]["process_boot_at"]
