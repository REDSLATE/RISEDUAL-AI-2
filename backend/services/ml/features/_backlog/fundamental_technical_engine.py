"""RISEDUAL AI — Fundamental + Technical Feature Enrichment Layer.

────────────────────────────────────────────────────────────────────
STATUS: BACKLOG / REFERENCE ONLY
────────────────────────────────────────────────────────────────────

* NOT imported anywhere in the live codebase.
* NOT wired into ``services.ml.feature_extraction.extract_live_features``.
* NOT wired into the Strategist / Auditor / Shelly / Fast Veto layers.
* Saved 2026-05-09 per operator decree ("Save for later, backlog").

Overlap with shipped 2026-05-09 modules — review before integration:

  * ``services/ml/features/fundamentals.py``  — Alpha Vantage OVERVIEW
    shaper (P/E, EPS, PEG, dividend yield, ROE, profit margin, growth
    YOY, beta, book value, EV/EBITDA + binary band flags). Emits a
    flat dict nested under ``frame.market.fundamentals``. Equity-only.

  * ``services/ml/features/technicals.py``    — Universal (equity +
    crypto) wrapper around ``services.universe_technicals.compute_technicals``
    (RSI14, MACD, SMA20/50/200 + binary flags). Emits a flat dict
    nested under ``frame.market.technicals``.

  * ``services/ml/feature_health.py``         — Owns the
    ``feature_health`` score the live pipeline reads. Strategist
    clamps ``effective_confidence = raw_confidence * feature_health_score``
    and forces NO_TRADE when below ``FEATURE_HEALTH_HOLD_THRESHOLD``.

This module's contributions on top of those:

  * Combined dataclass output (``FundamentalTechnicalFeatures``)
    instead of two flat dicts.
  * Raw-price-list input mode (vs. Alpha Vantage OVERVIEW input).
  * Built-in ``regime_tag`` emission (``TREND_UP`` / ``TREND_DOWN`` /
    ``HIGH_VOLATILITY`` / ``RANGE_BOUND``) — overlaps with the
    existing ``services.model_adaptation`` regime tagger and the
    crypto-side ``infer_crypto_regime`` helper.

Open questions to resolve BEFORE wiring (operator must answer):

  1. Where do ``net_income`` + ``shares_outstanding`` come from at
     runtime? AV OVERVIEW already provides EPS — do we want to derive
     ``eps`` from raw inputs (this module) or read it directly off
     OVERVIEW (current ``fundamentals.py``)?
  2. Should ``regime_tag`` be observation-only (one more feature
     key in the frame) or replace the current regime tagger?
  3. Does ``feature_health`` here override the dedicated
     ``services.ml.feature_health.compute_feature_health`` score, or
     is it a parallel signal?

Authority-boundary contract (already honoured by code below):

  * Generates features only — emits no BUY / SELL / NO_TRADE / hold.
  * No broker imports, no order placement.
  * No DB writes.
  * No env mutation.
  * Stays in the read-only feature-emit lane alongside the other
    ``services/ml/features/*.py`` modules.

────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Optional

import numpy as np


# ── Config ──────────────────────────────────────────────────────────

MIN_REQUIRED_PRICE_HISTORY = 20

SMA_SHORT_WINDOW = 5
SMA_LONG_WINDOW = 20

MAX_REASONABLE_PE = 250.0


# ── Feature payload ─────────────────────────────────────────────────


@dataclass
class FundamentalTechnicalFeatures:
    ticker: str

    current_price: float

    pe_ratio: Optional[float]
    eps: Optional[float]

    sma_5: Optional[float]
    sma_20: Optional[float]

    price_vs_sma5: Optional[float]
    price_vs_sma20: Optional[float]

    sma5_slope: Optional[float]

    volatility_5d: Optional[float]

    momentum_positive: bool

    earnings_positive: bool

    regime_tag: str

    feature_health: float

    valid: bool

    reasons: List[str]


# ── Engine ──────────────────────────────────────────────────────────


class FundamentalTechnicalFeatureEngine:
    """Read-only feature enrichment engine.

    NOT WIRED INTO THE LIVE PIPELINE.

    * Generates features only.
    * No BUY/SELL output.
    * No broker access.
    * No trade execution.
    """

    def build_features(
        self,
        ticker: str,
        prices: List[float],
        net_income: float,
        shares_outstanding: float,
    ) -> FundamentalTechnicalFeatures:
        reasons: List[str] = []

        # ── Basic validation ──
        if not prices or len(prices) < MIN_REQUIRED_PRICE_HISTORY:
            return self._invalid(
                ticker=ticker, reason="INSUFFICIENT_PRICE_HISTORY",
            )

        if shares_outstanding <= 0:
            return self._invalid(
                ticker=ticker, reason="INVALID_SHARES_OUTSTANDING",
            )

        # ── Current price ──
        current_price = float(prices[-1])

        # ── EPS / PE ──
        eps: Optional[float] = None
        pe_ratio: Optional[float] = None
        try:
            eps = float(net_income) / float(shares_outstanding)
            if eps > 0:
                pe_ratio = current_price / eps
                if pe_ratio > MAX_REASONABLE_PE:
                    reasons.append("PE_RATIO_EXTREME")
            else:
                reasons.append("NEGATIVE_EARNINGS")
        except Exception:  # noqa: BLE001
            reasons.append("PE_CALCULATION_FAILED")

        # ── Moving averages ──
        sma_5 = float(np.mean(prices[-SMA_SHORT_WINDOW:]))
        sma_20 = float(np.mean(prices[-SMA_LONG_WINDOW:]))

        price_vs_sma5 = current_price - sma_5
        price_vs_sma20 = current_price - sma_20

        # ── Momentum / slope ──
        previous_sma5 = float(
            np.mean(prices[-(SMA_SHORT_WINDOW + 1):-1])
        )
        sma5_slope = sma_5 - previous_sma5
        momentum_positive = current_price > sma_5 and sma5_slope > 0

        # ── Volatility ──
        volatility_5d = float(np.std(prices[-SMA_SHORT_WINDOW:]))

        # ── Earnings quality ──
        earnings_positive = eps is not None and eps > 0

        # ── Regime tagging ──
        regime_tag = self._determine_regime(
            current_price=current_price,
            sma_5=sma_5,
            sma_20=sma_20,
            volatility_5d=volatility_5d,
        )

        # ── Feature health ──
        feature_health = 1.0
        if pe_ratio is None:
            feature_health -= 0.20
        if volatility_5d > 10:
            feature_health -= 0.10
        feature_health = max(0.0, min(1.0, feature_health))

        return FundamentalTechnicalFeatures(
            ticker=ticker,
            current_price=round(current_price, 4),
            pe_ratio=(
                round(pe_ratio, 4) if pe_ratio is not None else None
            ),
            eps=round(eps, 6) if eps is not None else None,
            sma_5=round(sma_5, 4),
            sma_20=round(sma_20, 4),
            price_vs_sma5=round(price_vs_sma5, 4),
            price_vs_sma20=round(price_vs_sma20, 4),
            sma5_slope=round(sma5_slope, 6),
            volatility_5d=round(volatility_5d, 6),
            momentum_positive=momentum_positive,
            earnings_positive=earnings_positive,
            regime_tag=regime_tag,
            feature_health=round(feature_health, 4),
            valid=True,
            reasons=reasons,
        )

    # ── Regime logic ──
    def _determine_regime(
        self,
        current_price: float,
        sma_5: float,
        sma_20: float,
        volatility_5d: float,
    ) -> str:
        if volatility_5d > 12:
            return "HIGH_VOLATILITY"
        if current_price > sma_5 > sma_20:
            return "TREND_UP"
        if current_price < sma_5 < sma_20:
            return "TREND_DOWN"
        return "RANGE_BOUND"

    # ── Invalid response ──
    def _invalid(
        self, ticker: str, reason: str,
    ) -> FundamentalTechnicalFeatures:
        return FundamentalTechnicalFeatures(
            ticker=ticker,
            current_price=0.0,
            pe_ratio=None,
            eps=None,
            sma_5=None,
            sma_20=None,
            price_vs_sma5=None,
            price_vs_sma20=None,
            sma5_slope=None,
            volatility_5d=None,
            momentum_positive=False,
            earnings_positive=False,
            regime_tag="UNKNOWN",
            feature_health=0.0,
            valid=False,
            reasons=[reason],
        )


# ── Smoke example (NOT executed by tests) ───────────────────────────

if __name__ == "__main__":
    engine = FundamentalTechnicalFeatureEngine()
    sample_prices = [
        140.0, 141.2, 142.0, 143.1, 144.3,
        145.5, 146.0, 146.4, 147.2, 148.0,
        148.5, 149.1, 149.8, 150.0, 150.3,
        151.1, 151.8, 152.2, 153.0, 154.0,
    ]
    result = engine.build_features(
        ticker="RISE",
        prices=sample_prices,
        net_income=1_000_000,
        shares_outstanding=50_000,
    )
    print(asdict(result))


# ── Future wiring (operator-resolved before integration) ────────────
"""
FUTURE INTEGRATION POINTS:

1. Strategist:
   strategist_features.update(asdict(feature_payload))

2. Auditor:
   use feature_health + regime_tag as calibration context

3. Shelly:
   memory tags:
       regime_tag
       momentum_positive
       pe_ratio_bucket

4. Fast Veto:
   future toxicity learning:
       HIGH_VOLATILITY + NEGATIVE_EARNINGS
       may correlate with failures

5. ML Training:
   append feature vector to training rows before .joblib fit
"""
