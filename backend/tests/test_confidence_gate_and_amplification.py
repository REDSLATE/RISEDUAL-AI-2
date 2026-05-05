"""
Tests for the dynamic confidence gate + sovereign adversarial amplification.
"""
from __future__ import annotations

import asyncio

import pytest

from services.confidence_gate import (
    _current_base_min_confidence,
    _current_max_threshold,
    compute_dynamic_threshold,
    get_dynamic_confidence_threshold,
)
from services.sovereign_ai_core import SovereignFeatures, _strategist_model
from tests.test_sovereign_ai_core import _FakeDB


# Use the *current* (call-time) values throughout this file so the
# tests are robust to other modules in the suite mutating the env.
def BASE_MIN_CONFIDENCE() -> float:  # noqa: N802 — keep symbol shape
    return _current_base_min_confidence()


def MAX_THRESHOLD() -> float:  # noqa: N802 — keep symbol shape
    return _current_max_threshold()


@pytest.fixture(autouse=True)
def _pin_confidence_gate_base_to_production(monkeypatch):
    """These tests pin the *production* threshold maths (BASE=0.70).
    Sibling test files (e.g. ``test_crypto_paper_bot``) override the
    env to 0.55 — without this fixture the 0.55 leak corrupts the
    cap test."""
    monkeypatch.delenv("CONFIDENCE_GATE_BASE", raising=False)
    monkeypatch.delenv("CONFIDENCE_GATE_CAP", raising=False)


def _run(coro):
    # Fresh loop avoids RuntimeError: no current event loop
    # pollution after a prior async test closes pytest-asyncio's loop.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Dynamic threshold pure tests ─────────────────────────────────────────────


def test_baseline_threshold_is_base():
    t = compute_dynamic_threshold()
    assert t.threshold == BASE_MIN_CONFIDENCE()
    assert t.delta == 0
    assert "baseline" in t.reasons


def test_drawdown_10pct_adds_005():
    t = compute_dynamic_threshold(drawdown=0.10)
    assert abs(t.delta - 0.05) < 1e-9
    assert any("drawdown>=10%" in r for r in t.reasons)


def test_drawdown_15pct_stacks_to_015():
    """Operator spec: 10% AND 15% branches stack — at 15% we get +0.15."""
    t = compute_dynamic_threshold(drawdown=0.15)
    assert abs(t.delta - 0.15) < 1e-9


def test_calibration_gap_adds_005():
    t = compute_dynamic_threshold(calibration_gap=0.10)
    assert abs(t.delta - 0.05) < 1e-9
    assert any("calibration_gap" in r for r in t.reasons)


def test_loss_streak_3_adds_005():
    t = compute_dynamic_threshold(loss_streak=3)
    assert abs(t.delta - 0.05) < 1e-9


def test_loss_streak_under_3_no_effect():
    t = compute_dynamic_threshold(loss_streak=2)
    assert t.delta == 0


def test_all_branches_compose():
    t = compute_dynamic_threshold(
        drawdown=0.16, calibration_gap=0.10, loss_streak=4,
    )
    # 0.05 + 0.10 + 0.05 + 0.05 = 0.25
    assert abs(t.delta - 0.25) < 1e-9


def test_threshold_capped_at_max():
    """Even with all branches firing, threshold caps at MAX_THRESHOLD."""
    t = compute_dynamic_threshold(
        drawdown=0.50, calibration_gap=0.50, loss_streak=10,
    )
    assert t.threshold == MAX_THRESHOLD()


def test_passes_helper():
    # threshold = current base + 0.05 (drawdown)
    t = compute_dynamic_threshold(drawdown=0.10)
    assert t.passes(t.threshold + 0.05) is True
    assert t.passes(t.threshold - 0.05) is False


# ─── Mongo-backed reader (calibration gap) ────────────────────────────────────


def test_get_dynamic_threshold_baseline_with_no_data():
    """Empty DB → baseline threshold."""
    db = _FakeDB()
    t = _run(get_dynamic_confidence_threshold(db, asset_type="equity"))
    assert t.threshold == BASE_MIN_CONFIDENCE()


