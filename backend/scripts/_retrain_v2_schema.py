"""v2 feature-schema definitions for alpha-retrain.

Strangler-split out of ``scripts/retrain_alpha_models.py`` (2026-05-09)
to keep the main script under the 800-line architectural ceiling
(``test_code_size.py``).

Holds:
  * v1 / v2 schema string constants
  * ordered feature-name lists per schema
  * ``feature_dim_for`` lookup
  * ``reconstruct_features_v2`` extractor + its helper
    ``_read_nested_market_dict``

Everything is re-exported from ``scripts.retrain_alpha_models`` so
existing tests + CLI callers (which import from there) keep working
unchanged. This module has NO side effects, NO env reads, NO Mongo
calls, NO broker imports — same firewall as
``services.ml.features``.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


# ── Schema constants ───────────────────────────────────────────

FEATURE_SCHEMA_V1 = "alpha_v1"
FEATURE_SCHEMA_V2 = "alpha_v2_fundamentals_technicals"
DEFAULT_FEATURE_SCHEMA = FEATURE_SCHEMA_V1


# ── Ordered feature names per schema ───────────────────────────

# v1 — mirrors services/ml/strategist/base.py exactly. The live
# Strategist artifact reads its 10 inputs in this order, so this
# list is the contract every loaded artifact obeys.
FEATURE_NAMES_V1: List[str] = [
    "perception.event_shock.score",
    "perception.regime.score",
    "perception.drawdown.score",
    "perception.liquidity.score",
    "perception.system_health.score",
    "perception.pacing.score",
    "perception.avg_confidence",
    "shelly_recall.positive_ratio",
    "shelly_recall.negative_ratio",
    "intent_hint_encoded",
]

FEATURE_DIM_V1 = len(FEATURE_NAMES_V1)  # 10

# v2 fundamentals slot order (equity-only — crypto rows fill 0.0).
# Pulled from services/ml/features/fundamentals.py output keys.
FEATURE_NAMES_V2_FUNDAMENTALS: List[str] = [
    "fundamentals.pe_ratio",
    "fundamentals.forward_pe",
    "fundamentals.peg_ratio",
    "fundamentals.eps",
    "fundamentals.dividend_yield",
    "fundamentals.profit_margin",
    "fundamentals.return_on_equity",
    "fundamentals.price_to_book",
    "fundamentals.beta",
    "fundamentals.revenue_growth_yoy",
    "fundamentals.earnings_growth_yoy",
    "fundamentals.pe_in_value_band",
    "fundamentals.pe_in_growth_band",
    "fundamentals.pe_in_speculative",
    "fundamentals.negative_eps",
    "fundamentals.dividend_payer",
]

# v2 technicals slot order (both lanes).
# Pulled from services/ml/features/technicals.py output keys.
FEATURE_NAMES_V2_TECHNICALS: List[str] = [
    "technicals.rsi14",
    "technicals.macd",
    "technicals.macd_signal",
    "technicals.macd_hist",
    "technicals.price_above_sma20",
    "technicals.price_above_sma50",
    "technicals.price_above_sma200",
    "technicals.sma20_above_sma50",
    "technicals.sma50_above_sma200",
    "technicals.rsi_oversold",
    "technicals.rsi_overbought",
    "technicals.rsi_neutral",
    "technicals.macd_bullish",
    "technicals.macd_bearish",
]

# Full v2 ordered list = v1 + fundamentals + technicals.
FEATURE_NAMES_V2: List[str] = (
    FEATURE_NAMES_V1
    + FEATURE_NAMES_V2_FUNDAMENTALS
    + FEATURE_NAMES_V2_TECHNICALS
)

FEATURE_DIM_V2 = len(FEATURE_NAMES_V2)  # 10 + 16 + 14 = 40

FEATURE_NAMES_BY_SCHEMA: Dict[str, List[str]] = {
    FEATURE_SCHEMA_V1: FEATURE_NAMES_V1,
    FEATURE_SCHEMA_V2: FEATURE_NAMES_V2,
}


def feature_dim_for(schema: str) -> int:
    """Look up the expected feature dim for a schema. Defaults to v1."""
    return len(FEATURE_NAMES_BY_SCHEMA.get(schema, FEATURE_NAMES_V1))


# ── v2 reconstructor ───────────────────────────────────────────


def _read_nested_market_dict(
    decision_doc: Dict[str, Any], key: str,
) -> Optional[Dict[str, Any]]:
    """Pull ``frame.market.<key>`` from an ``alpha_decision_log`` row.

    The decision log persists the FeatureFrame.market dict under
    several possible names — ``market`` / ``market_features`` /
    ``feature_frame.market`` — so we probe in priority order and
    return ``None`` when nothing matches. ``None`` means "no signal
    recorded" (different from an empty dict, which means "computed
    but yielded no features").
    """
    candidates = (
        decision_doc.get("market"),
        decision_doc.get("market_features"),
        (decision_doc.get("feature_frame") or {}).get("market")
            if isinstance(decision_doc.get("feature_frame"), dict) else None,
    )
    for src in candidates:
        if isinstance(src, dict) and key in src:
            sub = src.get(key)
            return sub if isinstance(sub, dict) else None
    return None


def reconstruct_features_v2(
    decision_doc: Dict[str, Any],
    lane: str,
    *,
    base_extractor: Callable[[Dict[str, Any]], Optional[List[float]]],
    safe_float: Callable[..., float],
) -> Optional[Tuple[List[float], Dict[str, bool]]]:
    """Reconstruct the v2 (40-dim) feature vector.

    Layout = v1 (10) + fundamentals (16) + technicals (14).

    Args:
      decision_doc: persisted ``alpha_decision_log`` row.
      lane: ``"equity"`` or ``"crypto"`` (anything else treated as
        not-equity for the firewall — fundamentals not expected).
      base_extractor: the v1 reconstruct function. Injected by the
        caller to avoid a circular import. Called with ``decision_doc``;
        returns the 10-dim list or ``None``.
      safe_float: shared float coercer (also injected). Same contract
        as ``scripts/retrain_alpha_models._safe_float``: ``None`` /
        ``"None"`` / ``"-"`` / "" → ``default`` (passed positionally
        from the caller; we always rely on the default of 0.0 here
        for missing slots).

    Returns ``(features, presence)`` where ``presence`` is
    ``{"fundamentals": bool, "technicals": bool, "expected_fundamentals": bool}``
    so the caller can increment missing-counters without re-walking
    the document.

    Lane semantics (per operator brief):
      * crypto rows MUST NOT require fundamentals — missing →
        0.0 fills, ``presence["fundamentals"]=False``,
        ``presence["expected_fundamentals"]=False`` so the
        missing-fundamentals counter NEVER bumps for crypto.
      * equity rows: missing fundamentals → 0.0 fills + counter bumped.
      * technicals: both lanes; missing → 0.0 fills + counter bumped.

    Returns ``None`` when the v1 base vector cannot be recovered
    — same semantic as v1 (those rows are "missing_features" skips).
    v2 does NOT silently lower the bar.
    """
    base = base_extractor(decision_doc)
    if base is None:
        return None

    lane_norm = (lane or "").strip().lower()

    fundamentals = _read_nested_market_dict(decision_doc, "fundamentals")
    technicals = _read_nested_market_dict(decision_doc, "technicals")

    fundamentals_slots: List[float] = []
    for name in FEATURE_NAMES_V2_FUNDAMENTALS:
        # Strip the "fundamentals." prefix to get the dict key.
        key = name.split(".", 1)[1]
        if isinstance(fundamentals, dict) and key in fundamentals:
            fundamentals_slots.append(safe_float(fundamentals.get(key)))
        else:
            fundamentals_slots.append(0.0)

    technicals_slots: List[float] = []
    for name in FEATURE_NAMES_V2_TECHNICALS:
        key = name.split(".", 1)[1]
        if isinstance(technicals, dict) and key in technicals:
            technicals_slots.append(safe_float(technicals.get(key)))
        else:
            technicals_slots.append(0.0)

    feats = base + fundamentals_slots + technicals_slots
    if len(feats) != FEATURE_DIM_V2:
        return None

    presence = {
        "fundamentals": isinstance(fundamentals, dict) and bool(fundamentals),
        "technicals": isinstance(technicals, dict) and bool(technicals),
        # crypto rows never count as missing fundamentals — pinned here
        # so the extractor doesn't have to re-derive lane logic.
        "expected_fundamentals": lane_norm == "equity",
    }
    return feats, presence
