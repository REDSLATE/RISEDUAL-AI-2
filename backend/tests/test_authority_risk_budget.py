from datetime import datetime, timedelta, timezone

from services.authority_risk_budget import (
    AuthorityScope,
    AuthorityTier,
    RiskBudgetRequest,
    TrackRecord,
    apply_authority_scoped_risk_budget,
)


def future():
    return datetime.now(timezone.utc) + timedelta(days=1)


def base_scope(**overrides):
    data = dict(
        authority_id="auth-test-1",
        tier=AuthorityTier.STANDARD_LIVE,
        asset_type="crypto",
        engine="adversarial_commander",
        approved_by="operator-1",
        expires_at=future(),
        max_multiplier=0.75,
        max_notional=10_000.0,
        max_daily_loss=1_000.0,
        signature_hash="signed-authority-hash",
        countersignature_hash="counter-ok",
    )
    data.update(overrides)
    return AuthorityScope(**data)


def base_track(**overrides):
    data = dict(
        total_trades=100,
        win_rate=0.58,
        realized_pnl=500.0,
        max_drawdown=0.02,
        calibration_gap=0.03,
        loss_streak=0,
        veto_count=0,
        rejected_signal_count=0,
        last_updated=datetime.now(timezone.utc),
    )
    data.update(overrides)
    return TrackRecord(**data)


def test_hold_never_promoted_to_trade():
    req = RiskBudgetRequest(
        action="HOLD",
        base_notional=10_000,
        base_multiplier=1.0,
        requested_multiplier=1.25,
        authority=base_scope(),
        track_record=base_track(),
        daily_realized_loss=0,
        loosening_countersignature_hash="counter-ok",
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is False
    assert decision.final_notional == 0
    assert decision.final_multiplier == 0
    assert "hold_action_no_trade_promotion" in decision.reasons


def test_shadow_authority_cannot_execute_live():
    req = RiskBudgetRequest(
        action="BUY",
        base_notional=10_000,
        base_multiplier=1.0,
        authority=base_scope(tier=AuthorityTier.SHADOW),
        track_record=base_track(),
        daily_realized_loss=0,
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is False
    assert decision.final_multiplier == 0


def test_automatic_tightening_after_loss_streak():
    req = RiskBudgetRequest(
        action="BUY",
        base_notional=10_000,
        base_multiplier=0.75,
        authority=base_scope(),
        track_record=base_track(loss_streak=4),
        daily_realized_loss=0,
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is True
    assert decision.final_multiplier == 0.50
    assert decision.tightened is True
    assert "loss_streak_4_plus" in decision.reasons


def test_automatic_tightening_after_large_drawdown_hard_clamps():
    req = RiskBudgetRequest(
        action="SELL",
        base_notional=10_000,
        base_multiplier=0.75,
        authority=base_scope(),
        track_record=base_track(max_drawdown=0.21),
        daily_realized_loss=0,
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is False
    assert decision.final_multiplier == 0.0
    assert "drawdown_20_percent_hard_clamp" in decision.reasons


def test_loosening_denied_without_countersignature():
    req = RiskBudgetRequest(
        action="BUY",
        base_notional=10_000,
        base_multiplier=0.50,
        requested_multiplier=0.75,
        authority=base_scope(),
        track_record=base_track(),
        daily_realized_loss=0,
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is True
    assert decision.final_multiplier == 0.50
    assert decision.loosened is False
    assert "loosening_denied_missing_valid_countersignature" in decision.reasons


def test_loosening_allowed_with_valid_countersignature_but_still_scope_limited():
    req = RiskBudgetRequest(
        action="BUY",
        base_notional=10_000,
        base_multiplier=0.50,
        requested_multiplier=1.25,
        authority=base_scope(max_multiplier=0.75),
        track_record=base_track(),
        daily_realized_loss=0,
        loosening_countersignature_hash="counter-ok",
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is True
    assert decision.final_multiplier == 0.75
    assert decision.loosened is True
    assert "loosening_authorized_by_countersignature" in decision.reasons


def test_expired_authority_blocks_execution():
    req = RiskBudgetRequest(
        action="BUY",
        base_notional=10_000,
        base_multiplier=0.75,
        authority=base_scope(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
        track_record=base_track(),
        daily_realized_loss=0,
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is False
    assert decision.final_multiplier == 0.0
    assert "authority_expired" in decision.reasons


def test_daily_loss_limit_blocks_execution():
    req = RiskBudgetRequest(
        action="SELL",
        base_notional=10_000,
        base_multiplier=0.75,
        authority=base_scope(max_daily_loss=1_000),
        track_record=base_track(),
        daily_realized_loss=1_000,
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is False
    assert decision.final_multiplier == 0
    assert "daily_loss_limit_reached" in decision.reasons


def test_max_notional_cap_applies():
    req = RiskBudgetRequest(
        action="BUY",
        base_notional=100_000,
        base_multiplier=0.75,
        authority=base_scope(max_notional=5_000),
        track_record=base_track(),
        daily_realized_loss=0,
    )

    decision = apply_authority_scoped_risk_budget(req)

    assert decision.allowed is True
    assert decision.final_notional == 5_000
