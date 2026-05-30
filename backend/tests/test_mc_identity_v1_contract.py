"""MC v1 brain identity contract — tripwire pinning.

Pinned by spec from MC (2026-05-30). DO NOT relax these assertions
without coordinating a v2 spec bump with MC's operator. The
``GET /api/status`` payload + lifecycle log format MUST match MC's
BrainHealthTile expectations exactly.

Tests:
- v1 identity block contains exactly the documented field set
- ``checkin_worker_eligible`` true iff all 4 env vars present
- Lifecycle log STARTED branch uses exact phrase
- Lifecycle log NOT STARTED branch names the missing env var(s)
- ``GET /api/status`` route returns the v1 block, no auth required
- Public payload contains NO token values (presence booleans only)
- Alpha env aliasing maps historical names → v1 names without
  overwriting an operator-set v1 value
"""
from __future__ import annotations

import logging
import os
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.mc_identity_v1 import (
    _LIFECYCLE_LOGGED,
    build_identity_block,
    is_checkin_eligible,
    log_lifecycle,
)


_V1_FIELD_SET = {
    "app_name",
    "env_name",
    "git_sha",
    "broker_mode",
    "sidecar_version",
    "mc_url_set",
    "ingest_token_set",
    "mc_base_url_set",
    "heartbeat_token_set",
    "checkin_worker_eligible",
}


