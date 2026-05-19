"""Tests for the MC sidecar check-in client.

Covers the survival contract:
  * Boot stamp shape + policy_hash matches the doctrine constant
  * Required env vars enforce themselves (RUntimeError on missing
    MC URL / token, BEFORE any network call)
  * Network call carries the X-Runtime-Token header
  * Verdict !=  "prod" logs at ERROR level (not warning)
  * Periodic loop catches all exceptions so MC flakiness can't crash
    Alpha
"""
from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import patch, MagicMock

import httpx
import pytest

from services.mc_checkin import (
    RuntimeStamp,
    _policy_hash,
    checkin_now,
    start_periodic_checkin,
    stop_periodic_checkin,
)


_EXPECTED_POLICY_HASH = (
    "2ac7d02164886f5c9c4a6339a605bf7be87b2bf2b532ea08681b5c29a6dcea25"
)


# ── policy_hash is the doctrine constant ────────────────────────────


def test_policy_hash_is_the_doctrine_constant():
    """MC pins this hash. If we ever edit `_POLICY` without coordinating
    with MC, every Alpha check-in will come back with
    policy_hash_match=false and verdict=policy_drift."""
    assert _policy_hash() == _EXPECTED_POLICY_HASH


def test_policy_hash_matches_shared_runtime_module():
    """The two survival surfaces (intent pre-flight + check-in) MUST
    agree on the same dict. If they drift, MC's classifier will see
    one Alpha emitting two different policy_hashes."""
    from shared.runtime.platform_survival import policy_hash as sl_hash
    assert _policy_hash() == sl_hash()


# ── RuntimeStamp shape ──────────────────────────────────────────────


def test_runtime_stamp_carries_doctrine_pinned_fields(monkeypatch):
    monkeypatch.setenv("RISEDUAL_APP_NAME", "alpha")
    monkeypatch.setenv("RISEDUAL_ENV", "preview")
    monkeypatch.setenv("RISEDUAL_PLATFORM", "emergent")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("RISEDUAL_DB_NAME", "test_db")
    monkeypatch.setenv("RISEDUAL_BROKER_MODE", "paper")
    monkeypatch.setenv("RISEDUAL_SIDECAR_VERSION", "1.0.0")
    monkeypatch.setenv("GIT_SHA", "alpha-r1")

    stamp = RuntimeStamp.current()

    assert stamp.app_name == "alpha"
    assert stamp.env_name == "preview"
    assert stamp.platform == "emergent"
    assert stamp.mc_url == "https://mission.risedual.ai"
    assert stamp.db_name == "test_db"
    assert stamp.broker_mode == "paper"
    assert stamp.sidecar_version == "1.0.0"
    assert stamp.git_sha == "alpha-r1"
    assert stamp.sidecar_room == "alpha-room"
    # Doctrine-pinned: this MUST be False. The dataclass hard-pins it.
    assert stamp.local_execution_authority is False
    # Hash always matches the constitution constant.
    assert stamp.policy_hash == _EXPECTED_POLICY_HASH


def test_runtime_stamp_defaults_when_env_unset(monkeypatch):
    for key in (
        "RISEDUAL_APP_NAME", "RISEDUAL_ENV", "RISEDUAL_PLATFORM",
        "RISEDUAL_MC_URL", "RISEDUAL_DB_NAME", "RISEDUAL_BROKER_MODE",
        "RISEDUAL_SIDECAR_VERSION", "GIT_SHA",
    ):
        monkeypatch.delenv(key, raising=False)

    stamp = RuntimeStamp.current()
    assert stamp.app_name == "alpha"
    assert stamp.env_name == "unknown"
    assert stamp.local_execution_authority is False  # Still pinned.


# ── checkin_now() env validation ────────────────────────────────────


@pytest.mark.asyncio
async def test_checkin_raises_when_mc_url_missing(monkeypatch):
    monkeypatch.delenv("RISEDUAL_MC_URL", raising=False)
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")

    with pytest.raises(RuntimeError, match="RISEDUAL_MC_URL"):
        await checkin_now()


