"""Equity-only fundamentals feature builder.

Pulls Alpha Vantage OVERVIEW data (already cached by
``services.price_provider.get_overview_sync``) and emits a flat,
structured feature dict for the ML perception/strategist layers
to consume during retraining.

Hard rules
----------
* Equity-only. Crypto / unknown lanes get ``{}``. The fundamentals
  surface (P/E, EPS, dividend yield) does not exist for digital
  assets and feeding zero-or-garbage values into a future
  Strategist retrain would teach the wrong lesson.
* NEVER raises. Missing API key, missing OVERVIEW row, malformed
  values → empty dict, optional fields, or ``None`` per slot.
* NEVER places an order, calls a broker, or branches on the
  output to issue a verdict. This is feature production only.
* All raw numeric values plus a small set of binary "band" flags
  (``pe_in_value_band`` etc.). The flags are ML inputs — *never*
  hardcoded ``if`` rules for execution.

The returned dict is observation-only today (lands in
``frame.market.fundamentals``). When the next Strategist
retraining widens the feature vector, this dict is the
canonical source.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_NUMERIC_FIELDS_FROM_OVERVIEW: Dict[str, str] = {
    # Output-key → AV-OVERVIEW key
    "pe_ratio": "PERatio",
    "forward_pe": "ForwardPE",
    "trailing_pe": "TrailingPE",
    "peg_ratio": "PEGRatio",
    "eps": "EPS",
    "diluted_eps_ttm": "DilutedEPSTTM",
    "dividend_yield": "DividendYield",
    "profit_margin": "ProfitMargin",
    "operating_margin": "OperatingMarginTTM",
    "return_on_equity": "ReturnOnEquityTTM",
    "return_on_assets": "ReturnOnAssetsTTM",
    "price_to_book": "PriceToBookRatio",
    "price_to_sales": "PriceToSalesRatioTTM",
    "ev_to_revenue": "EVToRevenue",
    "ev_to_ebitda": "EVToEBITDA",
    "beta": "Beta",
    "revenue_growth_yoy": "QuarterlyRevenueGrowthYOY",
    "earnings_growth_yoy": "QuarterlyEarningsGrowthYOY",
    "book_value": "BookValue",
}


def _safe_float(value: Any) -> Optional[float]:
    """Coerce a value to ``float`` or return ``None``.

    AV returns numerics as strings, with ``"None"`` / ``"-"`` /
    empty string for missing fields. All of those map to ``None``.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip()
        if not v or v.lower() == "none" or v == "-":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


def _classify_pe_band(pe: Optional[float]) -> Dict[str, int]:
    """Binary band flags for the P/E ratio.

    These flags are FEATURES for the ML to learn from — NEVER
    decision rules. The Strategist may learn that ``pe_in_value_band``
    matters in regime A but not B; that's the whole point.

    Bands (industry-standard rough cuts):
      * ``negative_eps``     — P/E unavailable because earnings ≤ 0
      * ``pe_in_value_band`` — ``0 < pe < 20``
      * ``pe_in_growth_band``— ``20 ≤ pe < 50``
      * ``pe_in_speculative``— ``pe ≥ 50``
    """
    flags = {
        "negative_eps": 0,
        "pe_in_value_band": 0,
        "pe_in_growth_band": 0,
        "pe_in_speculative": 0,
    }
    if pe is None:
        return flags
    if pe <= 0:
        flags["negative_eps"] = 1
        return flags
    if pe < 20:
        flags["pe_in_value_band"] = 1
    elif pe < 50:
        flags["pe_in_growth_band"] = 1
    else:
        flags["pe_in_speculative"] = 1
    return flags


def _shape_features(overview: Dict[str, Any]) -> Dict[str, Any]:
    """Pure shape transformation — no I/O, no exceptions."""
    out: Dict[str, Any] = {}
    for out_key, av_key in _NUMERIC_FIELDS_FROM_OVERVIEW.items():
        out[out_key] = _safe_float(overview.get(av_key))

    pe = out.get("pe_ratio")
    out.update(_classify_pe_band(pe))

    # Dividend payer flag — feature for the ML, NOT a decision rule.
    dy = out.get("dividend_yield")
    out["dividend_payer"] = 1 if (dy is not None and dy > 0) else 0

    # Light derived combo: value+momentum alignment is left to the
    # technicals + perception layers; we expose only the value half
    # here so the cross-feature interaction can be learned downstream.
    return out


async def build_fundamentals_features(
    symbol: str,
    lane: str,
) -> Dict[str, Any]:
    """Build the fundamentals feature dict for ``symbol``.

    Args:
        symbol: ticker (case-insensitive).
        lane: ``"equity"`` runs the full extract; anything else
            (``"crypto"``, ``"unknown"``, ``""``) returns ``{}``.

    Returns:
        Flat dict of numeric features + binary band flags.
        Empty dict on lane-firewall trip, missing API key,
        unreachable provider, or malformed response.
    """
    if (lane or "").strip().lower() != "equity":
        return {}
    if not symbol:
        return {}
    try:
        from services.price_provider import get_overview_sync
        # The price provider's OVERVIEW path is sync (Alpha Vantage
        # adapter). Run it in a worker thread so the event loop
        # stays responsive — same pattern company_research_service
        # uses.
        overview = await asyncio.to_thread(get_overview_sync, symbol.upper())
        if not overview or not isinstance(overview, dict):
            return {}
        if not overview.get("Symbol"):
            return {}
        return _shape_features(overview)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[features.fundamentals] fetch failed for %s: %s", symbol, exc,
        )
        return {}
