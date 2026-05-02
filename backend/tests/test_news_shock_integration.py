"""
Pin that the NEWS_SHOCK branch of Patent M fires once the news-volume
z-score crosses the trigger — same statistical pattern as
volume_zscore, now wired to Benzinga-sourced counts.

Pure function tests — no DB, no Benzinga.
"""
from __future__ import annotations

from services.failure_mode_classifier import (
    FailureMode,
    MarketTelemetry,
    ModelTelemetry,
    classify_failure_mode,
)


def _healthy_model() -> ModelTelemetry:
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


def _market_with_news(
    sentiment_abs: float, news_zscore: float,
) -> MarketTelemetry:
    return MarketTelemetry(
        symbol="TEST",
        asset_type="equity",
        atr_pct=1.0,
        atr_pct_baseline=1.0,
        volume_zscore=0.0,
        spread_bps=20.0,
        spread_bps_baseline=20.0,
        news_sentiment_abs=sentiment_abs,
        news_volume_zscore=news_zscore,
    )


def test_news_shock_fires_when_sentiment_and_volume_both_spike():
    """Patent M requires BOTH a high sentiment magnitude AND a high
    news-volume z-score — a headline alone without volume, or volume
    without sentiment, isn't enough. News shocks are visually defined
    as 'a lot of noise that aligns directionally.'"""
    market = _market_with_news(sentiment_abs=0.85, news_zscore=3.5)
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NEWS_SHOCK
    assert "news_sentiment_and_volume_shock" in result.reasons


def test_news_shock_does_not_fire_on_volume_alone():
    """High volume + low sentiment = lots of neutral chatter. Not a shock."""
    market = _market_with_news(sentiment_abs=0.30, news_zscore=5.0)
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NORMAL


def test_news_shock_does_not_fire_on_sentiment_alone():
    """Single strongly-sentimental article, no broader flow. Not a shock."""
    market = _market_with_news(sentiment_abs=0.95, news_zscore=0.5)
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NORMAL


def test_news_shock_defaults_dormant_when_feeder_not_populated():
    """A freshly-deployed symbol with no baseline reads
    news_volume_zscore=0.0 — classifier must stay dormant, NOT block."""
    market = _market_with_news(sentiment_abs=0.0, news_zscore=0.0)
    result = classify_failure_mode(market, _healthy_model())
    assert result.mode == FailureMode.NORMAL