def _set_all_v1_envs(monkeypatch):
    monkeypatch.setenv("MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("MC_INGEST_TOKEN", "test-ingest")
    monkeypatch.setenv("MC_BASE_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("HEARTBEAT_TOKEN", "test-heartbeat")


def _clear_lifecycle_latch():
    _LIFECYCLE_LOGGED.clear()


def test_v1_field_set_exact(monkeypatch):
    _set_all_v1_envs(monkeypatch)
    block = build_identity_block(app_name="alpha", sidecar_version="1.0.0")
    assert set(block.keys()) == _V1_FIELD_SET, (
        "v1 identity field set has drifted. If intentional, bump to "
        "v2 and dual-publish."
    )


def test_eligible_true_when_all_four_envs_set(monkeypatch):
    _set_all_v1_envs(monkeypatch)
    block = build_identity_block(app_name="alpha", sidecar_version="1.0.0")
    assert block["checkin_worker_eligible"] is True
    assert block["mc_url_set"] is True
    assert block["ingest_token_set"] is True
    assert block["mc_base_url_set"] is True
    assert block["heartbeat_token_set"] is True


def test_eligible_false_when_any_env_missing(monkeypatch):
    _set_all_v1_envs(monkeypatch)
    monkeypatch.delenv("HEARTBEAT_TOKEN", raising=False)
    block = build_identity_block(app_name="alpha", sidecar_version="1.0.0")
    assert block["checkin_worker_eligible"] is False
    assert block["heartbeat_token_set"] is False


def test_empty_string_treated_as_unset(monkeypatch):
    """Common deploy bug — env panel writes empty string. v1 must treat
    that as unset, otherwise dashboard shows green while worker silent."""
    _set_all_v1_envs(monkeypatch)
    monkeypatch.setenv("MC_INGEST_TOKEN", "   ")
    block = build_identity_block(app_name="alpha", sidecar_version="1.0.0")
    assert block["ingest_token_set"] is False
    assert block["checkin_worker_eligible"] is False


def test_lifecycle_log_started_phrase(monkeypatch, caplog):
    _set_all_v1_envs(monkeypatch)
    _clear_lifecycle_latch()
    block = build_identity_block(app_name="alpha", sidecar_version="1.0.0")
    with caplog.at_level(logging.INFO, logger="brain.mc_identity"):
        log_lifecycle(block)
    msgs = [r.getMessage() for r in caplog.records if r.name == "brain.mc_identity"]
    assert any(
        "mc_checkin worker STARTED" in m
        and "periodic check-in every 300s" in m
        and "MC_URL set" in m
        and "MC_INGEST_TOKEN set" in m
        and "MC_BASE_URL set" in m
        and "HEARTBEAT_TOKEN set" in m
        for m in msgs
    ), f"STARTED log format drifted. Got: {msgs}"


def test_lifecycle_log_not_started_names_missing(monkeypatch, caplog):
    _set_all_v1_envs(monkeypatch)
    monkeypatch.delenv("MC_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("HEARTBEAT_TOKEN", raising=False)
    _clear_lifecycle_latch()
    block = build_identity_block(app_name="alpha", sidecar_version="1.0.0")
    with caplog.at_level(logging.WARNING, logger="brain.mc_identity"):
        log_lifecycle(block)
    msgs = [r.getMessage() for r in caplog.records if r.name == "brain.mc_identity"]
    target = next(
        (m for m in msgs if "mc_checkin worker NOT STARTED" in m),
        None,
    )
    assert target is not None, f"NOT STARTED log missing. Got: {msgs}"
    assert "missing env vars" in target
    assert "MC_INGEST_TOKEN" in target
    assert "HEARTBEAT_TOKEN" in target


def test_lifecycle_log_fires_once_per_process(monkeypatch, caplog):
    _set_all_v1_envs(monkeypatch)
    _clear_lifecycle_latch()
    block = build_identity_block(app_name="alpha", sidecar_version="1.0.0")
    with caplog.at_level(logging.INFO, logger="brain.mc_identity"):
        log_lifecycle(block)
        log_lifecycle(block)
        log_lifecycle(block)
    started = [
        r for r in caplog.records
        if r.name == "brain.mc_identity"
        and "mc_checkin worker STARTED" in r.getMessage()
    ]
    assert len(started) == 1, f"lifecycle fired {len(started)} times, expected 1"


def test_is_checkin_eligible_sentinel(monkeypatch):
    _set_all_v1_envs(monkeypatch)
    assert is_checkin_eligible() is True
    monkeypatch.delenv("MC_URL", raising=False)
    assert is_checkin_eligible() is False


# ── GET /api/status route ─────────────────────────────────────────


def _build_status_client():
    from routes.public_status import router as status_router
    app = FastAPI()
    app.include_router(status_router)
    return TestClient(app)


def test_status_route_returns_v1_block(monkeypatch):
    _set_all_v1_envs(monkeypatch)
    client = _build_status_client()
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "identity" in body
    assert set(body["identity"].keys()) == _V1_FIELD_SET


def test_status_route_no_auth_required(monkeypatch):
    """MC's BrainHealthTile reads /api/status without an operator JWT."""
    _set_all_v1_envs(monkeypatch)
    client = _build_status_client()
    # No Authorization header.
    resp = client.get("/api/status")
    assert resp.status_code == 200


def test_status_route_never_returns_token_values(monkeypatch):
    """The public payload MUST NOT leak token values. Presence only."""
    _set_all_v1_envs(monkeypatch)
    monkeypatch.setenv("MC_INGEST_TOKEN", "super-secret-token-abc-xyz")
    monkeypatch.setenv("HEARTBEAT_TOKEN", "another-secret-heartbeat-9999")
    client = _build_status_client()
    resp = client.get("/api/status")
    text = resp.text
    assert "super-secret-token-abc-xyz" not in text
    assert "another-secret-heartbeat-9999" not in text


# ── Alpha env aliasing ────────────────────────────────────────────


def test_alpha_env_aliasing_maps_historical_names(monkeypatch):
    """Alpha's historical RISEDUAL_MC_URL etc. must populate v1 names."""
    monkeypatch.delenv("MC_URL", raising=False)
    monkeypatch.delenv("MC_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("MC_BASE_URL", raising=False)
    monkeypatch.delenv("HEARTBEAT_TOKEN", raising=False)
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_MC_INGEST_TOKEN", "alpha-tok")
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "alpha-tok")

    from server import _alias_mc_identity_env_vars
    _alias_mc_identity_env_vars()

    assert os.environ.get("MC_URL") == "https://mission.risedual.ai"
    assert os.environ.get("MC_INGEST_TOKEN") == "alpha-tok"
    assert os.environ.get("MC_BASE_URL") == "https://mission.risedual.ai"
    assert os.environ.get("HEARTBEAT_TOKEN") == "alpha-tok"


def test_alpha_env_aliasing_never_overwrites_operator_value(monkeypatch):
    """If the operator set MC_URL directly, the alias step must respect it."""
    monkeypatch.setenv("MC_URL", "https://operator-override.example.com")
    monkeypatch.setenv("RISEDUAL_MC_URL", "https://historical-default.example.com")

    from server import _alias_mc_identity_env_vars
    _alias_mc_identity_env_vars()

    assert os.environ.get("MC_URL") == "https://operator-override.example.com"


def test_status_route_wins_collision_with_legacy_api_router(monkeypatch):
    """Regression — Emergent template ships a legacy api_router.get('/status')
    returning the status_checks debug collection. Our v1 identity route
    MUST be registered first so MC's BrainHealthTile reads the identity
    block, not the test-iteration list.

    Mounts the public_status router BEFORE a stub legacy /status route
    that would otherwise shadow it — mirrors production server.py order.
    """
    _set_all_v1_envs(monkeypatch)
    from fastapi import APIRouter
    from routes.public_status import router as public_status_router

    app = FastAPI()
    app.include_router(public_status_router)  # must come first

    legacy = APIRouter(prefix="/api")

    @legacy.get("/status")
    async def _legacy_status():
        return [{"id": "legacy-1", "client_name": "test-iteration-100"}]

    app.include_router(legacy)

    client = TestClient(app)
    resp = client.get("/api/status")
    body = resp.json()
    # Must be the identity block, NOT the legacy debug list.
    assert isinstance(body, dict), f"legacy route shadowed v1: {body}"
    assert "identity" in body
    assert set(body["identity"].keys()) == _V1_FIELD_SET
