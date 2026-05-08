from datetime import datetime, timedelta, timezone

from services.adversarial_enforcer import AgentDecision
from services.decision_pipeline_guard import run_guarded_decision_pipeline
from services.failure_mode_classifier import MarketTelemetry, ModelTelemetry
from services.proof_chain import InMemoryProofChainStore
from services.authority_risk_budget import (
    AuthorityScope,
    AuthorityTier,
    TrackRecord,
    RiskBudgetRequest,
)


def scope():
    return AuthorityScope(
        authority_id="auth-1",
        tier=AuthorityTier.STANDARD_LIVE,
        asset_type="crypto",
        engine="adversarial_commander",
        approved_by="operator",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        max_multiplier=0.75,
        max_notional=10_000,
        max_daily_loss=1_000,
        signature_hash="authority-signed",
        countersignature_hash="counter-ok",
    )


def track():
    return TrackRecord(
        total_trades=100,
        win_rate=0.58,
        realized_pnl=500,
        max_drawdown=0.02,
        calibration_gap=0.03,
        loss_streak=0,
        veto_count=0,
        rejected_signal_count=0,
        last_updated=datetime.now(timezone.utc),
    )


def market():
    return MarketTelemetry(
        symbol="BTCUSD",
        asset_type="crypto",
        atr_pct=0.02,
        atr_pct_baseline=0.02,
        volume_zscore=0.5,
        spread_bps=10,
        spread_bps_baseline=10,
    )


def model():
    return ModelTelemetry(
        calibration_gap=0.03,
        prediction_entropy=0.40,
        confidence=0.75,
        confidence_baseline=0.65,
        disagreement_score=0.30,
        recent_error_rate=0.25,
        loss_streak=0,
        max_drawdown=0.02,
    )


def test_guarded_pipeline_allows_clean_trade_and_logs_proofs():
    store = InMemoryProofChainStore()

    req = RiskBudgetRequest(
        action="BUY",
        base_notional=5_000,
        base_multiplier=0.75,
        authority=scope(),
        track_record=track(),
        daily_realized_loss=0,
    )

    result = run_guarded_decision_pipeline(
        entity_id="trade-1",
        bull=AgentDecision(name="bull", action="BUY", confidence=0.90),
        bear=AgentDecision(name="bear", action="SELL", confidence=0.20),
        commander=None,
        market=market(),
        model=model(),
        risk_request=req,
        proof_store=store,
    )

    assert result["allow"] is True
    assert result["action"] == "BUY"
    assert result["notional"] > 0
    assert len(result["proof_hashes"]) == 3


def test_guarded_pipeline_blocks_low_conviction():
    store = InMemoryProofChainStore()

    req = RiskBudgetRequest(
        action="BUY",
        base_notional=5_000,
        base_multiplier=0.75,
        authority=scope(),
        track_record=track(),
        daily_realized_loss=0,
    )

    result = run_guarded_decision_pipeline(
        entity_id="trade-2",
        bull=AgentDecision(name="bull", action="BUY", confidence=0.71),
        bear=AgentDecision(name="bear", action="BUY", confidence=0.70),
        commander=None,
        market=market(),
        model=model(),
        risk_request=req,
        proof_store=store,
    )

    assert result["allow"] is False
    assert result["action"] == "HOLD"
    assert "low_dissent_no_conviction" in result["reasons"]
    assert len(result["proof_hashes"]) == 1
