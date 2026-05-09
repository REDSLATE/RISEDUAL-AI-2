"""Sidecar safety + wiring tests.

The Alpha Monorepo Sidecar must:
  * NEVER raise — emits are fire-and-forget.
  * Be silently disabled when env vars are missing or
    ``MONOREPO_SIDECAR_ENABLED=false``.
  * Serialize the canonical payloads (receipt / memory-label /
    calibrator / artifact / heartbeat) the monorepo expects.
  * Tag every body with the local ``RUNTIME_NAME``.
"""
from __future__ import annotations

import asyncio
import os
import pytest

from services import risedual_monorepo_client as mono


# ── _enabled() gating ───────────────────────────────────────────────


def test_disabled_when_kill_switch_set(monkeypatch):
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "false")
    monkeypatch.setenv("MONOREPO_BASE_URL", "http://x")
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "t")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")
    assert mono._enabled() is False


def test_disabled_when_env_missing(monkeypatch):
    monkeypatch.delenv("MONOREPO_BASE_URL", raising=False)
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "t")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "true")
    assert mono._enabled() is False


def test_enabled_when_env_present(monkeypatch):
    monkeypatch.setenv("MONOREPO_BASE_URL", "http://x")
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "t")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "true")
    assert mono._enabled() is True


# ── _post() never raises ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_returns_disabled_when_off(monkeypatch):
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "false")
    out = await mono._post("receipts", {"action": "x"})
    assert out["ok"] is False
    assert out["error"] == "sidecar_disabled"


@pytest.mark.asyncio
async def test_post_swallows_network_errors(monkeypatch):
    monkeypatch.setenv("MONOREPO_BASE_URL", "http://127.0.0.1:1")  # no listener
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "t")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "true")
    # Reset the cached client so it picks up the unreachable host.
    await mono.aclose()
    out = await mono._post("heartbeat", {"status": "ok"})
    assert out["ok"] is False
    assert "error" in out
    await mono.aclose()


# ── fire_and_forget never raises ────────────────────────────────────


def test_fire_and_forget_no_loop_drops_coro():
    async def _coro():
        return 1

    # No running loop in this thread — must close the coro and return.
    mono.fire_and_forget(_coro())  # no exception


@pytest.mark.asyncio
async def test_fire_and_forget_schedules_on_running_loop(monkeypatch):
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "false")
    seen = {"hit": 0}

    async def _coro():
        seen["hit"] += 1

    mono.fire_and_forget(_coro())
    # Yield control so the scheduled task runs.
    await asyncio.sleep(0.05)
    assert seen["hit"] == 1


# ── Public emit helpers shape the body correctly ────────────────────


@pytest.mark.asyncio
async def test_emit_receipt_body_shape(monkeypatch):
    monkeypatch.setenv("MONOREPO_BASE_URL", "http://x")
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "t")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")
    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "true")

    captured: dict = {}

    async def fake_post(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"ok": True}

    monkeypatch.setattr(mono, "_post", fake_post)
    await mono.emit_receipt(
        action="alpha_decision_log",
        intent={"symbol": "BTC", "lane": "crypto", "decision": "APPROVED"},
        executed=False,
    )
    assert captured["path"] == "receipts"
    assert captured["body"]["action"] == "alpha_decision_log"
    assert captured["body"]["executed"] is False
    assert captured["body"]["intent"]["symbol"] == "BTC"


@pytest.mark.asyncio
async def test_emit_memory_label_validates_label_passthrough(monkeypatch):
    captured: dict = {}

    async def fake_post(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"ok": True}

    monkeypatch.setattr(mono, "_post", fake_post)
    await mono.emit_memory_label(
        label="quarantine", reason="missing_source", payload_summary="x=1",
    )
    assert captured["path"] == "memory-labels"
    assert captured["body"]["label"] == "quarantine"
    assert captured["body"]["reason"] == "missing_source"


@pytest.mark.asyncio
async def test_register_calibrator_and_artifact(monkeypatch):
    captured: list = []

    async def fake_post(path, body):
        captured.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(mono, "_post", fake_post)
    await mono.register_calibrator(
        name="chevelle_isotonic", version="v1", method="isotonic",
    )
    await mono.register_artifact(
        artifact="calibrator", version="v1", sha="abc",
    )
    paths = [p for p, _ in captured]
    assert paths == ["calibrators", "artifacts"]


@pytest.mark.asyncio
async def test_heartbeat_default_status(monkeypatch):
    captured: dict = {}

    async def fake_post(path, body):
        captured.update({"path": path, "body": body})
        return {"ok": True}

    monkeypatch.setattr(mono, "_post", fake_post)
    await mono.heartbeat()
    assert captured["path"] == "heartbeat"
    assert captured["body"]["status"] == "ok"


# ── Wiring smoke: ADL receipt path schedules a sidecar task ─────────


@pytest.mark.asyncio
async def test_alpha_decision_log_fires_sidecar(monkeypatch):
    """``record_decision`` must mirror to the sidecar after the local
    insert succeeds. We capture by patching ``emit_receipt``."""
    from services import alpha_decision_log as adl

    monkeypatch.setenv("MONOREPO_SIDECAR_ENABLED", "true")
    monkeypatch.setenv("MONOREPO_BASE_URL", "http://x")
    monkeypatch.setenv("MONOREPO_INGEST_TOKEN", "t")
    monkeypatch.setenv("RUNTIME_NAME", "alpha")

    seen: list = []

    async def fake_emit(action, intent, executed=False):
        seen.append({"action": action, "intent": intent, "executed": executed})
        return {"ok": True}

    monkeypatch.setattr(mono, "emit_receipt", fake_emit)

    # Tiny in-memory fake DB.
    class _Coll:
        async def insert_one(self, doc):
            class _R:
                inserted_id = "fake-id"
            return _R()

    class _DB:
        def __getitem__(self, _name):
            return _Coll()

    rid = await adl.record_decision(
        _DB(),
        symbol="BTC-USD",
        lane="crypto",
        decision="APPROVED",
        reason="passthrough",
        confidence=0.8,
    )
    assert rid == "fake-id"
    # Yield so the fire-and-forget task can run.
    await asyncio.sleep(0.05)
    assert len(seen) == 1
    assert seen[0]["action"] == "alpha_decision_log"
    assert seen[0]["intent"]["symbol"] == "BTC-USD"
    assert seen[0]["executed"] is False
