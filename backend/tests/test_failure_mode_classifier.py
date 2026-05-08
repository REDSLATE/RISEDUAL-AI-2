from services.failure_mode_classifier import (
    FailureMode,
    MarketTelemetry,
    ModelTelemetry,
    classify_failure_mode,
    apply_failure_mode_to_multiplier,
)


def normal_market():
    return MarketTelemetry(
        symbol="BTCUSD",
        asset_type="crypto",
        atr_pct=0.02,
        atr_pct_baseline=0.02,
        volume_zscore=0.5,
        spread_bps=10,
        spread_bps_baseline=10,
    )


def normal_model():
    return ModelTelemetry(
        calibration_gap=0.03,
        prediction_entropy=0.40,
        confidence=0.70,
        confidence_baseline=0.65,
        disagreement_score=0.20,
        recent_error_rate=0.30,
        loss_streak=0,
        max_drawdown=0.02,
    )


def test_normal_conditions():
    result = classify_failure_mode(normal_market(), normal_model())

    assert result.mode == FailureMode.NORMAL
    assert result.block_trade is False
    assert result.risk_multiplier_cap == 1.0


def test_liquidity_trap_blocks_trade():
    market = normal_market()
    market = MarketTelemetry(
        symbol=market.symbol,
        asset_type=market.asset_type,
        atr_pct=market.atr_pct,
        atr_pct_baseline=market.atr_pct_baseline,
        volume_zscore=market.volume_zscore,
        spread_bps=100,
        spread_bps_baseline=10,
    )

    result = classify_failure_mode(market, normal_model())

    assert result.mode == FailureMode.LIQUIDITY_TRAP
    assert result.block_trade is True

    multiplier, reasons = apply_failure_mode_to_multiplier(1.0, result)
    assert multiplier == 0.0
    assert "failure_mode_blocked_trade" in reasons


def test_calibration_failure_tightens():
    model = normal_model()
    model = ModelTelemetry(
        calibration_gap=0.20,
        prediction_entropy=model.prediction_entropy,
        confidence=model.confidence,
        confidence_baseline=model.confidence_baseline,
        disagreement_score=model.disagreement_score,
        recent_error_rate=model.recent_error_rate,
        loss_streak=model.loss_streak,
        max_drawdown=model.max_drawdown,
    )

    result = classify_failure_mode(normal_market(), model)

    assert result.mode == FailureMode.CALIBRATION_FAILURE
    assert result.risk_multiplier_cap == 0.50

    multiplier, reasons = apply_failure_mode_to_multiplier(1.0, result)
    assert multiplier == 0.50
    assert "failure_mode_tightened_risk" in reasons


def test_data_quality_failure_blocks():
    market = MarketTelemetry(
        symbol="BTCUSD",
        asset_type="crypto",
        atr_pct=0.02,
        atr_pct_baseline=0.02,
        volume_zscore=0.5,
        spread_bps=10,
        spread_bps_baseline=10,
        data_missing_ratio=0.25,
    )

    result = classify_failure_mode(market, normal_model())

    assert result.mode == FailureMode.DATA_QUALITY_FAILURE
    assert result.block_trade is True
    assert result.risk_multiplier_cap == 0.0
