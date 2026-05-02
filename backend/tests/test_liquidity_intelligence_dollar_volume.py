"""
Tests for the dollar-volume starvation branch of Patent M LIQUIDITY_TRAP.

Verifies:
* Zero-baseline guard — legacy callers that omit `dollar_volume*` fields
  don't accidentally trip the trap.
* Below-trigger ratio fires with the right metadata.
* Ratio at / above trigger does NOT fire.
* Dollar-volume trap stacks with spread-based trap (both in candidates).
* Confidence scales with deficit depth.
"""
from __future__ import annotations

from services.failure_mode_classifier import (
    FailureMode,
    FailureModeConfig,
    MarketTelemetry,
    ModelTelemetry,
    classify_failure_mode,
)


def _healthy_model() -> ModelTelemetry:
    """Non-failure-mode model state; keeps market-branch tests clean."""
    return ModelTelemetry(
        calibration_gap=0.0,
        prediction_entropy=0.3,
        confidence=0.75,
        confidence_baseline=0.70,
        disagreement_score=0.2,
        recent_error_rate=0.3,
        loss_streak=0,
        max_drawdown=0.02,
    )


def _quiet_market(**overrides) -> MarketTelemetry:
    """Baseline market telemetry with all gates closed."""
    base = dict(
        symbol="TEST",
        asset_type="equity",
        atr_pct=1.0,
        atr_pct_baseline=1.0,
        volume_zscore=0.0,
        spread_bps=20.0,
        spread_bps_baseline=20.0,
    )
    base.update(overrides)
    return MarketTelemetry(**base)


# ── Default / legacy call safety ──────────────────────────────────


def test_legacy_callers_without_dollar_volume_do_not_trigger_trap():
    """Existing callers that never set `dollar_volume*` must retain the
    pre-extension behaviour — no dollar-volume branch firing."""
    market = _quiet_market()  # no dv fields → both default to 0.0
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NORMAL


def test_zero_baseline_does_not_fire_even_with_live_volume():
    """Baseline = 0 (no history yet) must NEVER trip the trap — we'd
    be dividing by something effectively undefined."""
    market = _quiet_market(dollar_volume=1000.0, dollar_volume_baseline=0.0)
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NORMAL


def test_zero_current_with_positive_baseline_does_not_fire():
    """Symmetry check: a missing/zero current reading also stays quiet.
    The trap is about a MEASURED deficit, not missing data."""
    market = _quiet_market(dollar_volume=0.0, dollar_volume_baseline=1_000_000.0)
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NORMAL


# ── Trigger behaviour ────────────────────────────────────────────


def test_severe_dollar_volume_deficit_trips_liquidity_trap():
    """10% of baseline → ratio 0.1 < 0.3 trigger → LIQUIDITY_TRAP."""
    market = _quiet_market(
        dollar_volume=100_000.0,
        dollar_volume_baseline=1_000_000.0,
    )
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.LIQUIDITY_TRAP
    assert result.block_trade is True
    assert result.risk_multiplier_cap == 0.25
    assert "dollar_volume_starvation" in result.reasons
    assert result.metadata["dollar_volume_ratio"] == 0.1


def test_ratio_at_trigger_edge_does_not_fire():
    """ratio == 0.30 → NOT < 0.30 → no fire. Pins the boundary."""
    market = _quiet_market(
        dollar_volume=300_000.0,
        dollar_volume_baseline=1_000_000.0,
    )
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NORMAL


def test_healthy_dollar_volume_does_not_fire():
    """Baseline-level dollar volume — obviously no trap."""
    market = _quiet_market(
        dollar_volume=950_000.0,
        dollar_volume_baseline=1_000_000.0,
    )
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NORMAL


def test_confidence_scales_with_deficit_depth():
    """A 99% collapse should produce higher confidence than a 50%
    collapse above trigger."""
    severe = _quiet_market(dollar_volume=10_000.0, dollar_volume_baseline=1_000_000.0)
    mild = _quiet_market(dollar_volume=250_000.0, dollar_volume_baseline=1_000_000.0)

    r_severe = classify_failure_mode(severe, _healthy_model())
    r_mild = classify_failure_mode(mild, _healthy_model())

    assert r_severe.mode == FailureMode.LIQUIDITY_TRAP
    assert r_mild.mode == FailureMode.LIQUIDITY_TRAP
    assert r_severe.confidence > r_mild.confidence


# ── Interaction with the existing spread-based trap ──────────────


def test_dollar_volume_trap_alone_fires_independently_of_spread():
    """Spread healthy, dollar volume collapsed → still triggers."""
    market = _quiet_market(
        spread_bps=20.0,
        spread_bps_baseline=20.0,
        dollar_volume=50_000.0,
        dollar_volume_baseline=1_000_000.0,
    )
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.LIQUIDITY_TRAP
    assert "dollar_volume_starvation" in result.reasons


def test_spread_trap_alone_still_fires_without_dollar_volume_data():
    """Back-compat: spread-widening trap keeps working when
    dollar_volume is absent (legacy code path)."""
    market = _quiet_market(
        spread_bps=150.0,  # well above 75 bps absolute trigger
        spread_bps_baseline=20.0,
    )
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.LIQUIDITY_TRAP
    assert "spread_widening_liquidity_trap" in result.reasons


# ── Custom config override ───────────────────────────────────────


def test_config_override_changes_trigger_threshold():
    """A stricter config (0.50) should trip on a mild deficit that the
    default (0.30) would let through."""
    market = _quiet_market(
        dollar_volume=400_000.0,
        dollar_volume_baseline=1_000_000.0,  # ratio 0.4
    )
    default_cfg = FailureModeConfig()
    strict_cfg = FailureModeConfig(dollar_volume_ratio_trigger=0.50)

    r_default = classify_failure_mode(market, _healthy_model(), default_cfg)
    r_strict = classify_failure_mode(market, _healthy_model(), strict_cfg)

    assert r_default.mode == FailureMode.NORMAL
    assert r_strict.mode == FailureMode.LIQUIDITY_TRAP
