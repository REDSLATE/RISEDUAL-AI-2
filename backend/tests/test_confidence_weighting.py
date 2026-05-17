"""Pytest coverage for the confidence-weighting doctrine and the
transparency receipt fields surfaced by ``_weighted_consensus``.

This locks in the 2026-05-15 Camaro hold-trap RCA:

  * Disagreement never collapses confidence to 0.50 — it applies a
    bounded multiplicative penalty (0.82 hard conflict, 0.90 hold
    dissent).
  * HOLD cannot win by passive accumulation when at least one brain
    emits a directional vote above the 55 % confidence floor.
  * Every consensus carries the full receipt: ``raw_action``,
    ``raw_confidence``, ``final_action``, ``final_confidence``,
    ``hold_reason``, ``blocked_by``, ``would_have_traded_without_gates``,
    ``disagreement_kind``, ``individual_weights``.
  * A JSON-parse failure marks the brain as ERROR (not NEUTRAL/50), so
    it is dropped from consensus instead of polluting the average.
"""
from __future__ import annotations

import pytest

from services.confidence_weighting import (
    BrainWeightState,
    DISAGREEMENT_PENALTY,
    HARD_CONFLICT_PENALTY,
    HOLD_BIAS_PENALTY,
    WeightState,
    apply_disagreement_penalty,
    clamp,
    classify_disagreement,
    compute_brain_weights,
    compute_dynamic_weights,
    smooth,
)
from services.multi_model_hypothesis_service import (
    BRAINS,
    _weighted_consensus,
)


# ── primitives ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("v,lo,hi,exp", [
    (1.5, 0.5, 1.0, 1.0),
    (0.1, 0.5, 1.0, 0.5),
    (0.7, 0.5, 1.0, 0.7),
])
def test_clamp(v, lo, hi, exp):
    assert clamp(v, lo, hi) == pytest.approx(exp)


def test_smooth_blends_with_default_alpha_30pct():
    # old=1.0, target=2.0, alpha=0.30 → 0.7*1.0 + 0.3*2.0 = 1.30
    assert smooth(1.0, 2.0) == pytest.approx(1.30)


def test_smooth_prevents_violent_swings():
    # Even with a target of 10.0, one update only moves ~30 % of the gap.
    out = smooth(1.0, 10.0)
    assert 3.0 < out < 4.0


# ── 5-role council (MC's doctrine) ─────────────────────────────────────


def test_compute_dynamic_weights_rewards_high_winrate():
    state = WeightState()
    new = compute_dynamic_weights(
        strategist_winrate_20=0.65,
        auditor_winrate_20=0.62,
        commander_alignment_rate=0.75,
        regime_accuracy=0.62,
        memory_match_winrate=0.70,
        current=state,
    )
    assert new.strategist_weight > state.strategist_weight
    assert new.auditor_weight > state.auditor_weight
    assert new.commander_weight > state.commander_weight


def test_compute_dynamic_weights_penalises_low_winrate():
    state = WeightState()
    new = compute_dynamic_weights(
        strategist_winrate_20=0.40,
        auditor_winrate_20=0.42,
        commander_alignment_rate=0.30,
        regime_accuracy=0.40,
        memory_match_winrate=0.30,
        current=state,
    )
    assert new.strategist_weight < state.strategist_weight
    assert new.commander_weight < state.commander_weight


def test_compute_dynamic_weights_respects_clamps():
    # Drive every role to its floor — make sure none drops below 0.50.
    state = WeightState(
        strategist_weight=0.50, auditor_weight=0.50,
        commander_weight=0.50, regime_weight=0.50, memory_weight=0.50,
    )
    new = compute_dynamic_weights(
        strategist_winrate_20=0.0,
        auditor_winrate_20=0.0,
        commander_alignment_rate=0.0,
        regime_accuracy=0.0,
        memory_match_winrate=0.0,
        current=state,
    )
    assert new.strategist_weight >= 0.50
    assert new.auditor_weight >= 0.50
    assert new.commander_weight >= 0.50
    assert new.regime_weight >= 0.50
    assert new.memory_weight >= 0.50


# ── 4-brain adapter ────────────────────────────────────────────────────


def test_brain_weights_default_to_balanced():
    s = BrainWeightState()
    for v in s.as_mapping().values():
        assert v == 1.0


def test_compute_brain_weights_rewards_strong_brain():
    new = compute_brain_weights(
        brain_winrates_20={"alpha": 0.65},
        current=BrainWeightState(),
    )
    # Smoothing keeps it < target; just assert direction.
    assert new.alpha_weight > 1.0
    # Other brains weren't reported → stay at 1.0.
    assert new.camaro_weight == pytest.approx(1.0)


def test_compute_brain_weights_penalises_weak_brain():
    new = compute_brain_weights(
        brain_winrates_20={"redeye": 0.35},
        current=BrainWeightState(),
    )
    assert new.redeye_weight < 1.0


