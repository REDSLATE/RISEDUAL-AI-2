"""Feature-health weighting for the Strategist.

Live feature degradation should clamp Strategist confidence:

    effective_confidence = raw_confidence * feature_health_score

If health < ``HOLD_THRESHOLD`` (default 0.3), force a NO_TRADE
verdict regardless of model output. The score lands in the verdict
diagnostics so the heartbeat and decision-log receipts can report
"why was this clamped".

The score is computed from the FeatureFrame using:
  * perception sub-score confidences (6 dimensions)
  * presence of critical market fields (data_lag_ms, broker_uptime,
    error_rate)
  * data freshness — penalised when ``data_lag_ms`` is large

NEVER raises. Returns a float in ``[0.0, 1.0]``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from services.ml.contracts import FeatureFrame


# Hard floor: below this level the lane MUST hold.
def _hold_threshold() -> float:
    try:
        return float(os.getenv("FEATURE_HEALTH_HOLD_THRESHOLD", "0.3"))
    except (TypeError, ValueError):
        return 0.3


_PERCEPTION_KEYS = (
    "event_shock", "regime", "drawdown",
    "liquidity", "system_health", "pacing",
)


def compute_feature_health(frame: FeatureFrame) -> Tuple[float, Dict[str, Any]]:
    """Return ``(score, diagnostics)``.

    Score components (each in [0, 1], averaged with weights):

      * perception_confidence: mean of the 6 perception confidences
        (1.0 = all sub-models confident; 0.0 = no signal).
      * market_completeness: fraction of expected market fields with
        usable, non-zero values.
      * data_freshness: 1.0 when ``data_lag_ms`` < 500, decays
        linearly to 0.0 at 30s lag.

    All three are folded into a single score; missing components
    contribute 0.0 (the caller decides when to HOLD).
    """
    diagnostics: Dict[str, Any] = {}

    # ── 1. Perception confidence ──
    scores = (frame.perception or {}).get("scores") or {}
    confs = []
    for k in _PERCEPTION_KEYS:
        c = (scores.get(k) or {}).get("confidence")
        if c is None:
            confs.append(0.0)
        else:
            try:
                confs.append(max(0.0, min(1.0, float(c))))
            except (TypeError, ValueError):
                confs.append(0.0)
    perception_conf = sum(confs) / len(confs) if confs else 0.0
    diagnostics["perception_confidence"] = perception_conf
    diagnostics["perception_confidences"] = {
        k: confs[i] for i, k in enumerate(_PERCEPTION_KEYS)
    }

    # ── 2. Market completeness ──
    expected_fields = (
        "broker_uptime", "data_lag_ms", "error_rate",
        "spread_bps", "vix",
    )
    market = frame.market or {}
    present = 0
    for k in expected_fields:
        v = market.get(k)
        if v is None:
            continue
        try:
            float(v)  # finite check
            present += 1
        except (TypeError, ValueError):
            pass
    completeness = present / len(expected_fields)
    diagnostics["market_completeness"] = completeness
    diagnostics["market_fields_present"] = present
    diagnostics["market_fields_expected"] = len(expected_fields)

    # ── 3. Data freshness ──
    lag = market.get("data_lag_ms")
    try:
        lag_val = float(lag) if lag is not None else None
    except (TypeError, ValueError):
        lag_val = None
    if lag_val is None:
        freshness = 0.5  # unknown -> neutral
    elif lag_val <= 500:
        freshness = 1.0
    elif lag_val >= 30_000:
        freshness = 0.0
    else:
        freshness = max(0.0, 1.0 - (lag_val - 500) / 29_500.0)
    diagnostics["data_freshness"] = freshness
    diagnostics["data_lag_ms"] = lag_val

    # ── Weighted fold ──
    score = (
        0.5 * perception_conf
        + 0.3 * completeness
        + 0.2 * freshness
    )
    score = max(0.0, min(1.0, score))
    diagnostics["score"] = score
    diagnostics["hold_threshold"] = _hold_threshold()
    diagnostics["force_hold"] = score < _hold_threshold()
    return score, diagnostics


def should_force_hold(score: float) -> bool:
    return score < _hold_threshold()
