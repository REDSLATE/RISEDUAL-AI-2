"""Confidence Weighting — bounded, smoothed dynamic council weights.

This module is the **doctrine** layer for confidence math across the
RISEDUAL council. It exists because the prior consensus path had two
silent failure modes that flattened every result to HOLD/0.50:

  1. **Disagreement = neutralization.** When the council split, raw
     confidence was forced down to 0.50 (or worse, NEUTRAL on a
     JSON-parse error fallback). The brain looked "lazy" when it was
     actually being mathematically flattened.

  2. **No telemetry.** A blocked trade and an actual HOLD opinion
     produced indistinguishable receipts. There was no way to see that
     a brain *judged* BUY but *execution* blocked it.

The fix is bounded, smoothed weight updates driven by recent
performance, plus a strict separation of **market judgment** from
**execution judgment**:

    Market Judgment    : BUY / SELL / SHORT / COVER / HOLD
    Execution Judgment : ALLOW / BLOCK / SIZE_DOWN / OBSERVE_ONLY

Doctrine:
    Camaro is allowed to say
        "I judge this as BUY, but execution is blocked."
    Camaro is NOT allowed to say
        "I HOLD because gates blocked me."

This module exposes the generic 5-role council shape (strategist /
auditor / commander / regime / memory) used by Mission Control's
Camaro patch, plus a 4-brain adapter (`compute_brain_weights`) for the
RISEDUAL hypothesis council (Alpha / Camaro / Chevelle / RedEye).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# ── primitives ─────────────────────────────────────────────────────────


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def smooth(old: float, target: float, alpha: float = 0.30) -> float:
    """Prevent violent weight swings between epochs.

    A 30 % blend per update means weights need ~3 consecutive
    confirming epochs to fully move from one regime to another. That
    is intentional — we want adaptivity, not whiplash.
    """
    return (old * (1.0 - alpha)) + (target * alpha)


# ── 5-role council (Mission Control / Camaro doctrine module) ─────────


@dataclass
class WeightState:
    """Five-role council weight state, used by MC's Camaro patch.

    RISEDUAL keeps it here so the doctrine module is the single source
    of truth — both MC and RISEDUAL update weights with the same math.
    """

    strategist_weight: float = 1.0
    auditor_weight: float = 1.0
    commander_weight: float = 1.0
    regime_weight: float = 1.0
    memory_weight: float = 1.0


def compute_dynamic_weights(
    *,
    strategist_winrate_20: float,
    auditor_winrate_20: float,
    commander_alignment_rate: float,
    regime_accuracy: float,
    memory_match_winrate: float,
    current: WeightState,
) -> WeightState:
    """MC's exact doctrine — bounded, smoothed weight updates.

    Win-rate windows are deliberately short (n=20) so the council can
    sense regime change quickly. Clamps are asymmetric: penalties bite
    harder than rewards (a strategist that craters to 45 % winrate
    loses 0.20, but at 62 % winrate only gains 0.15) because the cost
    of overweighting a bad signal is greater than the cost of
    underweighting a good one.
    """
    strategist_target = 1.0
    auditor_target = 1.0
    commander_target = 1.0
    regime_target = 1.0
    memory_target = 1.0

    # Strategist performance
    if strategist_winrate_20 >= 0.62:
        strategist_target += 0.15
    elif strategist_winrate_20 <= 0.45:
        strategist_target -= 0.20

    # Auditor reliability
    if auditor_winrate_20 >= 0.60:
        auditor_target += 0.10
    elif auditor_winrate_20 <= 0.45:
        auditor_target -= 0.15

    # Commander disagreement quality
    if commander_alignment_rate >= 0.70:
        commander_target += 0.10
    elif commander_alignment_rate <= 0.40:
        commander_target -= 0.20

    # Regime model quality
    if regime_accuracy >= 0.60:
        regime_target += 0.10
    elif regime_accuracy <= 0.45:
        regime_target -= 0.10

    # Memory retrieval quality
    if memory_match_winrate >= 0.65:
        memory_target += 0.10
    elif memory_match_winrate <= 0.40:
        memory_target -= 0.15

    return WeightState(
        strategist_weight=clamp(
            smooth(current.strategist_weight, strategist_target),
            0.50, 1.35,
        ),
        auditor_weight=clamp(
            smooth(current.auditor_weight, auditor_target),
            0.50, 1.25,
        ),
        commander_weight=clamp(
            smooth(current.commander_weight, commander_target),
            0.50, 1.25,
        ),
        regime_weight=clamp(
            smooth(current.regime_weight, regime_target),
            0.50, 1.20,
        ),
        memory_weight=clamp(
            smooth(current.memory_weight, memory_target),
            0.50, 1.15,
        ),
    )


# ── 4-brain adapter (RISEDUAL hypothesis council) ──────────────────────


@dataclass
class BrainWeightState:
    """RISEDUAL's hypothesis council weight state — one per brain.

    Default to 1.0 each so a cold-start council is perfectly balanced.
    Updated each epoch from the brains' rolling 20-call hit rate (see
    ``compute_brain_weights``).
    """

    alpha_weight: float = 1.0
    camaro_weight: float = 1.0
    chevelle_weight: float = 1.0
    redeye_weight: float = 1.0

    def as_mapping(self) -> dict[str, float]:
        return {
            "alpha": self.alpha_weight,
            "camaro": self.camaro_weight,
            "chevelle": self.chevelle_weight,
            "redeye": self.redeye_weight,
        }


def compute_brain_weights(
    *,
    brain_winrates_20: Mapping[str, float],
    current: BrainWeightState,
) -> BrainWeightState:
    """Update brain weights from rolling 20-call winrate.

    ``brain_winrates_20`` maps brain keys (``alpha`` / ``camaro`` /
    ``chevelle`` / ``redeye``) to their fraction of correct directional
    calls over the most recent 20 resolved predictions. A missing key
    leaves that brain at its current weight.
    """

    def _target(winrate: float | None) -> float:
        if winrate is None:
            return 1.0
        if winrate >= 0.62:
            return 1.15
        if winrate >= 0.55:
            return 1.05
        if winrate <= 0.40:
            return 0.80
        if winrate <= 0.48:
            return 0.92
        return 1.0

    return BrainWeightState(
        alpha_weight=clamp(
            smooth(current.alpha_weight, _target(brain_winrates_20.get("alpha"))),
            0.50, 1.30,
        ),
        camaro_weight=clamp(
            smooth(current.camaro_weight, _target(brain_winrates_20.get("camaro"))),
            0.50, 1.30,
        ),
        chevelle_weight=clamp(
            smooth(current.chevelle_weight, _target(brain_winrates_20.get("chevelle"))),
            0.50, 1.30,
        ),
        redeye_weight=clamp(
            smooth(current.redeye_weight, _target(brain_winrates_20.get("redeye"))),
            0.50, 1.30,
        ),
    )


# ── disagreement handling ──────────────────────────────────────────────


DISAGREEMENT_PENALTY = 0.82  # multiplicative, bounded — MC doctrine
# Original, restored 2026-05-16: the experimental softer values
# (HOLD=0.95, HARD_CONFLICT=0.80) were rolled back in favor of the
# A-pattern override below — fix the trapped HIGH-CONVICTION signal,
# don't water down the penalty on genuine disagreement.
HOLD_BIAS_PENALTY = 0.90      # softer penalty when only HOLD is the disagreer
HARD_CONFLICT_PENALTY = 0.70  # both BUY AND SELL present — real conflict

# 2026-05-16: when a single brain emits a directional verdict at
# ≥ this confidence, that brain wins ``market_decision`` regardless
# of council split. The disagreement penalty still applies to
# ``final_confidence`` so the receipt stays honest about dissent;
# what the override changes is the *direction* the council chooses,
# not how confident it claims to be after the penalty.
#
# Why 80 (not 75 or 50): LLM calibration places real conviction in
# the 75-85% band — below that is "leaning" / noise. The bar is
# deliberately high so this is rare but unambiguous when it fires.
HIGH_CONVICTION_OVERRIDE = 80


@dataclass
class DisagreementResult:
    """Output of :func:`apply_disagreement_penalty`.

    ``kind`` is one of:
      * ``UNANIMOUS``       — every directional vote agrees; no penalty.
      * ``HOLD_DISSENT``    — directional consensus exists but at least
                              one brain HOLDs; softer penalty.
      * ``HARD_CONFLICT``   — both BUY and SELL are present; full
                              bounded penalty applied.
      * ``ALL_HOLD``        — no directional signal at all; confidence
                              passes through unchanged (HOLD is the
                              honest answer here).
    """

    kind: str
    penalty_multiplier: float
    pre_penalty: float
    post_penalty: float

    @property
    def delta(self) -> float:
        return round(self.post_penalty - self.pre_penalty, 4)


def classify_disagreement(verdicts: list[str]) -> str:
    norm = [(v or "").upper() for v in verdicts]
    has_buy = any(v == "BUY" for v in norm)
    has_sell = any(v == "SELL" for v in norm)
    has_hold = any(v in ("HOLD", "NEUTRAL") for v in norm)
    directional = [v for v in norm if v in ("BUY", "SELL")]
    if has_buy and has_sell:
        return "HARD_CONFLICT"
    if not directional:
        return "ALL_HOLD"
    if has_hold:
        return "HOLD_DISSENT"
    return "UNANIMOUS"


def apply_disagreement_penalty(
    *, pre_confidence: float, verdicts: list[str],
) -> DisagreementResult:
    """Bounded penalty for council disagreement — never flatten to 0.50.

    The old failure mode was ``confidence = 0.50`` on any disagreement,
    which collapsed the entire epistemic signal. Here the penalty is
    multiplicative and bounded, and the *kind* of disagreement is
    surfaced so the receipt stays honest.
    """
    kind = classify_disagreement(verdicts)
    if kind == "UNANIMOUS" or kind == "ALL_HOLD":
        penalty = 1.0
    elif kind == "HOLD_DISSENT":
        penalty = HOLD_BIAS_PENALTY
    else:  # HARD_CONFLICT
        penalty = HARD_CONFLICT_PENALTY
    post = max(0.0, min(1.0, pre_confidence * penalty))
    return DisagreementResult(
        kind=kind,
        penalty_multiplier=penalty,
        pre_penalty=round(pre_confidence, 4),
        post_penalty=round(post, 4),
    )


# ── public surface ─────────────────────────────────────────────────────


__all__ = [
    "BrainWeightState",
    "DISAGREEMENT_PENALTY",
    "DisagreementResult",
    "HARD_CONFLICT_PENALTY",
    "HIGH_CONVICTION_OVERRIDE",
    "HOLD_BIAS_PENALTY",
    "WeightState",
    "apply_disagreement_penalty",
    "clamp",
    "classify_disagreement",
    "compute_brain_weights",
    "compute_dynamic_weights",
    "smooth",
]