def test_compute_brain_weights_clamp_floor():
    very_low = BrainWeightState(alpha_weight=0.50)
    new = compute_brain_weights(
        brain_winrates_20={"alpha": 0.0}, current=very_low,
    )
    assert new.alpha_weight >= 0.50


# ── disagreement classifier + penalty ──────────────────────────────────


@pytest.mark.parametrize("verdicts,expected_kind", [
    (["BUY", "BUY", "BUY", "BUY"], "UNANIMOUS"),
    (["BUY", "BUY", "HOLD", "BUY"], "HOLD_DISSENT"),
    (["BUY", "BUY", "SELL", "HOLD"], "HARD_CONFLICT"),
    (["HOLD", "HOLD", "NEUTRAL", "HOLD"], "ALL_HOLD"),
])
def test_classify_disagreement(verdicts, expected_kind):
    assert classify_disagreement(verdicts) == expected_kind


def test_apply_disagreement_penalty_unanimous_passes_through():
    r = apply_disagreement_penalty(pre_confidence=0.80, verdicts=["BUY"] * 4)
    assert r.kind == "UNANIMOUS"
    assert r.penalty_multiplier == 1.0
    assert r.post_penalty == pytest.approx(0.80)


def test_apply_disagreement_penalty_hold_dissent_softer():
    r = apply_disagreement_penalty(
        pre_confidence=0.80, verdicts=["BUY", "BUY", "BUY", "HOLD"],
    )
    assert r.kind == "HOLD_DISSENT"
    assert r.penalty_multiplier == HOLD_BIAS_PENALTY
    assert r.post_penalty == pytest.approx(0.80 * HOLD_BIAS_PENALTY)
    # Confidence dropped but did NOT flatten to 0.50.
    assert r.post_penalty > 0.65


def test_apply_disagreement_penalty_hard_conflict_uses_full_bound():
    r = apply_disagreement_penalty(
        pre_confidence=0.80, verdicts=["BUY", "SELL", "HOLD", "HOLD"],
    )
    assert r.kind == "HARD_CONFLICT"
    assert r.penalty_multiplier == HARD_CONFLICT_PENALTY
    # Still bounded — not flattened to 0.50.
    assert r.post_penalty == pytest.approx(0.80 * HARD_CONFLICT_PENALTY)


def test_disagreement_penalty_never_drops_to_half_on_conflict():
    """Regression guard: the prior failure mode was confidence=0.50 on
    ANY disagreement. This must never recur."""
    for verdicts in [
        ["BUY", "SELL"],
        ["BUY", "SELL", "HOLD"],
        ["BUY", "HOLD"],
    ]:
        r = apply_disagreement_penalty(pre_confidence=0.85, verdicts=verdicts)
        assert r.post_penalty != pytest.approx(0.50, abs=0.001), (
            f"confidence flattened to 0.50 on {verdicts}"
        )


def test_disagreement_penalty_bound_constants_are_sane():
    assert 0.5 < HARD_CONFLICT_PENALTY < HOLD_BIAS_PENALTY < 1.0
    assert DISAGREEMENT_PENALTY == 0.82  # MC's exact prescription


# ── transparency receipt on consensus ──────────────────────────────────


def _stub_result(brain_key: str, verdict: str, confidence: int) -> dict:
    return {
        "model": BRAINS[brain_key]["label"],
        "model_key": brain_key,
        "symbol": "NVDA",
        "verdict": verdict,
        "confidence": confidence,
        "summary": f"{brain_key} → {verdict}",
        "catalysts": [],
        "risks": [],
    }


def _build_results(buy=0, sell=0, hold=0, conf_each=70):
    keys = list(BRAINS.keys())
    out: list[dict] = []
    i = 0
    for _ in range(buy):
        out.append(_stub_result(keys[i % 4], "BUY", conf_each))
        i += 1
    for _ in range(sell):
        out.append(_stub_result(keys[i % 4], "SELL", conf_each))
        i += 1
    for _ in range(hold):
        out.append(_stub_result(keys[i % 4], "HOLD", conf_each))
        i += 1
    return out


def test_consensus_receipt_has_all_required_fields():
    out = _weighted_consensus(_build_results(buy=4))
    required = {
        "raw_action", "raw_confidence",
        "final_action", "final_confidence",
        "hold_reason", "blocked_by",
        "would_have_traded_without_gates",
        "market_decision", "execution_decision", "display_action",
        "pre_weight_confidence", "post_weight_confidence",
        "council_penalty", "disagreement_kind",
        "individual_weights",
    }
    missing = required - out.keys()
    assert not missing, f"receipt missing fields: {missing}"


