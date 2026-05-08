"""
Tests for the NEWS_SHOCK catalyst layer.
"""
from __future__ import annotations

from services.catalyst_risk_gate import catalyst_risk_gate
from services.catalyst_snapshot_service import event_risk_from_news_shock
from services.news_shock_service import (
    MIN_BASELINE_SAMPLES,
    classify_news_shock,
    classify_sentiment,
    compute_news_volume,
    compute_sentiment_score,
    zscore,
)


# ── zscore ────────────────────────────────────────────────────────


def test_zscore_not_ready_under_min_samples():
    """Fewer than MIN_BASELINE_SAMPLES → None. Fresh-deploy symbols
    are silent until we have enough history."""
    assert zscore(10, [1, 2, 3]) is None


def test_zscore_computes_when_ready():
    baseline = [1.0] * 10 + [2.0] * 10
    assert len(baseline) >= MIN_BASELINE_SAMPLES
    out = zscore(5.0, baseline)
    assert out is not None
    assert out > 2.0


def test_zscore_zero_stddev_returns_none():
    """Flat baseline — every value identical — must degrade to None
    rather than division-by-zero."""
    assert zscore(5.0, [1.0] * 30) is None


# ── sentiment classifier ─────────────────────────────────────────


def test_sentiment_classification():
    assert classify_sentiment(0.5) == "bullish"
    assert classify_sentiment(-0.5) == "bearish"
    assert classify_sentiment(0.1) == "neutral"
    assert classify_sentiment(None) == "unknown"


# ── news shock classifier ────────────────────────────────────────


def test_news_shock_classification():
    assert classify_news_shock(None, 0.8) == "not_ready"
    assert classify_news_shock(1.0, 0.8) == "normal"
    assert classify_news_shock(3.0, 0.1) == "elevated"
    assert classify_news_shock(4.5, 0.8) == "high"


def test_news_shock_high_requires_both_volume_and_sentiment():
    """z≥4.0 alone is NOT high — without a sentiment signal it's
    just elevated volume chatter."""
    assert classify_news_shock(5.0, 0.10) == "elevated"


# ── aggregation helpers ───────────────────────────────────────────


def test_compute_news_volume_is_just_length():
    assert compute_news_volume([]) == 0
    assert compute_news_volume([{}, {}, {}]) == 3


def test_compute_sentiment_score_averages_signed():
    events = [
        {"sentiment_score": 0.6},
        {"sentiment_score": -0.4},
        {"sentiment_score": 0.0},
    ]
    assert compute_sentiment_score(events) == round((0.6 - 0.4 + 0.0) / 3, 4)


def test_compute_sentiment_score_falls_back_to_av_field_name():
    """AV emits ``overall_sentiment_score`` — must be picked up."""
    events = [
        {"overall_sentiment_score": 0.6},
        {"overall_sentiment_score": 0.4},
    ]
    assert compute_sentiment_score(events) == 0.5


def test_compute_sentiment_score_none_when_no_scored_events():
    assert compute_sentiment_score([]) is None
    assert compute_sentiment_score([{"headline": "no score here"}]) is None


# ── event_risk_from_news_shock projection ────────────────────────


def test_event_risk_projection():
    assert event_risk_from_news_shock("high", "bullish") == "restricted"
    assert event_risk_from_news_shock("elevated", "bullish") == "elevated"
    assert event_risk_from_news_shock("normal", "neutral") == "normal"
    assert event_risk_from_news_shock("not_ready", "unknown") == "normal"


# ── catalyst_risk_gate ────────────────────────────────────────────


def test_catalyst_gate_no_data_allows():
    out = catalyst_risk_gate({"action": "BUY"}, None)
    assert out["allow"] is True
    assert out["size_multiplier"] == 1.0
    assert out["reason"] == "NO_CATALYST_DATA"


def test_catalyst_gate_restricted_blocks():
    snapshot = {
        "event_risk": "restricted",
        "news_shock": {
            "shock_state": "high", "news_zscore": 5.0, "sentiment_label": "bearish",
        },
    }
    out = catalyst_risk_gate({"action": "BUY"}, snapshot)
    assert out["allow"] is False
    assert out["size_multiplier"] == 0.0
    assert out["reason"] == "CATALYST_RESTRICTED_NEWS_SHOCK"


def test_catalyst_gate_restricted_blocks_short_side_too():
    snapshot = {
        "event_risk": "restricted",
        "news_shock": {"shock_state": "high", "sentiment_label": "bullish"},
    }
    out = catalyst_risk_gate({"action": "SELL"}, snapshot)
    assert out["allow"] is False


def test_catalyst_gate_elevated_aligned_reduces_less():
    snapshot = {
        "event_risk": "elevated",
        "news_shock": {
            "shock_state": "elevated", "news_zscore": 3.0, "sentiment_label": "bullish",
        },
    }
    out = catalyst_risk_gate({"action": "BUY"}, snapshot)
    assert out["allow"] is True
    assert out["size_multiplier"] == 0.75
    assert out["reason"] == "CATALYST_ELEVATED_ALIGNED"


def test_catalyst_gate_elevated_unaligned_reduces_more():
    snapshot = {
        "event_risk": "elevated",
        "news_shock": {
            "shock_state": "elevated", "news_zscore": 3.0, "sentiment_label": "bearish",
        },
    }
    out = catalyst_risk_gate({"action": "BUY"}, snapshot)
    assert out["allow"] is True
    assert out["size_multiplier"] == 0.5
    assert out["reason"] == "CATALYST_ELEVATED_UNALIGNED_SIZE_REDUCED"


def test_catalyst_gate_normal_no_op():
    snapshot = {
        "event_risk": "normal",
        "news_shock": {"shock_state": "normal", "sentiment_label": "neutral"},
    }
    out = catalyst_risk_gate({"action": "BUY"}, snapshot)
    assert out["size_multiplier"] == 1.0
    assert out["reason"] == "CATALYST_NORMAL"


def test_catalyst_gate_elevated_unknown_sentiment_unaligned():
    """Unknown sentiment can never align with an action → treat as
    the conservative 0.5 reduction path."""
    snapshot = {
        "event_risk": "elevated",
        "news_shock": {"shock_state": "elevated", "sentiment_label": "unknown"},
    }
    out = catalyst_risk_gate({"action": "BUY"}, snapshot)
    assert out["size_multiplier"] == 0.5
