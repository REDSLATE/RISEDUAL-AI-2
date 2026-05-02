"""
Tests for the catalyst wiring additions:
* ``conviction_service._catalyst_conviction_delta`` — pure delta fn
* ``adversarial_core.apply_catalyst_context`` — narrative enrichment
* ``adversarial_core.catalyst_thesis_lines`` — structured lines

No Mongo required — both functions are pure.
"""
from __future__ import annotations

from services.adversarial_core import (
    AgentOutput,
    apply_catalyst_context,
    catalyst_thesis_lines,
)
from services.conviction_service import _catalyst_conviction_delta


def _bull(thesis: str = "rsi_oversold") -> AgentOutput:
    return AgentOutput(
        side="LONG",
        confidence=0.6,
        expected_r=1.2,
        thesis=thesis,
        invalidations=["rsi_climbs"],
    )


def _bear(thesis: str = "rsi_overbought") -> AgentOutput:
    return AgentOutput(
        side="SHORT_OR_REJECT",
        confidence=0.5,
        expected_r=1.0,
        thesis=thesis,
        invalidations=["breakout_confirm"],
    )


# ── _catalyst_conviction_delta ────────────────────────────────────


def test_delta_none_snapshot_no_data():
    delta, reason = _catalyst_conviction_delta(action="BUY", catalyst_snapshot=None)
    assert delta == 0.0
    assert reason == "NO_CATALYST_DATA"


def test_delta_high_shock_always_penalises():
    snap = {"news_shock": {"shock_state": "high", "sentiment_label": "bullish"}}
    delta, reason = _catalyst_conviction_delta(action="BUY", catalyst_snapshot=snap)
    assert delta == -0.10
    assert reason == "HIGH_NEWS_SHOCK_DE_RISK"


def test_delta_elevated_aligned_bullish_buy():
    snap = {"news_shock": {"shock_state": "elevated", "sentiment_label": "bullish"}}
    delta, reason = _catalyst_conviction_delta(action="BUY", catalyst_snapshot=snap)
    assert delta == 0.05
    assert reason == "ELEVATED_ALIGNED_CATALYST"


def test_delta_elevated_aligned_bearish_sell():
    snap = {"news_shock": {"shock_state": "elevated", "sentiment_label": "bearish"}}
    delta, reason = _catalyst_conviction_delta(action="SELL", catalyst_snapshot=snap)
    assert delta == 0.05


def test_delta_elevated_unaligned_buy_vs_bearish():
    snap = {"news_shock": {"shock_state": "elevated", "sentiment_label": "bearish"}}
    delta, reason = _catalyst_conviction_delta(action="BUY", catalyst_snapshot=snap)
    assert delta == -0.05
    assert reason == "ELEVATED_UNALIGNED_CATALYST"


def test_delta_elevated_unaligned_unknown_sentiment():
    snap = {"news_shock": {"shock_state": "elevated", "sentiment_label": "unknown"}}
    delta, reason = _catalyst_conviction_delta(action="BUY", catalyst_snapshot=snap)
    assert delta == -0.05


def test_delta_normal_state_is_zero():
    snap = {"news_shock": {"shock_state": "normal", "sentiment_label": "neutral"}}
    delta, reason = _catalyst_conviction_delta(action="BUY", catalyst_snapshot=snap)
    assert delta == 0.0
    assert reason == "CATALYST_NORMAL"


def test_delta_action_synonyms():
    """LONG and UP are bullish synonyms; SHORT / DOWN are bearish."""
    bull_snap = {"news_shock": {"shock_state": "elevated", "sentiment_label": "bullish"}}
    assert _catalyst_conviction_delta(action="LONG", catalyst_snapshot=bull_snap)[0] == 0.05
    assert _catalyst_conviction_delta(action="UP", catalyst_snapshot=bull_snap)[0] == 0.05
    bear_snap = {"news_shock": {"shock_state": "elevated", "sentiment_label": "bearish"}}
    assert _catalyst_conviction_delta(action="DOWN", catalyst_snapshot=bear_snap)[0] == 0.05
    assert _catalyst_conviction_delta(action="SHORT", catalyst_snapshot=bear_snap)[0] == 0.05