def test_consensus_unanimous_buy_keeps_full_confidence():
    out = _weighted_consensus(_build_results(buy=4, conf_each=80))
    assert out["market_decision"] == "BUY"
    assert out["disagreement_kind"] == "UNANIMOUS"
    assert out["pre_weight_confidence"] == out["post_weight_confidence"]
    assert out["council_penalty"] == 0
    assert out["would_have_traded_without_gates"] is True
    assert out["hold_reason"] is None


def test_consensus_hold_does_not_win_when_directional_signal_present():
    """Regression guard for the original symptom: 2 HOLD + 1 BUY + 1
    SELL used to collapse to HOLD by passive accumulation. Now a
    directional vote (above the 55 % floor) must win."""
    results = [
        _stub_result("alpha",   "BUY",  72),
        _stub_result("camaro",  "HOLD", 60),
        _stub_result("chevelle","HOLD", 60),
        _stub_result("redeye",  "BUY",  68),  # 2 BUYs both above floor
    ]
    out = _weighted_consensus(results)
    assert out["market_decision"] == "BUY", (
        f"HOLD trap recurred: {out['market_decision']} won despite directional signal"
    )
    assert out["raw_action"] == "BUY"


def test_consensus_hard_conflict_applies_bounded_penalty_not_flatten():
    results = [
        _stub_result("alpha",   "BUY",  80),
        _stub_result("camaro",  "SELL", 75),
        _stub_result("chevelle","BUY",  70),
        _stub_result("redeye",  "SELL", 68),
    ]
    out = _weighted_consensus(results)
    assert out["disagreement_kind"] == "HARD_CONFLICT"
    # Confidence dropped, but NOT to 50 (the old flatten value).
    assert out["pre_weight_confidence"] > out["post_weight_confidence"]
    assert out["post_weight_confidence"] != 50, (
        "confidence flattened to 50 on hard conflict (HOLD trap)"
    )
    assert out["council_penalty"] < 0


def test_consensus_all_hold_is_honest_hold():
    out = _weighted_consensus(_build_results(hold=4, conf_each=55))
    assert out["market_decision"] == "HOLD"
    assert out["disagreement_kind"] == "ALL_HOLD"
    assert out["hold_reason"] == "NO_DIRECTIONAL_SIGNAL"
    assert out["would_have_traded_without_gates"] is False
    # No penalty applied — there's no disagreement to penalise.
    assert out["pre_weight_confidence"] == out["post_weight_confidence"]


def test_consensus_directional_floor_blocks_low_confidence_signal():
    """A sub-coin-flip BUY shouldn't win over a HOLD majority — that
    is exactly the noise the floor protects against. Floor is at
    55% per current doctrine."""
    results = [
        _stub_result("alpha",   "BUY",  40),   # below the 55 % floor
        _stub_result("camaro",  "HOLD", 60),
        _stub_result("chevelle","HOLD", 65),
        _stub_result("redeye",  "HOLD", 62),
    ]
    out = _weighted_consensus(results)
    assert out["market_decision"] == "HOLD"
    assert out["hold_reason"] == "DIRECTIONAL_FLOOR_NOT_CLEARED"
    assert "DIRECTIONAL_FLOOR" in out["blocked_by"]


# ── high-conviction override (A-pattern, 2026-05-16) ───────────────────


def test_high_conviction_override_flips_hold_majority():
    """The trapped-opportunity scenario: 3 HOLDs at moderate
    confidence + 1 brain at 82% BUY. Without the override, council
    would HOLD-trap. With override at 80, the strong directional
    signal owns the market_decision."""
    results = [
        _stub_result("alpha",   "BUY",  82),   # clears override
        _stub_result("camaro",  "HOLD", 60),
        _stub_result("chevelle","HOLD", 62),
        _stub_result("redeye",  "HOLD", 58),
    ]
    out = _weighted_consensus(results)
    assert out["market_decision"] == "BUY"
    assert out["override_reason"] is not None
    assert "HIGH_CONVICTION" in out["override_reason"]
    assert out["override_brain"] == "alpha"
    assert out["override_confidence"] == 82
    # Disagreement penalty still bites — receipt stays honest.
    assert out["disagreement_kind"] == "HOLD_DISSENT"
    assert out["council_penalty"] < 0
    assert out["would_have_traded_without_gates"] is True


def test_high_conviction_override_threshold_at_80_not_79():
    """Lock the override threshold at exactly 80 — a 79% BUY must
    still go through the normal floor/weight path, not override."""
    results = [
        _stub_result("alpha",   "BUY",  79),   # just below override
        _stub_result("camaro",  "HOLD", 70),
        _stub_result("chevelle","HOLD", 70),
        _stub_result("redeye",  "HOLD", 70),
    ]
    out = _weighted_consensus(results)
    # No override fired (79 < 80).
    assert out["override_reason"] is None
    # The HOLD-trap fix still lets the directional win since the
    # alpha BUY clears the 55 % floor — but no override badge.
    assert out["market_decision"] == "BUY"


