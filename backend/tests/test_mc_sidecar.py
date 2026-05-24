"""Pytest coverage for the in-process MC sidecar (Alpha port of
REDEYE's pattern). Locks in the start/stop/status surface contract
MC's brain-operator dashboard relies on.

Doctrine asserts:
  * ``start()`` and ``stop()`` are idempotent.
  * Two tasks (heartbeat, watchdog) are tracked
    independently — a hung anything cannot starve heartbeat.
  * The contribution loop was DELETED on 2026-02-23 — the
    supervisor sidecar owns contributions. ``contribution_task_alive``
    is exposed as a constant ``False`` for back-compat with
    REDEYE-shaped MC dashboards.
  * ``status()`` carries the legacy ``task_alive`` alias REDEYE's MC
    parser already speaks.
  * Watchdog can be disabled (e.g. local dev) by env, but heartbeat
    always runs.
  * Liveness file age is exposed for operator visibility.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def fake_db():
    """A motor-shaped mock: ``db[STATE_COLLECTION].find_one`` /
    ``update_one`` both awaitable. The sidecar's state-persistence
    path is best-effort so we never need to test the Mongo branch
    deeply — just that the calls happen and don't raise."""
    col = MagicMock()
    col.find_one = AsyncMock(return_value={"status": "running"})
    col.update_one = AsyncMock(return_value=None)
    db = MagicMock()
    db.__getitem__.return_value = col
    return db


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    """Each test runs against a fresh task globals + a watchdog that
    never actually SIGKILLs the test process."""
    import services.mc_sidecar as mod

    # Reset task globals between tests.
    mod._heartbeat_task = None
    mod._watchdog_task = None

    # Make every HTTP call into a no-op so the loops can spin without
    # talking to MC. The transport-level invariants we care about are
    # tested in the receipt-bridge + mc_client tests already.
    class _FakeResp:
        status_code = 200
        text = ""

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *_a, **_kw):
            return _FakeResp()

    monkeypatch.setattr(mod, "_async_client", lambda: _FakeClient())

    # Watchdog must not SIGKILL the pytest runner — patch os.kill
    # only, keep everything else (os.environ) intact.
    monkeypatch.setattr(
        "services.mc_sidecar.os.kill",
        lambda pid, sig: None,
    )

    yield

    # Tear down any leaked tasks from a failing test.
    for ref in (mod._heartbeat_task, mod._watchdog_task):
        if ref is not None and not ref.done():
            ref.cancel()


# ── start / stop / status idempotence ──────────────────────────────────


@pytest.mark.asyncio
async def test_start_spawns_heartbeat_and_watchdog(fake_db):
    """Doctrine 2026-02-23: only TWO tasks spawn (heartbeat +
    watchdog). The contribution loop was deleted — the supervisor
    sidecar owns contributions now."""
    import services.mc_sidecar as mod

    st = await mod.start(fake_db)
    try:
        assert st["heartbeat_task_alive"] is True
        assert st["watchdog_task_alive"] is True
        # Contribution is gone — surface stays the same shape but
        # always False, with an owner tag for operator clarity.
        assert st["contribution_task_alive"] is False
        assert st["contribution_owned_by"] == "supervisor:alpha-sidecar"
        # task_alive legacy alias reflects heartbeat only now.
        assert st["task_alive"] is True
    finally:
        await mod.stop(fake_db)


@pytest.mark.asyncio
async def test_start_is_idempotent(fake_db):
    """Calling start() twice must not spawn duplicate tasks."""
    import services.mc_sidecar as mod
    await mod.start(fake_db)
    t1 = (mod._heartbeat_task, mod._watchdog_task)
    await mod.start(fake_db)
    t2 = (mod._heartbeat_task, mod._watchdog_task)
    try:
        # Same task objects → no double-spawn.
        assert t1 == t2
    finally:
        await mod.stop(fake_db)


@pytest.mark.asyncio
async def test_stop_cancels_all_tasks(fake_db):
    import services.mc_sidecar as mod
    await mod.start(fake_db)
    st = await mod.stop(fake_db)
    assert st["heartbeat_task_alive"] is False
    assert st["watchdog_task_alive"] is False
    assert st["task_alive"] is False
    # contribution_task_alive remains False (it's a constant now).
    assert st["contribution_task_alive"] is False


@pytest.mark.asyncio
async def test_stop_without_start_is_idempotent(fake_db):
    """Calling stop on a never-started sidecar must not raise."""
    import services.mc_sidecar as mod
    st = await mod.stop(fake_db)
    assert st["heartbeat_task_alive"] is False
    assert st["task_alive"] is False


# ── doctrine: contribution loop is gone ────────────────────────────────


def test_contribution_loop_function_is_removed():
    """The deleted function MUST stay deleted — the supervisor
    sidecar is now the canonical contribution producer. If a
    future refactor accidentally re-adds it the inbox-drain race
    returns."""
    import services.mc_sidecar as mod
    assert not hasattr(mod, "_contribution_loop"), (
        "mc_sidecar._contribution_loop was deleted on 2026-02-23 — "
        "the supervisor sidecar is the canonical producer. If you "
        "need to re-add it, also re-add the inbox-drain race "
        "mitigation."
    )
    # The task global is also gone.
    assert not hasattr(mod, "_contrib_task")


