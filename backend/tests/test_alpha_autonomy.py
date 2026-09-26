"""Tests for the Alpha-native autonomy layer above Core v2.

Proves the doctrine: provider role never grants authority; live requires
authority + explicit arm + Core v2 armed; HALT runs no cycle; everything else
runs Core v2 DRY (live=False)."""
import pytest

from services.alpha_core_v2.autonomy import (
    Authority,
    AutonomyController,
    AutonomyState,
    ProviderRole,
    load_state,
)


class _FakeCycle:
    def to_dict(self):
        return {"traded": 0, "blocked": 0, "failed": 0}


class _FakeEngine:
    def __init__(self):
        self.calls = []

    async def reconcile_outstanding(self):
        self.calls.append("reconcile")
        return {"ok": True, "pending": 0, "finalized": 0}

    async def run_cycle(self, live):
        self.calls.append(live)
        return _FakeCycle()


class _FakeCfg:
    def __init__(self, enabled):
        self.enabled = enabled


def _clear(monkeypatch):
    for k in ("ALPHA_AUTONOMY_AUTHORITY", "ALPHA_AUTONOMY_LIVE"):
        monkeypatch.delenv(k, raising=False)


def test_authority_parse_defaults_observe():
    assert Authority.parse(None) is Authority.OBSERVE
    assert Authority.parse("junk") is Authority.OBSERVE
    assert Authority.parse("AUTONOMOUS") is Authority.AUTONOMOUS
    assert Authority.parse(" halt ") is Authority.HALT


def _state(authority, live, core):
    return AutonomyState(authority=authority, live_armed=live,
                         core_v2_armed=core, provider_roles={})


def test_execute_live_requires_all_three():
    # authority alone is not enough
    assert _state(Authority.AUTONOMOUS, False, True).execute_live() is False
    assert _state(Authority.AUTONOMOUS, True, False).execute_live() is False
    assert _state(Authority.TOEHOLD, True, True).execute_live() is True
    assert _state(Authority.AUTONOMOUS, True, True).execute_live() is True


def test_observe_shadow_halt_never_live():
    for a in (Authority.OBSERVE, Authority.SHADOW, Authority.HALT):
        assert _state(a, True, True).execute_live() is False


def test_provider_primary_never_grants_authority(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALPHA_PROVIDER_ROLE_ANTHROPIC", "primary")
    monkeypatch.setenv("ALPHA_PROVIDER_ROLE_OPENAI", "primary")
    st = load_state(_FakeCfg(enabled=True))  # authority unset → OBSERVE
    assert st.provider_roles["anthropic"] == "primary"
    assert st.authority is Authority.OBSERVE
    assert st.execute_live() is False  # PRIMARY providers, still no authority


@pytest.mark.asyncio
async def test_tick_halt_runs_no_cycle(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALPHA_AUTONOMY_AUTHORITY", "halt")
    eng = _FakeEngine()
    ctrl = AutonomyController(lambda: _ret(eng), cfg=_FakeCfg(True))
    r = await ctrl.tick()
    assert r["ran"] is False and eng.calls == ["reconcile"]


@pytest.mark.asyncio
async def test_tick_observe_runs_dry(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALPHA_AUTONOMY_AUTHORITY", "observe")
    eng = _FakeEngine()
    ctrl = AutonomyController(lambda: _ret(eng), cfg=_FakeCfg(True))
    r = await ctrl.tick()
    assert r["ran"] is True and r["live"] is False and eng.calls == ["reconcile", False]


@pytest.mark.asyncio
async def test_tick_autonomous_without_arm_runs_dry(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALPHA_AUTONOMY_AUTHORITY", "autonomous")  # no live arm
    eng = _FakeEngine()
    ctrl = AutonomyController(lambda: _ret(eng), cfg=_FakeCfg(True))
    r = await ctrl.tick()
    assert r["ran"] is True and r["live"] is False and eng.calls == ["reconcile", False]


@pytest.mark.asyncio
async def test_tick_live_only_when_fully_armed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALPHA_AUTONOMY_AUTHORITY", "autonomous")
    monkeypatch.setenv("ALPHA_AUTONOMY_LIVE", "1")
    eng = _FakeEngine()
    ctrl = AutonomyController(lambda: _ret(eng), cfg=_FakeCfg(True))
    r = await ctrl.tick()
    assert r["live"] is True and eng.calls == ["reconcile", True]


@pytest.mark.asyncio
async def test_tick_engine_unavailable(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ALPHA_AUTONOMY_AUTHORITY", "observe")
    ctrl = AutonomyController(lambda: _ret(None), cfg=_FakeCfg(True))
    r = await ctrl.tick()
    assert r["ran"] is False and "unavailable" in r["reason"]


async def _ret(v):
    return v
