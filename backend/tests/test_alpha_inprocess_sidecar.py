"""Tests for the in-process Sovereign sidecar (2026-02-23 prod-deploy fix).

Pins:
  * Default OFF — preview/test envs never spawn it.
  * Lockfile guard — if ``/tmp/alpha_alive`` is fresh, the
    supervisor sidecar wins and the in-process loop no-ops.
  * Idempotent start / stop.
  * Failure-soft on missing env (MC_BASE_URL / token).
  * Status payload shape.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Reset module-level task globals between tests."""
    from sovereign import inprocess_sidecar as mod
    mod._sidecar_task = None
    yield
    if mod._sidecar_task is not None and not mod._sidecar_task.done():
        mod._sidecar_task.cancel()


# ── enabled / disabled ────────────────────────────────────────────


def test_default_disabled(monkeypatch):
    """Master switch defaults OFF — vanilla preview/test runs never
    spawn the loop."""
    monkeypatch.delenv("ALPHA_INPROCESS_SIDECAR_ENABLED", raising=False)
    from sovereign import inprocess_sidecar as mod
    assert mod._is_enabled() is False


def test_explicit_enable(monkeypatch):
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "1")
    from sovereign import inprocess_sidecar as mod
    assert mod._is_enabled() is True
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "true")
    assert mod._is_enabled() is True


def test_start_noop_when_disabled():
    """``start()`` MUST return cleanly without spawning when the
    master switch is off."""
    from sovereign import inprocess_sidecar as mod
    out = _run(mod.start())
    assert out["started"] is False
    assert out["reason"] == "disabled"
    assert mod._sidecar_task is None


# ── lockfile guard ────────────────────────────────────────────────


def test_lockfile_guard_supervisor_wins(monkeypatch, tmp_path):
    """If ``/tmp/alpha_alive`` mtime is fresh, the in-process loop
    MUST NOT spawn — supervisor sidecar owns the wire."""
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "1")
    lock = tmp_path / "alpha_alive"
    lock.touch()
    monkeypatch.setenv("SOVEREIGN_LIVENESS_FILE", str(lock))

    from sovereign import inprocess_sidecar as mod
    out = _run(mod.start())
    assert out["started"] is False
    assert out["reason"] == "supervisor_present"


def test_lockfile_guard_stale_lockfile_allows_spawn(monkeypatch, tmp_path):
    """If the lockfile exists but is older than the max-age, the
    supervisor is presumed dead and the in-process loop spawns."""
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "1")
    monkeypatch.setenv("ALPHA_SIDECAR_LOCKFILE_MAX_AGE_S", "30")
    lock = tmp_path / "alpha_alive"
    lock.touch()
    # Backdate the mtime by 1 hour — well past the 30s threshold.
    old = time.time() - 3600
    os.utime(lock, (old, old))
    monkeypatch.setenv("SOVEREIGN_LIVENESS_FILE", str(lock))

    from sovereign import inprocess_sidecar as mod
    assert mod.is_supervisor_sidecar_winning() is False


def test_lockfile_guard_missing_file_allows_spawn(monkeypatch, tmp_path):
    """No lockfile at all = no supervisor running = we should spawn."""
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "1")
    monkeypatch.setenv(
        "SOVEREIGN_LIVENESS_FILE", str(tmp_path / "does-not-exist"),
    )
    from sovereign import inprocess_sidecar as mod
    assert mod.is_supervisor_sidecar_winning() is False


# ── start / stop / idempotency ────────────────────────────────────


def test_start_failsoft_on_missing_env(monkeypatch, tmp_path):
    """Missing MC_BASE_URL / token MUST NOT raise — backend still
    serves API; operator can fix env and bounce."""
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "1")
    monkeypatch.delenv("MC_BASE_URL", raising=False)
    monkeypatch.delenv("ALPHA_INGEST_TOKEN", raising=False)
    monkeypatch.setenv(
        "SOVEREIGN_LIVENESS_FILE", str(tmp_path / "no-lockfile"),
    )

    from sovereign import inprocess_sidecar as mod
    # Spawn — the build will fail-soft inside the loop body.
    out = _run(mod.start())
    assert out["started"] is True
    # Allow the loop one scheduler turn to attempt the build and
    # bail out gracefully.
    async def _settle():
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    _run(_settle())
    # The loop should exit cleanly (return), not raise.
    if mod._sidecar_task and not mod._sidecar_task.done():
        # Give it a brief moment to exit on its own.
        async def _wait():
            try:
                await asyncio.wait_for(mod._sidecar_task, timeout=1.0)
            except asyncio.TimeoutError:
                mod._sidecar_task.cancel()
        _run(_wait())


def test_status_payload_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_INPROCESS_SIDECAR_ENABLED", "1")
    monkeypatch.setenv(
        "SOVEREIGN_LIVENESS_FILE", str(tmp_path / "lock"),
    )
    from sovereign import inprocess_sidecar as mod
    st = mod.status()
    for k in ("enabled", "task_alive", "supervisor_present",
              "interval_s", "lockfile", "lockfile_max_age_s"):
        assert k in st
    assert st["enabled"] is True
    assert st["task_alive"] is False  # not started yet
    assert st["supervisor_present"] is False
    assert st["interval_s"] >= 1


def test_stop_without_start_is_idempotent():
    from sovereign import inprocess_sidecar as mod
    out = _run(mod.stop())
    assert out["stopped"] is False
    assert out["reason"] == "not_running"


# ── wiring static authority ───────────────────────────────────────


def test_server_startup_wires_inprocess_sidecar():
    """The lifespan must spawn the in-process sidecar so a vanilla
    Emergent deploy carries the contribution loop."""
    src = Path("/app/backend/server.py").read_text(encoding="utf-8")
    assert "from sovereign import inprocess_sidecar as _alpha_sov" in src
    assert "_alpha_sov.start()" in src
    assert "_alpha_sov.stop()" in src


def test_server_startup_uses_default_off_doctrine():
    """A future refactor MUST NOT auto-enable the in-process loop
    (that would race the supervisor sidecar in preview where it's
    still wired). Keep the env-gated default-OFF doctrine."""
    src = Path("/app/backend/sovereign/inprocess_sidecar.py").read_text(
        encoding="utf-8",
    )
    # Default must be the env's empty value → False.
    assert "_is_enabled" in src
    # Doctrine comment must be present so the next maintainer
    # understands the lockfile/race nuance.
    assert "lockfile" in src.lower()
    assert "supervisor" in src.lower()
