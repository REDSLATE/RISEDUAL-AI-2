"""Tests for pattern sensitivity scalar (2026-02).

The pre-fix pattern gates produced 0 fills in 14 days. New
``ALPHA_PATTERN_SENSITIVITY`` env (default 1.25) scales:
* rvol floors DOWN
* distance windows UP
* pct-change floors DOWN

Guardrails:
* Default 1.25 loosens gates enough that mid-magnitude intraday moves fire
* Env override honored, bounded 0.5..2.5
* Sensitivity=1.0 reproduces the original strict behavior
* Sensitivity=2.0 fires on borderline moves that 1.0 rejected
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.alpha_day_trader import (
    AlphaPatternEngine, MarketSnapshot, SetupType,
)


def _snap(**overrides):
    base = dict(
        symbol="TEST", price=100.0, volume=1e6, avg_volume=1e6,
        relative_volume=1.0, open_price=100.0, high=100.0, low=100.0,
        vwap=100.0, bid=99.99, ask=100.01, spread_bps=2.0,
        pct_change=0.0, volume_acceleration=1.0,
        timestamp=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return MarketSnapshot(**base)


# ─── sensitivity accessor ───────────────────────────────────────


def test_sensitivity_default_is_125(monkeypatch):
    monkeypatch.delenv("ALPHA_PATTERN_SENSITIVITY", raising=False)
    assert AlphaPatternEngine._sensitivity() == pytest.approx(1.25)


def test_sensitivity_env_override(monkeypatch):
    monkeypatch.setenv("ALPHA_PATTERN_SENSITIVITY", "1.75")
    assert AlphaPatternEngine._sensitivity() == pytest.approx(1.75)


def test_sensitivity_bounded_low(monkeypatch):
    monkeypatch.setenv("ALPHA_PATTERN_SENSITIVITY", "0.01")
    assert AlphaPatternEngine._sensitivity() == pytest.approx(0.5)


def test_sensitivity_bounded_high(monkeypatch):
    monkeypatch.setenv("ALPHA_PATTERN_SENSITIVITY", "99")
    assert AlphaPatternEngine._sensitivity() == pytest.approx(2.5)


def test_sensitivity_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("ALPHA_PATTERN_SENSITIVITY", "abc")
    assert AlphaPatternEngine._sensitivity() == pytest.approx(1.25)


# ─── HOD_BREAK at mid-rvol only fires with loosened sensitivity ──


def test_hod_break_rejected_at_strict_sensitivity(monkeypatch):
    """At sensitivity=1.0 (original strict), rvol=1.7 doesn't hit
    the 2.0 floor and HOD_BREAK does not arm."""
    monkeypatch.setenv("ALPHA_PATTERN_SENSITIVITY", "1.0")
    m = _snap(price=105.0, vwap=102.5, open_price=100.0, high=105.2,
              low=99.9, relative_volume=1.7, pct_change=3.5,
              volume_acceleration=1.0)  # accel below strict 1.25
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    # None of the patterns match at rvol=1.7 + accel=1.0 under strict
    assert setup is None or setup.setup_type != SetupType.HOD_BREAK


def test_hod_break_fires_at_relaxed_sensitivity(monkeypatch):
    """At sensitivity=1.5 (relaxed), 2.0/1.5 = 1.33 rvol floor and
    1.25/1.5 = 0.83 accel floor. rvol=1.7 + accel=1.0 both pass."""
    monkeypatch.setenv("ALPHA_PATTERN_SENSITIVITY", "1.5")
    m = _snap(price=105.0, vwap=102.5, open_price=100.0, high=105.2,
              low=99.9, relative_volume=1.7, pct_change=3.5,
              volume_acceleration=1.0)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    assert setup is not None
    assert setup.setup_type == SetupType.HOD_BREAK


# ─── momentum reaccel — 2.5% move fires only when relaxed ──────


def test_momentum_reaccel_needs_relaxed_for_25pct_move(monkeypatch):
    """A 2.5% move + 1.2 vol accel is below the strict 3.0/1.5 gates
    but passes at sens=1.25 (3.0/1.25=2.4, 1.5/1.25=1.2)."""
    monkeypatch.setenv("ALPHA_PATTERN_SENSITIVITY", "1.25")
    m = _snap(price=102.5, vwap=100.5, open_price=100.0, high=102.5,
              low=99.9, relative_volume=1.3, pct_change=2.5,
              volume_acceleration=1.2)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    assert setup is not None


# ─── strict mode still rejects borderline setups ────────────────


def test_strict_mode_rejects_25pct_move(monkeypatch):
    """Same 2.5% move fails to arm at strict sens=0.75."""
    monkeypatch.setenv("ALPHA_PATTERN_SENSITIVITY", "0.75")
    m = _snap(price=102.5, vwap=100.5, open_price=100.0, high=102.5,
              low=99.9, relative_volume=1.3, pct_change=2.5,
              volume_acceleration=1.2)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    # Should not arm the momentum_reaccel pattern under strict mode
    # (may still arm VWAP_RECLAIM or something else — that's fine)
    assert setup is None or setup.setup_type != SetupType.MOMENTUM_REACCELERATION


# ─── existing tests still work at default sensitivity ───────────


def test_high_rvol_setups_still_fire_at_default_sensitivity(monkeypatch):
    """Classic strong HOD break (rvol=5.0) still fires unchanged."""
    monkeypatch.delenv("ALPHA_PATTERN_SENSITIVITY", raising=False)
    m = _snap(price=105.0, vwap=102.5, open_price=100.0, high=105.2,
              low=99.9, relative_volume=5.0, pct_change=5.0,
              volume_acceleration=2.0)
    setup = AlphaPatternEngine().detect(m, slow_regime="trend_up")
    assert setup is not None
    assert setup.setup_type == SetupType.HOD_BREAK