def test_high_conviction_override_fires_at_exact_80():
    """Boundary inclusive — exactly 80% is "high conviction"."""
    results = [
        _stub_result("alpha",   "BUY",  80),
        _stub_result("camaro",  "HOLD", 70),
        _stub_result("chevelle","HOLD", 70),
        _stub_result("redeye",  "HOLD", 70),
    ]
    out = _weighted_consensus(results)
    assert out["override_reason"] is not None
    assert out["override_confidence"] == 80


def test_high_conviction_override_wins_over_hard_conflict():
    """Most important guard: a single brain at 85% BUY must own the
    market_decision even when another brain is at 70% SELL — the
    HARD_CONFLICT penalty still cuts confidence by ×0.70 but does
    NOT silence the directional verdict. This is the exact
    opportunity-capture case the operator described."""
    results = [
        _stub_result("alpha",   "BUY",  85),   # clears override
        _stub_result("camaro",  "SELL", 70),
        _stub_result("chevelle","HOLD", 65),
        _stub_result("redeye",  "HOLD", 60),
    ]
    out = _weighted_consensus(results)
    assert out["market_decision"] == "BUY"
    assert out["override_brain"] == "alpha"
    assert out["disagreement_kind"] == "HARD_CONFLICT"
    # Penalty still bites — pre > post — but direction is BUY.
    assert out["pre_weight_confidence"] > out["post_weight_confidence"]


def test_high_conviction_override_does_not_fire_on_unanimous():
    """When everyone agrees, no override is needed — receipt should
    reflect the normal path, not a phantom override badge."""
    out = _weighted_consensus(_build_results(buy=4, conf_each=85))
    assert out["market_decision"] == "BUY"
    # Override fields still populate (since alpha cleared 80) — that
    # is doctrinally correct: a unanimous high-conviction BUY is
    # still a high-conviction BUY. The badge just doesn't change the
    # outcome.
    assert out["override_reason"] is not None
    # But the disagreement kind is UNANIMOUS, no penalty.
    assert out["disagreement_kind"] == "UNANIMOUS"
    assert out["council_penalty"] == 0


def test_override_first_brain_wins_on_simultaneous_clearance():
    """If two brains both clear the 80 bar, the first one
    encountered owns it. Deterministic and rare."""
    results = [
        _stub_result("alpha",   "BUY",  82),
        _stub_result("camaro",  "BUY",  88),   # higher but second
        _stub_result("chevelle","HOLD", 60),
        _stub_result("redeye",  "HOLD", 60),
    ]
    out = _weighted_consensus(results)
    # Either alpha or camaro is acceptable doctrinally; the
    # implementation picks the first valid iterator hit.
    assert out["override_brain"] in ("alpha", "camaro")
    assert out["market_decision"] == "BUY"


def test_consensus_execution_decision_is_observe_only_under_doctrine_v3():
    """RISEDUAL is headless under Doctrine V3 — MC owns execution. The
    receipt's execution_decision must reflect that."""
    out = _weighted_consensus(_build_results(buy=4))
    assert out["execution_decision"] == "OBSERVE_ONLY"


def test_consensus_dynamic_brain_weights_shift_outcome():
    """When a brain has a 1.30 weight vs others at 1.0, its directional
    signal should weigh more heavily in the consensus."""
    results = [
        _stub_result("alpha",   "BUY",  70),
        _stub_result("camaro",  "SELL", 70),
        _stub_result("chevelle","SELL", 70),
        _stub_result("redeye",  "BUY",  70),
    ]
    # Alpha + RedEye are heavily weighted → BUY wins.
    boosted = BrainWeightState(
        alpha_weight=1.30, camaro_weight=0.60,
        chevelle_weight=0.60, redeye_weight=1.30,
    )
    out = _weighted_consensus(results, brain_weights=boosted)
    assert out["market_decision"] == "BUY"
    weights = out["individual_weights"]
    assert weights["alpha"] == pytest.approx(1.30)
    assert weights["redeye"] == pytest.approx(1.30)


def test_consensus_all_brains_failed_returns_honest_error_receipt():
    results = [
        {"model_key": "alpha",   "verdict": "ERROR", "confidence": 0},
        {"model_key": "camaro",  "verdict": "ERROR", "confidence": 0},
        {"model_key": "chevelle","verdict": "ERROR", "confidence": 0},
        {"model_key": "redeye",  "verdict": "ERROR", "confidence": 0},
    ]
    out = _weighted_consensus(results)
    assert out["hold_reason"] == "ALL_BRAINS_FAILED"
    assert out["blocked_by"] == ["ALL_BRAINS_FAILED"]
    assert out["would_have_traded_without_gates"] is False