def test_module_docstring_pins_doctrine_decision():
    """Future maintainers must be able to find the 2026-02-23
    decision in the module docstring."""
    import services.mc_sidecar as mod
    doc = (mod.__doc__ or "").lower()
    assert "2026-02-23" in doc
    assert "supervisor" in doc
    assert "canonical" in doc


# ── status payload shape ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_includes_required_keys(fake_db):
    import services.mc_sidecar as mod
    await mod.start(fake_db)
    try:
        st = await mod.status(fake_db)
        for k in (
            "contribution_task_alive", "contribution_owned_by",
            "heartbeat_task_alive", "watchdog_task_alive", "task_alive",
            "intervals", "watchdog", "identity",
        ):
            assert k in st, f"status missing required key: {k}"
        # Intervals echo the env-driven config. ``contribution_s``
        # is retained for back-compat with REDEYE-shaped parsers
        # even though no contribution loop exists in this module.
        assert st["intervals"]["heartbeat_s"] >= 1
        assert st["intervals"]["contribution_s"] >= 1
        # Watchdog block is fully populated.
        wd = st["watchdog"]
        assert "enabled" in wd
        assert "stale_threshold_s" in wd
        assert "liveness_file" in wd
        assert "liveness_age_s" in wd
    finally:
        await mod.stop(fake_db)


@pytest.mark.asyncio
async def test_status_identity_does_not_leak_runtime_token(fake_db):
    """``identity`` must surface ``has_runtime_token`` (a bool) but
    never the token itself — operators read this in logs."""
    import services.mc_sidecar as mod
    st = await mod.status(fake_db)
    ident = st["identity"]
    assert "name" in ident
    assert "has_runtime_token" in ident
    assert isinstance(ident["has_runtime_token"], bool)
    # Belt-and-braces: assert no key in identity *looks* like a token.
    for k in ident:
        assert "token" not in k or k == "has_runtime_token", (
            f"identity exposed a token-shaped field: {k}"
        )


# ── watchdog disable path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watchdog_disabled_does_not_spawn_task(fake_db, monkeypatch):
    """``ALPHA_WATCHDOG_ENABLED=false`` must keep the watchdog task
    dormant while still spawning heartbeat."""
    import services.mc_sidecar as mod

    monkeypatch.setattr(mod, "WATCHDOG_ENABLED", False)
    await mod.start(fake_db)
    try:
        assert mod._heartbeat_task is not None
        assert mod._watchdog_task is None
        st = await mod.status(fake_db)
        assert st["watchdog_task_alive"] is False
        assert st["heartbeat_task_alive"] is True
        # Legacy alias should reflect the live heartbeat task.
        assert st["task_alive"] is True
    finally:
        await mod.stop(fake_db)


# ── heartbeat independence (2026-05-14 silent-freeze invariant) ────────


@pytest.mark.asyncio
async def test_heartbeat_task_independent_of_watchdog(fake_db):
    """If we cancel ONLY the watchdog task by hand, the heartbeat
    task must keep running. This guards the single-loop SPOF
    that caused the silent freeze in the first place."""
    import services.mc_sidecar as mod
    await mod.start(fake_db)
    try:
        # Cancel watchdog out of band.
        if mod._watchdog_task is not None:
            mod._watchdog_task.cancel()
        await asyncio.sleep(0.05)
        st = await mod.status(fake_db)
        assert st["watchdog_task_alive"] is False
        assert st["heartbeat_task_alive"] is True
        assert st["task_alive"] is True
    finally:
        await mod.stop(fake_db)


# ── liveness file age helper ───────────────────────────────────────────


def test_liveness_age_returns_none_when_missing(tmp_path, monkeypatch):
    import services.mc_sidecar as mod
    monkeypatch.setattr(mod, "LIVENESS_FILE", tmp_path / "does-not-exist")
    assert mod._liveness_age_s() is None


def test_liveness_age_returns_seconds_when_present(tmp_path, monkeypatch):
    import services.mc_sidecar as mod
    fp = tmp_path / "alpha_alive"
    fp.touch()
    monkeypatch.setattr(mod, "LIVENESS_FILE", fp)
    age = mod._liveness_age_s()
    assert age is not None
    assert age >= 0.0
    assert age < 5.0


# ── brain_identity ─────────────────────────────────────────────────────


def test_brain_identity_reads_env(monkeypatch):
    from services import mc_sovereign
    monkeypatch.setenv("ALPHA_BRAIN_NAME", "alpha")
    monkeypatch.setenv("ALPHA_BRAIN_VERSION", "1.6")
    monkeypatch.setenv("MC_BASE_URL", "https://mission.risedual.ai")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok")
    ident = mc_sovereign.brain_identity()
    assert ident["name"] == "alpha"
    assert ident["version"] == "1.6"
    assert ident["mc_base_url"] == "https://mission.risedual.ai"
    assert ident["has_runtime_token"] is True


def test_brain_identity_redacts_runtime_token(monkeypatch):
    from services import mc_sovereign
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "super-secret-token")
    ident = mc_sovereign.brain_identity()
    # Hard guarantee: the token's value never appears in the identity dict.
    assert "super-secret-token" not in str(ident)

