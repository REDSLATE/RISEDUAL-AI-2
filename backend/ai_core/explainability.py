"""Feature-importance explainability for the agent activity feed.

Phase 1: lightweight importance × value contribution scoring. Pure
functions, no model introspection — consumes the
``feature_importances`` dict that SignalModel already attaches to
every SignalResult, and scores each feature by ``value * importance``.

Phase 2 (future): local SHAP values, restricted to high-conviction
trades or losses for performance.

Design notes
------------
* The scoring formula ``value * importance`` is directionally
  correct but approximate. See README / architecture doc for the
  caveat. It's fast, stable, and fits the stack — perfect for a
  "narrative" surface where users want a *gist*, not a proof.
* Returns an empty list on any failure — the caller's never-raise
  contract must hold.
* We normalise the "direction" term (bullish / bearish) based on
  SIGN of the contribution, not the feature's absolute value. A
  large negative momentum signal that *drove the skip* shows as
  ``bearish``, which matches intuition.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


def extract_top_features(
    feature_names: list[str],
    feature_values: list[float],
    importances: Mapping[str, float],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Return the top_k features ranked by ``|value × importance|``.

    Parameters
    ----------
    feature_names, feature_values
        Parallel lists. Values that are NaN, None, or non-numeric
        are treated as 0.0 and effectively dropped from ranking.
    importances
        ``{feature_name: importance_float}``. Missing names default
        to importance 0. Typically ``SignalResult.feature_importance``.
    top_k
        How many rows to return. 3 is the default — any more crowds
        the activity feed row.

    Returns
    -------
    list[dict]
        Each dict::

            {
              "feature": "momentum_14",
              "impact": 0.0421,        # signed; positive = bullish push
              "abs_impact": 0.0421,    # used for ranking, kept for UI
              "direction": "bullish",  # or "bearish"
              "value": 1.85,           # the raw feature value
              "importance": 0.023,     # the global importance weight
            }
    """
    if not feature_names or not feature_values:
        return []
    if len(feature_names) != len(feature_values):
        return []

    scored: list[dict[str, Any]] = []
    for name, value in zip(feature_names, feature_values):
        try:
            v = float(value)
            if math.isnan(v) or math.isinf(v):
                continue
        except (TypeError, ValueError):
            continue
        imp = float(importances.get(name, 0.0) or 0.0)
        if imp <= 0:
            # Features the model never used are noise in this view.
            continue
        impact = v * imp
        scored.append({
            "feature": name,
            "value": round(v, 4),
            "importance": round(imp, 4),
            "impact": round(impact, 4),
            "abs_impact": round(abs(impact), 4),
            "direction": "bullish" if impact > 0 else "bearish",
        })

    # Rank by absolute contribution — sign is presentation-level.
    scored.sort(key=lambda r: r["abs_impact"], reverse=True)
    return scored[: max(1, int(top_k))]
