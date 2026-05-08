from __future__ import annotations

from scripts.compression_ci_gate import evaluate_gate
from services.regime_performance_tracker import ResolvedTradeRow


def row(confidence: float, win: bool, family: str = "normal") -> ResolvedTradeRow:
    return ResolvedTradeRow(
        symbol="SPY",
        date="2026-01-01",
        action="BUY",
        confidence=confidence,
        win=win,
        pnl_pct=1.0 if win else -1.0,
        regime_label="normal",
        vix_level="normal",
        yield_curve="normal",
        liquidity="normal",
        market_event="none",
        event_family=family,
        is_crisis=False,
        regime_id=f"normal:{family}",
    )


def many(confidence: float, wins: int, losses: int, family: str = "normal"):
    return (
        [row(confidence, True, family) for _ in range(wins)]
        + [row(confidence, False, family) for _ in range(losses)]
    )


def test_inconclusive_when_baseline_too_small():
    verdict = evaluate_gate(
        baseline=many(0.7, 10, 0),
        candidate=many(0.7, 30, 0),
    )

    assert verdict.inconclusive is True
    assert verdict.ok is False
    assert "baseline has only" in verdict.breaches[0]


def test_inconclusive_when_candidate_too_small():
    verdict = evaluate_gate(
        baseline=many(0.7, 30, 0),
        candidate=many(0.7, 10, 0),
    )

    assert verdict.inconclusive is True
    assert verdict.ok is False
    assert "candidate has only" in verdict.breaches[0]


def test_passes_when_weighted_gap_within_tolerance():
    # Baseline: 30 rows, conf 0.60, 18W/12L → win_rate 0.600, gap 0.000
    # Candidate: 30 rows, conf 0.61, 18W/12L → win_rate 0.600, gap 0.010
    # Weighted-aggregate delta = +0.010 < 0.02 → PASS
    verdict = evaluate_gate(
        baseline=many(0.60, 18, 12, "normal"),
        candidate=many(0.61, 18, 12, "normal"),
    )

    assert verdict.inconclusive is False
    assert verdict.ok is True
    assert verdict.breaches == []


def test_fails_when_event_family_calibration_gap_worsens():
    verdict = evaluate_gate(
        baseline=many(0.7, 21, 9, "ai_bubble"),
        candidate=many(0.9, 18, 12, "ai_bubble"),
    )

    assert verdict.inconclusive is False
    assert verdict.ok is False
    assert any("calibration_gap[ai_bubble]" in b for b in verdict.breaches)


def test_fails_when_weighted_avg_calibration_gap_worsens():
    verdict = evaluate_gate(
        baseline=many(0.7, 21, 9, "normal"),
        candidate=many(0.95, 18, 12, "normal"),
    )

    assert verdict.inconclusive is False
    assert verdict.ok is False
    assert any("weighted_avg_calibration_gap" in b for b in verdict.breaches)


def test_no_cross_family_monotonicity_gate_exists():
    baseline = (
        many(0.50, 3, 7, "normal")
        + many(0.90, 9, 1, "ai_bubble")
        + many(0.70, 8, 2, "rate_hike")
    )

    candidate = (
        many(0.50, 7, 3, "normal")
        + many(0.90, 3, 7, "ai_bubble")
        + many(0.70, 8, 2, "rate_hike")
    )

    verdict = evaluate_gate(baseline, candidate)

    assert all("monotonicity" not in b for b in verdict.breaches)


def test_small_family_buckets_ignored_under_floor():
    """Per-family floor protects against tiny-bucket false positives."""
    # Baseline + candidate each have 30 'normal' rows (clears global floor)
    # PLUS a tiny 'ai_bubble' bucket of 5 rows where candidate is wildly
    # miscalibrated. The per-family check must SKIP the bubble bucket
    # (under MIN_FAMILY_SAMPLES=10) and the gate must PASS.
    baseline = many(0.6, 18, 12, "normal") + many(0.5, 5, 0, "ai_bubble")
    candidate = (
        many(0.6, 18, 12, "normal") + many(0.95, 0, 5, "ai_bubble")
    )

    verdict = evaluate_gate(baseline, candidate)

    assert verdict.inconclusive is False
    assert verdict.ok is True
    assert all("ai_bubble" not in b for b in verdict.breaches)