# ── apply_catalyst_context ────────────────────────────────────────


def test_apply_catalyst_context_no_snapshot_returns_inputs_unchanged():
    bull, bear = _bull("x"), _bear("y")
    out_bull, out_bear = apply_catalyst_context(bull, bear, None)
    assert out_bull.thesis == "x"
    assert out_bear.thesis == "y"
    assert out_bull.confidence == bull.confidence
    assert out_bear.expected_r == bear.expected_r


def test_apply_catalyst_context_bullish_sentiment_extends_bull_only():
    snap = {"news_shock": {"sentiment_label": "bullish"}}
    bull, bear = _bull("orig_bull"), _bear("orig_bear")
    out_bull, out_bear = apply_catalyst_context(bull, bear, snap)
    assert "bullish_catalyst_sentiment" in out_bull.thesis
    assert "bullish_catalyst_sentiment" not in out_bear.thesis


def test_apply_catalyst_context_bearish_sentiment_extends_bear_only():
    snap = {"news_shock": {"sentiment_label": "bearish"}}
    bull, bear = _bull("orig_bull"), _bear("orig_bear")
    out_bull, out_bear = apply_catalyst_context(bull, bear, snap)
    assert "bearish_catalyst_sentiment" in out_bear.thesis
    assert "bearish_catalyst_sentiment" not in out_bull.thesis


def test_apply_catalyst_context_elevated_shock_annotates_both():
    """Shock raises uncertainty on the directional bet regardless of
    which side the operator is on — both theses get the tag."""
    snap = {
        "news_shock": {
            "sentiment_label": "neutral",
            "shock_state": "elevated",
            "news_zscore": 3.2,
        }
    }
    bull, bear = _bull("orig_bull"), _bear("orig_bear")
    out_bull, out_bear = apply_catalyst_context(bull, bear, snap)
    assert "news_shock_elevated" in out_bull.thesis
    assert "news_shock_elevated" in out_bear.thesis
    assert "z3.2" in out_bull.thesis


def test_apply_catalyst_context_preserves_confidence_and_expected_r():
    """Additive-only rule — never moves numbers."""
    snap = {"news_shock": {"shock_state": "high", "sentiment_label": "bearish"}}
    bull, bear = _bull(), _bear()
    out_bull, out_bear = apply_catalyst_context(bull, bear, snap)
    assert out_bull.confidence == bull.confidence
    assert out_bull.expected_r == bull.expected_r
    assert out_bear.confidence == bear.confidence
    assert out_bear.expected_r == bear.expected_r


# ── catalyst_thesis_lines ────────────────────────────────────────


def test_thesis_lines_none_snapshot_returns_empty_lists():
    out = catalyst_thesis_lines(None)
    assert out == {"bull": [], "bear": [], "risk": []}


def test_thesis_lines_bullish_no_shock():
    snap = {"news_shock": {"sentiment_label": "bullish", "latest_headline": "AAPL beats"}}
    out = catalyst_thesis_lines(snap)
    assert len(out["bull"]) == 1
    assert "Bullish catalyst" in out["bull"][0]
    assert "AAPL beats" in out["bull"][0]
    assert out["bear"] == []
    assert out["risk"] == []


def test_thesis_lines_elevated_shock_adds_risk_line():
    snap = {
        "news_shock": {
            "sentiment_label": "neutral",
            "shock_state": "elevated",
            "news_zscore": 2.8,
        }
    }
    out = catalyst_thesis_lines(snap)
    assert len(out["risk"]) == 1
    assert "elevated" in out["risk"][0]
    assert "2.8" in out["risk"][0]


def test_thesis_lines_high_shock_bearish_sentiment():
    snap = {
        "news_shock": {
            "sentiment_label": "bearish",
            "latest_headline": "NVDA downgraded",
            "shock_state": "high",
            "news_zscore": 4.7,
        }
    }
    out = catalyst_thesis_lines(snap)
    assert len(out["bear"]) == 1
    assert "Bearish catalyst" in out["bear"][0]
    assert len(out["risk"]) == 1
    assert "high" in out["risk"][0]
    assert "4.7" in out["risk"][0]
