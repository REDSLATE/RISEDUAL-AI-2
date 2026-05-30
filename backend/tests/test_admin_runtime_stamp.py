"""Tests — admin runtime stamp endpoint."""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.admin_runtime_stamp import router as runtime_stamp_router


@pytest.fixture
def client_owner(monkeypatch):
    """Test client with auth bypassed as owner."""
    app = FastAPI()
    app.include_router(runtime_stamp_router)

    async def _fake_owner(_request):
        return {"user_id": "test", "role": "owner"}

    import routes.admin_runtime_stamp as mod
    monkeypatch.setattr(mod, "_require_owner", _fake_owner)
    return TestClient(app)


@pytest.fixture
def client_non_owner(monkeypatch):
    app = FastAPI()
    app.include_router(runtime_stamp_router)

    async def _fake_non_owner(_request):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Owner access required")

    import routes.admin_runtime_stamp as mod
    monkeypatch.setattr(mod, "_require_owner", _fake_non_owner)
    return TestClient(app)


def test_runtime_stamp_shape(client_owner, monkeypatch):
    monkeypatch.setenv("RISEDUAL_ENV", "prod")
    monkeypatch.setenv("RISEDUAL_DB_NAME", "risedual_db")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("RISEDUAL_BROKER_MODE", "paper")
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    monkeypatch.setenv("RISEDUAL_LOCAL_TRADES_BLOCKED", "true")
    monkeypatch.setenv("RISEDUAL_EMIT_INTENTS_TO_MC", "1")
    monkeypatch.setenv("POLYGON_API_KEY", "p")
    monkeypatch.setenv("FINNHUB_API_KEY", "f")

    res = client_owner.get("/api/admin/runtime/stamp")
    assert res.status_code == 200
    body = res.json()

    # Top-level shape
    assert set(body.keys()) >= {
        "runtime_stamp", "validator_self_check",
        "operator_trading_gate", "intent_emission",
        "mc_keys_proxy", "tokens_present",
    }

    # Validator self-check should report ok=True given the env above
    assert body["validator_self_check"]["ok"] is True
    assert body["validator_self_check"]["errors"] == []
    assert body["validator_self_check"]["env_name"] == "prod"
    assert body["validator_self_check"]["db_name"] == "risedual_db"

    # Gate state — RISEDUAL_LOCAL_TRADES_BLOCKED=true → blocked
    assert body["operator_trading_gate"]["blocked"] is True

    # Keys present flags
    assert body["mc_keys_proxy"]["POLYGON_API_KEY_present"] is True
    assert body["mc_keys_proxy"]["FINNHUB_API_KEY_present"] is True


def test_runtime_stamp_surfaces_validator_errors(client_owner, monkeypatch):
    """Bad env_name produces the same error MC would return."""
    monkeypatch.setenv("RISEDUAL_ENV", "preview")
    monkeypatch.setenv("RISEDUAL_DB_NAME", "")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("RISEDUAL_BROKER_MODE", "paper")

    res = client_owner.get("/api/admin/runtime/stamp")
    assert res.status_code == 200
    body = res.json()
    errors = body["validator_self_check"]["errors"]
    assert "ENV_NOT_PROD" in errors
    assert "BAD_OR_UNKNOWN_DB_NAME" in errors


def test_runtime_stamp_gate_open_when_unblocked(client_owner, monkeypatch):
    monkeypatch.setenv("RISEDUAL_LOCAL_TRADES_BLOCKED", "false")
    monkeypatch.delenv("OPERATOR_TRADING_AUTHORIZATION_ENABLED", raising=False)
    res = client_owner.get("/api/admin/runtime/stamp")
    body = res.json()
    assert body["operator_trading_gate"]["blocked"] is False
    assert "open" in body["operator_trading_gate"]["reason"]


def test_runtime_stamp_never_returns_token_values(client_owner, monkeypatch):
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "supersecret-token-xyz")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "another-secret-abc")
    res = client_owner.get("/api/admin/runtime/stamp")
    body = res.json()
    full_text = str(body)
    # Token values must NEVER leak into the response.
    assert "supersecret-token-xyz" not in full_text
    assert "another-secret-abc" not in full_text
    # Only presence booleans surface.
    assert body["tokens_present"]["ALPHA_INGEST_TOKEN"] is True
    assert body["tokens_present"]["ALPHA_MC_INGEST_TOKEN"] is True


def test_runtime_stamp_non_owner_blocked(client_non_owner):
    res = client_non_owner.get("/api/admin/runtime/stamp")
    assert res.status_code == 403
