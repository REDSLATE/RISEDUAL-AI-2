"""
RISEDUAL — Auditor calibration veto

Part of the canonical IP contract (step 4). The Auditor's job is to
catch high-confidence signals that the model has historically been
overconfident about — i.e. when the rolling calibration gap
(predicted_prob − empirical_hit_rate) is wide enough that the model
should not be trusted at face value.

Why this is its own module rather than baked into adversarial:
  * Adversarial enforcement (Patent K) checks Bull-vs-Bear conviction
    spread. It says "are these two agents disagreeing enough that we
    have actual evidence?".
  * The Auditor checks "even if the agents agree, is the AGREEING
    confidence reliable historically?". A unanimously-confident model
    that's been consistently wrong is exactly the failure mode this
    catches.

Pure function — no DB, no network. Caller passes the rolling stats.
"""
from __future__ import annotations

__domain__ = "DTD"

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AuditorVerdict(str, Enum):
    PASS = "pass"           # No calibration concern
    CAUTION = "caution"     # Wide gap but not vetoed; downstream may shrink size
    VETO = "veto"           # Calibration too poor to trust — block


# Thresholds — tuned conservatively. Tighter gaps = more vetoes.
DEFAULT_VETO_GAP = 0.18      # 18 percentage-point gap kills the trade
DEFAULT_CAUTION_GAP = 0.10   # 10 pp triggers caution flag
DEFAULT_VETO_OVERCONF = 0.85 # If conf >= this AND gap > caution, veto


@dataclass(frozen=True)
class AuditorReview:
    verdict: AuditorVerdict
    calibration_gap: float
    rolling_accuracy: float
    signal_confidence: float
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "calibration_gap": self.calibration_gap,
            "rolling_accuracy": self.rolling_accuracy,
            "signal_confidence": self.signal_confidence,
            "reasons": list(self.reasons),
        }


def review_calibration(
    *,
    rolling_accuracy: float,
    calibration_gap: float,
    signal_confidence: float,
    veto_gap: float = DEFAULT_VETO_GAP,
    caution_gap: float = DEFAULT_CAUTION_GAP,
    veto_overconf: float = DEFAULT_VETO_OVERCONF,
) -> AuditorReview:
    """Veto / caution / pass on a single signal based on calibration.

    Parameters
    ----------
    rolling_accuracy:
        Observed hit rate over a recent window (e.g. last 100 closed
        trades). 0.55 = 55 % accuracy.
    calibration_gap:
        ``avg_predicted_confidence - rolling_accuracy``. Positive =
        overconfident; negative = underconfident. Either direction
        counts toward a wide gap, so we veto on absolute value.
    signal_confidence:
        Confidence the strategist is asking us to act on, in [0, 1].

    Returns
    -------
    AuditorReview with one of three verdicts. Defaults to PASS for
    benign inputs (zero gap, low confidence) so cold starts don't
    block legitimate trades.
    """
    abs_gap = abs(calibration_gap)
    reasons: list[str] = []

    # Hard veto: gap is wide on its own.
    if abs_gap >= veto_gap:
        reasons.append(f"calibration_gap_{abs_gap:.2f}>={veto_gap:.2f}")
        return AuditorReview(
            verdict=AuditorVerdict.VETO,
            calibration_gap=calibration_gap,
            rolling_accuracy=rolling_accuracy,
            signal_confidence=signal_confidence,
            reasons=reasons,
        )

    # Compound veto: medium gap + high signal confidence = stop.
    # Captures the "model is moderately overconfident AND asking us
    # to size up" failure mode.
    if abs_gap >= caution_gap and signal_confidence >= veto_overconf:
        reasons.append(
            f"compound_veto_gap_{abs_gap:.2f}+conf_{signal_confidence:.2f}",
        )
        return AuditorReview(
            verdict=AuditorVerdict.VETO,
            calibration_gap=calibration_gap,
            rolling_accuracy=rolling_accuracy,
            signal_confidence=signal_confidence,
            reasons=reasons,
        )

    # Caution band — let the trade through but flag it.
    if abs_gap >= caution_gap:
        reasons.append(f"caution_gap_{abs_gap:.2f}")
        return AuditorReview(
            verdict=AuditorVerdict.CAUTION,
            calibration_gap=calibration_gap,
            rolling_accuracy=rolling_accuracy,
            signal_confidence=signal_confidence,
            reasons=reasons,
        )

    return AuditorReview(
        verdict=AuditorVerdict.PASS,
        calibration_gap=calibration_gap,
        rolling_accuracy=rolling_accuracy,
        signal_confidence=signal_confidence,
        reasons=["pass"],
    )