def test_get_dynamic_threshold_swallows_mongo_errors():
    """Broken DB → baseline threshold (never raise)."""
    class BrokenDB:
        def __getitem__(self, name):
            raise RuntimeError("mongo down")

    t = _run(get_dynamic_confidence_threshold(BrokenDB(), asset_type="equity"))
    assert t.threshold == BASE_MIN_CONFIDENCE()
    assert t.delta == 0


# ─── Sovereign adversarial amplification ──────────────────────────────────────


def test_sovereign_amplifies_when_upstream_long_disagrees_with_native_bear():
    """Upstream strategist LONG @ 0.85 but native compute leans bear → bear amplified."""
    f_with_pressure = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=70, momentum_5b=-0.02,  # native lean = bear
        strategist_action="LONG",
        strategist_confidence=0.85,  # upstream confidently LONG (contradicting)
    )
    f_baseline = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=70, momentum_5b=-0.02,  # same native compute
        strategist_action=None,
        strategist_confidence=None,
    )
    v_with = _strategist_model(f_with_pressure)
    v_base = _strategist_model(f_baseline)
    # The adversarial amplification should make the bear vote stronger
    assert v_with["bear"] > v_base["bear"]


def test_sovereign_amplifies_when_upstream_short_disagrees_with_native_bull():
    f = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=30, momentum_5b=0.03,  # native lean = bull
        strategist_action="SHORT",
        strategist_confidence=0.85,  # upstream confidently SHORT
    )
    f_baseline = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=30, momentum_5b=0.03,
    )
    v_with = _strategist_model(f)
    v_base = _strategist_model(f_baseline)
    assert v_with["bull"] > v_base["bull"]


def test_sovereign_no_amplification_when_upstream_agrees():
    """When upstream agrees with native compute, no amplification fires."""
    f_agree = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=30, momentum_5b=0.03,  # native lean = bull
        strategist_action="LONG",  # upstream agrees
        strategist_confidence=0.85,
    )
    f_baseline = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=30, momentum_5b=0.03,
    )
    v_with = _strategist_model(f_agree)
    v_base = _strategist_model(f_baseline)
    # Same vote — no amplification when sides agree
    assert v_with["bull"] == v_base["bull"]
    assert v_with["bear"] == v_base["bear"]


def test_sovereign_no_amplification_when_upstream_low_confidence():
    """Upstream proposal with conf < 0.70 doesn't trigger amplification —
    a hesitant proposal isn't worth attacking."""
    f = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=70, momentum_5b=-0.02,
        strategist_action="LONG",
        strategist_confidence=0.55,  # too low
    )
    f_baseline = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=70, momentum_5b=-0.02,
    )
    v_with = _strategist_model(f)
    v_base = _strategist_model(f_baseline)
    assert v_with["bear"] == v_base["bear"]


def test_sovereign_amplification_capped_at_020():
    """Even when upstream is at max confidence, amplification can't exceed 0.20."""
    f_high = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=70, momentum_5b=-0.02,
        strategist_action="LONG",
        strategist_confidence=0.99,  # max
    )
    f_med = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=70, momentum_5b=-0.02,
        strategist_action="LONG",
        strategist_confidence=0.70,  # threshold floor
    )
    v_high = _strategist_model(f_high)
    v_med = _strategist_model(f_med)
    # Difference should be bounded by the cap
    assert (v_high["bear"] - v_med["bear"]) <= 0.20 + 1e-9


def test_sovereign_amplification_doesnt_fire_on_neutral_native_compute():
    """When native compute is exactly neutral (no bull/bear lean), there's
    nothing to amplify — neither side should grow."""
    f = SovereignFeatures(
        symbol="X", asset_type="equity",
        rsi=50, momentum_5b=0.0,  # perfectly neutral
        strategist_action="LONG",
        strategist_confidence=0.85,
    )
    v = _strategist_model(f)
    # Neither side should have been amplified (both are 0.0)
    assert v["bull"] == 0.0
    assert v["bear"] == 0.0
