"""Tests for Alpha's mean-reversion setup family (2026-02).

Guardrails:
* Alpha must produce a setup for chop regimes (previously the bot sat
  idle for days because only momentum patterns existed).
* Mean-reversion patterns get a score boost during chop; momentum
  patterns get a small penalty (but never suppression to zero).
* Executor confidence floor drops to 0.55 when the intent is tagged
  with a chop regime, so mean-reversion intents can actually fire.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.alpha_day_trader import (
    AlphaPatternEngine,
    MarketSnapshot,
    SetupType,
    _is_chop_regime,
    MEAN_REVERT_PATTERNS,
    MOMENTUM_PATTERNS,
)
from services.public_equity_live_executor import (
    _effective_confidence_floor,
    _live_confidence_floor,
    _live_confidence_floor_chop,
)


# ─── chop-regime detector ────────────────────────────────────────


@pytest.mark.parametrize("labels,expected", [
    (("choppy_meanrevert",), True),
    (("session_chop",), True),
    ((None, "session_chop"), True),
    (("range_bound",), True),
    (("trend_up",), False),
    (("momentum_ignition_up",), False),
    ((None, None), False),
    ((None,), False),
    (("", ""), False),
])
def test_is_chop_regime(labels, expected):
    assert _is_chop_regime(*labels) is expected


# ─── pattern taxonomy ────────────────────────────────────────────


def test_pattern_families_are_disjoint_where_it_matters():
    # PULLBACK is intentionally in both families (it straddles).
    only_momentum = MOMENTUM_PATTERNS - MEAN_REVERT_PATTERNS
    only_mean_revert = MEAN_REVERT_PATTERNS - MOMENTUM_PATTERNS
    assert only_momentum, "momentum family must not be empty"
    assert only_mean_revert, "mean-reversion family must not be empty"


# ─── mean-reversion pattern detection ────────────────────────────


def _snap(**overrides):
    """Build a MarketSnapshot with sane defaults."""
    base = dict(
        symbol="TEST", price=100.0, volume=1e6, avg_volume=1e6,
        relative_volume=1.0, open_price=100.0, high=100.0, low=100.0,
        vwap=100.0, bid=99.99, ask=100.01, spread_bps=2.0,
        pct_change=0.0, volume_acceleration=1.0,
        timestamp=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return MarketSnapshot(**base)


def test_vwap_fade_long_arms_in_chop():
    """Price 0.5% below vwap + red bar + normal rvol → VWAP_FADE_LONG."""
    m = _snap(price=100.0, vwap=100.5, open_price=101.0, high=101.5,
              low=99.5, relative_volume=1.2, pct_change=-0.8)
    pe = AlphaPatternEngine()
    setup = pe.detect(m, slow_regime="choppy_meanrevert",
                       fast_regime="session_chop")
    assert setup is not None
    assert setup.setup_type == SetupType.VWAP_FADE_LONG
    # Chop boost bumps 0.62 → 0.72
    assert setup.score == pytest.approx(0.72, abs=0.001)


def test_vwap_fade_long_still_fires_outside_chop_with_lower_score():
    """Raw pattern shape triggers even in trend regime — no gate."""
    m = _snap(price=100.0, vwap=100.5, open_price=101.0, high=101.5,
              low=99.5, relative_volume=1.2, pct_change=-0.8)
    pe = AlphaPatternEngine()
    setup = pe.detect(m, slow_regime="trend_up")
    assert setup is not None
    assert setup.setup_type == SetupType.VWAP_FADE_LONG
    # No boost outside chop — stays at base 0.62
    assert setup.score == pytest.approx(0.62, abs=0.001)


def test_momentum_hod_gets_soft_penalty_in_chop_but_still_fires():
    """HOD_BREAK is still allowed in chop — just scored slightly lower."""
    m = _snap(price=105.0, vwap=102.5, open_price=100.0, high=105.2,
              low=99.9, relative_volume=5.0, pct_change=5.0,
              volume_acceleration=2.0)
    pe = AlphaPatternEngine()
    trend = pe.detect(m, slow_regime="trend_up")
    chop = pe.detect(m, slow_regime="choppy_meanrevert")
    assert trend is not None and chop is not None
    assert trend.setup_type == SetupType.HOD_BREAK
    assert chop.setup_type == SetupType.HOD_BREAK
    # Chop applies a 0.05 penalty
    assert chop.score < trend.score
    assert chop.score == pytest.approx(trend.score - 0.05, abs=0.001)


def test_range_low_bounce_arms_at_intraday_low():
    """Price at bottom of intraday range, low rvol → RANGE_LOW_BOUNCE."""
    # VWAP set FAR from price to avoid triggering VWAP_FADE_LONG first
    m = _snap(price=99.6, vwap=99.6, open_price=101.0, high=101.0,
              low=99.5, relative_volume=1.0, pct_change=-1.4)
    pe = AlphaPatternEngine()
    setup = pe.detect(m, slow_regime="choppy_meanrevert")
    assert setup is not None
    assert setup.setup_type == SetupType.RANGE_LOW_BOUNCE
    # Chop boost bumps 0.60 → 0.70
    assert setup.score == pytest.approx(0.70, abs=0.001)


def test_opening_drive_fade_arms_when_price_reclaims_open():
    """Gap-down that has been bought back to open → OPENING_DRIVE_FADE.

    Sequence of pattern checks in detect() matters: VWAP_FADE_LONG and
    RANGE_LOW_BOUNCE run first. This scenario is designed so neither
    matches (price is at open, not below vwap enough, not near low).
    """
    # Session where price gapped down and has recovered to open
    m = _snap(price=100.1, vwap=100.05, open_price=100.0, high=100.3,
              low=98.5, relative_volume=1.5, pct_change=-0.5)
    pe = AlphaPatternEngine()
    setup = pe.detect(m, slow_regime="session_chop")
    assert setup is not None
    assert setup.setup_type == SetupType.OPENING_DRIVE_FADE


def test_detect_regime_defaults_to_no_bias_when_omitted():
    """When regime is not supplied (existing callers), behaviour is
    identical to the trending-neutral case — no crash, no boost."""
    m = _snap(price=100.0, vwap=100.5, open_price=101.0, high=101.5,
              low=99.5, relative_volume=1.2, pct_change=-0.8)
    pe = AlphaPatternEngine()
    setup = pe.detect(m)
    assert setup is not None
    assert setup.score == pytest.approx(0.62, abs=0.001)


# ─── regime-aware confidence floor ───────────────────────────────


def test_default_floor_when_env_unset(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", raising=False)
    assert _live_confidence_floor() == pytest.approx(0.65)


def test_chop_floor_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP", raising=False)
    assert _live_confidence_floor_chop() == pytest.approx(0.55)


def test_chop_floor_is_lower_than_default_floor(monkeypatch):
    """Contract: whatever the operator sets, the chop floor MUST be
    ≤ the default floor. Chop patterns naturally score lower — a
    higher chop floor would silently disable the chop playbook."""
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", raising=False)
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP", raising=False)
    assert _live_confidence_floor_chop() <= _live_confidence_floor()


def test_effective_floor_uses_chop_for_choppy_meanrevert(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", raising=False)
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP", raising=False)
    floor, label = _effective_confidence_floor({"regime": "choppy_meanrevert"})
    assert floor == pytest.approx(0.55)
    assert label == "chop"


def test_effective_floor_uses_chop_for_session_chop(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", raising=False)
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP", raising=False)
    floor, label = _effective_confidence_floor({"fast_regime": "session_chop"})
    assert floor == pytest.approx(0.55)
    assert label == "chop"


def test_effective_floor_uses_default_for_trending(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", raising=False)
    floor, label = _effective_confidence_floor({"regime": "trend_up"})
    assert floor == pytest.approx(0.65)
    assert label == "default"


def test_effective_floor_uses_default_when_regime_missing(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", raising=False)
    floor, label = _effective_confidence_floor({})
    assert floor == pytest.approx(0.65)
    assert label == "default"


def test_env_overrides_chop_floor(monkeypatch):
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP", "0.50")
    assert _live_confidence_floor_chop() == pytest.approx(0.50)


def test_env_overrides_chop_floor_bounded(monkeypatch):
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP", "1.5")
    assert _live_confidence_floor_chop() == pytest.approx(0.95)
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP", "-0.1")
    assert _live_confidence_floor_chop() == pytest.approx(0.0)


def test_env_bad_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP", "not_a_number")
    assert _live_confidence_floor_chop() == pytest.approx(0.55)
