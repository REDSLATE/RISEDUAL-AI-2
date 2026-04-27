"""Regime-conditional weight calculator (P2 scaffolding).

What this module is
-------------------
A pure-function weight calculator that turns regime-tagged shadow
stats into per-(engine, asset_type, regime) sizing multipliers. The
output is a dict-of-dicts the bot scheduler can consult before
applying its base position size, e.g.::

    weight = regime_weights["crypto"]["trending"]  # 1.10
    final_position_size = base_size * weight

Default-inert design
--------------------
Returns ``1.0`` for every regime where the dissent bucket hasn't
matured (≥``MIN_REGIME_SAMPLES`` scored dissents). 1.0 is the
"no opinion" multiplier — applying it changes nothing. Until real
regime-conditional data accumulates, this whole module is a no-op
with operator visibility.

Hard bounds
-----------
Same discipline as :mod:`services.council_risk_modulator` — bounds
pinned in code, not env. An operator misconfiguration cannot unlock
2× sizing or 0.5× sizing through env overrides.

When does this become live?
---------------------------
The scheduler hook is intentionally NOT wired today. Activation
sequence (post-Tier-3, after at least 30 dissents per regime
have been scored):

    1. Operator reviews ``GET /api/admin/shadow/regime-weights``
       and confirms the proposed multipliers look defensible.
    2. Operator flips ``REGIME_WEIGHTS_ENABLED=true`` in .env.
    3. Operator adds the ~5-line hook into the bot scheduler.

Until step 3, this module is logging-only.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Activation flag — defaults false. Hook isn't wired into the bot
# scheduler yet; this is here so when it IS wired, the env flag
# is the same name + shape as the rest of the framework.
REGIME_WEIGHTS_ENABLED: bool = (
    os.getenv("REGIME_WEIGHTS_ENABLED", "false").lower() == "true"
)


# Maturity guardrail — same MIN_DISSENT_SAMPLES floor as the rest
# of the framework, applied per regime. 30 dissents in a single
# regime bucket before that regime's weight is allowed to deviate
# from 1.0.
MIN_REGIME_SAMPLES: int = int(os.getenv("REGIME_MIN_SAMPLES", "30"))


# Hard bounds on the computed multipliers. Pinned in code.
MAX_REGIME_WEIGHT: float = 1.25
MIN_REGIME_WEIGHT: float = 0.50
NEUTRAL_WEIGHT: float = 1.0


# Win-rate → weight mapping. Conservative envelope:
#   - win_rate ≥ 0.65  → 1.20× (strong upweight)
#   - win_rate ≥ 0.55  → 1.10× (small upweight)
#   - win_rate ≥ 0.45  → 1.00× (neutral)
#   - win_rate ≥ 0.35  → 0.85× (small downweight)
#   - win_rate <  0.35 → 0.70× (strong downweight)
#
# All multipliers also pass through the [MIN, MAX] floor/ceiling
# at the end so even a misconfigured envelope can't exit bounds.
def _win_rate_to_weight(win_rate: Optional[float]) -> float:
    if win_rate is None:
        return NEUTRAL_WEIGHT
    wr = float(win_rate)
    if wr >= 0.65:
        return 1.20
    if wr >= 0.55:
        return 1.10
    if wr >= 0.45:
        return NEUTRAL_WEIGHT
    if wr >= 0.35:
        return 0.85
    return 0.70


def _clamp(v: float) -> float:
    return max(MIN_REGIME_WEIGHT, min(MAX_REGIME_WEIGHT, v))


def compute_regime_weights(regime_stats: Dict[str, Any]) -> Dict[str, Any]:
    """Pure reducer — turns the regime-stats payload into the
    operator-grade weights dict.

    Input shape (matches :func:`services.research_shadow_stats.fetch_regime_stats`):

        {"buckets": [
            {"regime": "trending", "shadow_engine": "council",
             "asset_type": "crypto", "dissent_count": 45,
             "scored_dissent_count": 45, "win_rate": 0.62,
             "total_delta_usd": 18.5, "actionable": True},
            ...
        ]}

    Output shape::

        {
            "weights": {
                "crypto": {
                    "trending": {
                        "weight": 1.10,
                        "actionable": True,
                        "scored": 45,
                        "win_rate": 0.62,
                        "delta_usd": 18.5,
                    },
                    "parabolic": {"weight": 1.0, "actionable": False, ...},
                    ...
                },
                "stock": {...},
            },
            "min_samples_required": 30,
            "enabled": false,
        }

    Default-inert: every (asset_type, regime) without enough samples
    receives ``weight = 1.0`` (the no-op multiplier).
    """
    out: Dict[str, Any] = {
        "weights": {},
        "min_samples_required": MIN_REGIME_SAMPLES,
        "enabled": REGIME_WEIGHTS_ENABLED,
    }

    for bucket in regime_stats.get("buckets", []) or []:
        regime = bucket.get("regime")
        asset_type = bucket.get("asset_type")
        if not regime or not asset_type:
            continue

        scored = int(bucket.get("scored_dissent_count") or 0)
        win_rate = bucket.get("win_rate")
        delta = float(bucket.get("total_delta_usd") or 0.0)

        if scored >= MIN_REGIME_SAMPLES and win_rate is not None:
            weight = _clamp(_win_rate_to_weight(win_rate))
            actionable = True
        else:
            weight = NEUTRAL_WEIGHT
            actionable = False

        asset_dict = out["weights"].setdefault(asset_type, {})
        asset_dict[regime] = {
            "weight": round(weight, 4),
            "actionable": actionable,
            "scored": scored,
            "win_rate": (
                round(float(win_rate), 4) if win_rate is not None else None
            ),
            "delta_usd": round(delta, 4),
            "needed_samples": max(0, MIN_REGIME_SAMPLES - scored),
        }

    return out


def lookup_weight(
    weights_payload: Dict[str, Any],
    *,
    asset_type: str,
    regime: str,
) -> float:
    """Lookup helper for the (eventual) bot scheduler hook.

    Returns ``1.0`` when:
        - the framework's env flag is off
        - no matching (asset_type, regime) bucket exists
        - the matching bucket isn't actionable yet

    Caller can apply unconditionally — this function never returns
    a multiplier that would change behaviour unless ALL the gates
    have explicitly passed.
    """
    if not REGIME_WEIGHTS_ENABLED:
        return NEUTRAL_WEIGHT
    weights = weights_payload.get("weights", {})
    asset_dict = weights.get(asset_type)
    if not asset_dict:
        return NEUTRAL_WEIGHT
    record = asset_dict.get(regime)
    if not record or not record.get("actionable"):
        return NEUTRAL_WEIGHT
    raw = record.get("weight")
    try:
        return _clamp(float(raw))
    except (TypeError, ValueError):
        return NEUTRAL_WEIGHT


async def fetch_regime_weights(
    db: Any, *, hours: Optional[int] = None,
) -> Dict[str, Any]:
    """Mongo glue — fetches regime stats + computes weights in one shot."""
    try:
        from services.research_shadow_stats import fetch_regime_stats
        regime_stats = await fetch_regime_stats(db, hours=hours)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[regime-weights] regime stats fetch failed: %s", exc)
        regime_stats = {"buckets": []}

    return compute_regime_weights(regime_stats)