@pytest.mark.asyncio
async def test_checkin_raises_when_token_missing(monkeypatch):
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.delenv("ALPHA_MC_INGEST_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="ALPHA_MC_INGEST_TOKEN"):
        await checkin_now()


# ── checkin_now() network behaviour ─────────────────────────────────


class _MockResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=MagicMock(), response=MagicMock(),
            )

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_checkin_posts_stamp_with_runtime_token_header(monkeypatch):
    """Confirms (a) URL is /api/admin/runtime/sidecar-checkin/alpha,
    (b) header is X-Runtime-Token, (c) body wraps stamp under 'stamp'."""
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("RISEDUAL_ENV", "preview")

    captured = {}

    async def _fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _MockResponse(
            payload={"verdict": "preview", "errors": [], "note": ""},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    result = await checkin_now()

    assert captured["url"] == (
        "https://mission.risedual.ai/api/admin/runtime/sidecar-checkin/alpha"
    )
    assert captured["headers"]["X-Runtime-Token"] == "secret-token"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert "stamp" in captured["json"]
    assert captured["json"]["stamp"]["policy_hash"] == _EXPECTED_POLICY_HASH
    assert captured["json"]["stamp"]["local_execution_authority"] is False
    assert result["verdict"] == "preview"


@pytest.mark.asyncio
async def test_checkin_logs_loudly_on_non_prod_verdict(monkeypatch, caplog):
    """A non-`prod` verdict is the operator's drift tripwire — it
    must be logged at ERROR, not warning."""
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    caplog.set_level(logging.ERROR, logger="alpha.mc_checkin")

    async def _fake_post(self, url, json=None, headers=None):
        return _MockResponse(payload={
            "verdict": "policy_drift",
            "errors": ["bad_hash"],
            "note": "alpha shipped stale doctrine",
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    await checkin_now()

    error_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("policy_drift" in m for m in error_msgs)
    assert any("bad_hash" in m for m in error_msgs)


@pytest.mark.asyncio
async def test_checkin_quiet_on_prod_verdict(monkeypatch, caplog):
    """The happy path logs at INFO, not ERROR — keeps prod logs clean."""
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    monkeypatch.setenv("RISEDUAL_ENV", "prod")
    caplog.set_level(logging.INFO, logger="alpha.mc_checkin")

    async def _fake_post(self, url, json=None, headers=None):
        return _MockResponse(payload={"verdict": "prod", "errors": []})

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    await checkin_now()

    msgs = [r.getMessage() for r in caplog.records]
    assert any("verdict=prod" in m for m in msgs)
    # No ERROR-level records.
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_checkin_raises_on_http_error(monkeypatch):
    """The boot caller decides whether to block — the helper just
    raises so the caller can log + decide."""
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")

    async def _fake_post(self, url, json=None, headers=None):
        return _MockResponse(status_code=401)

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        await checkin_now()


# ── Periodic loop ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_periodic_loop_swallows_errors(monkeypatch, caplog):
    """A flaky MC must never crash Alpha. The periodic loop catches
    every exception so the next interval still fires."""
    monkeypatch.setenv("RISEDUAL_MC_CHECKIN_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")
    caplog.set_level(logging.ERROR, logger="alpha.mc_checkin")

    call_count = {"n": 0}

    async def _flaky_post(self, url, json=None, headers=None):
        call_count["n"] += 1
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "post", _flaky_post)

    task = start_periodic_checkin()
    await asyncio.sleep(0.18)  # let it tick ~3 times
    await stop_periodic_checkin()

    assert task.done()  # loop exited cleanly on cancel
    assert call_count["n"] >= 2, "loop should re-fire after each failure"
    # And it logged the failures (exception path).
    msgs = [r.getMessage() for r in caplog.records]
    assert any("periodic ping failed" in m for m in msgs)


@pytest.mark.asyncio
async def test_start_periodic_checkin_stores_task_on_app_state(monkeypatch):
    monkeypatch.setenv("RISEDUAL_MC_CHECKIN_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "tok")

    async def _noop_post(self, url, json=None, headers=None):
        return _MockResponse(payload={"verdict": "prod"})

    monkeypatch.setattr(httpx.AsyncClient, "post", _noop_post)

    class _State:
        pass

    state = _State()
    task = start_periodic_checkin(state)
    try:
        assert state.mc_checkin_task is task
        assert not task.done()
    finally:
        await stop_periodic_checkin()
