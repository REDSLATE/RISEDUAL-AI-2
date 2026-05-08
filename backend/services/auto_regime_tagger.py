"""
Auto Regime Tagger — converts raw macro snapshots into the
categorical ``RegimeFingerprint`` already used by
``services.regime_memory_retrieval``.

Patent M layer (b) — "label maker".

Why a separate module
---------------------
The existing ``RegimeFingerprint`` is hand-built today (operator or
ingest pipeline picks the labels). This module turns *raw numbers*
(VIX, 2y/10y yields, DXY, HY-IG spread, an M2/RRP-style liquidity
proxy, an optional macro-phase string) into the same fingerprint
shape. That lets the orchestrator tag 15 years of macro history
the same way regardless of which feed produced it.

What this module deliberately does NOT do
-----------------------------------------
* No live data fetching. The caller passes in ``RawMacroData``
  already shaped. (Existing FRED/macro services do the IO.)
* No MongoDB writes — this is a pure transform layer.
* No risk decisions or sizing.

Threshold sources
-----------------
VIX bands and inversion criteria are widely-used market
conventions. They live as module-level constants so a future
admin route can render them read-only without bytecode-walking,
and so calibration changes leave a visible diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from services.regime_memory_retrieval import RegimeFingerprint


# ─── Calibration thresholds (operator-readable) ─────────────────

# VIX bands. Conventional rule-of-thumb cuts.
VIX_LOW = 15.0
VIX_NORMAL_HI = 20.0
VIX_ELEVATED_HI = 30.0
# Above VIX_ELEVATED_HI → "extreme".

# Yield curve: 10y minus 2y in basis points.
YC_INVERSION_BP = 0      # negative spread → inverted
YC_FLAT_HI_BP = 50       # 0..50 bp → flat, >50 → steep

# DXY momentum: percent change vs 60-day average.
DXY_BREAKOUT_PCT = 3.0
DXY_STRONG_PCT = 0.5
# Below DXY_STRONG_PCT (incl. negative) → "weak".

# Credit spreads: HY OAS minus IG OAS, in basis points.
CREDIT_TIGHT_HI_BP = 250
CREDIT_WIDENING_HI_BP = 500
# Above CREDIT_WIDENING_HI_BP → "distressed".

# Liquidity proxy: pass in something normalised to a -1..+1 scale
# (e.g. M2 YoY growth z-score, RRP balance z-score, or your own
# composite). We bucket on that scale so the source choice can
# evolve without touching this module.
LIQUIDITY_FROZEN_HI = -0.75
LIQUIDITY_CONSTRAINED_HI = -0.25
LIQUIDITY_AMPLE_LO = 0.25


@dataclass
class RawMacroData:
    """One macro snapshot.

    All fields are optional so partial feeds still produce a
    fingerprint (with conservative fallbacks). The orchestrator's
    ``LearningCoreDecisionContext`` uses this shape directly.
    """

    date: str                         # ISO date — no parsing here
    vix: Optional[float] = None
    yield_2y: Optional[float] = None
    yield_10y: Optional[float] = None
    dxy: Optional[float] = None
    dxy_60d_avg: Optional[float] = None
    hy_oas_bp: Optional[float] = None
    ig_oas_bp: Optional[float] = None
    liquidity_z: Optional[float] = None
    macro_phase_hint: Optional[str] = None  # operator override


@dataclass
class RegimePretell:
    """Wrapper so callers can tell apart a "no historical data
    available" return (``None``) from a real pre-tell snapshot."""

    days_back: int
    fingerprint: RegimeFingerprint
    source_date: str


class RegimeTagger:
    """Pure transform: ``RawMacroData`` → ``RegimeFingerprint``.

    Stateless by design — every method is a function in disguise.
    """

    def generate_fingerprint(
        self,
        macro_data: RawMacroData,
        macro_history: Optional[Dict[str, List[float]]] = None,
    ) -> RegimeFingerprint:
        """Categorise the snapshot.

        ``macro_history`` is accepted for forward compatibility (a
        future revision may use it for trend/Δ features) but the
        Phase-1 tagger ignores it. We accept it so the orchestrator
        signature stays stable when we wire it in later.
        """
        _ = macro_history  # reserved for Phase-2 trend features

        return RegimeFingerprint(
            vix_level=self._vix_level(macro_data.vix),
            yield_curve=self._yield_curve(
                macro_data.yield_2y, macro_data.yield_10y,
            ),
            dxy_trend=self._dxy_trend(
                macro_data.dxy, macro_data.dxy_60d_avg,
            ),
            credit_spreads=self._credit_spreads(
                macro_data.hy_oas_bp, macro_data.ig_oas_bp,
            ),
            liquidity=self._liquidity(macro_data.liquidity_z),
            macro_phase=self._macro_phase(macro_data.macro_phase_hint),
        )

    def generate_pretell(
        self,
        current_date: str,
        macro_series: List[RawMacroData],
        days_back: int = 30,
    ) -> Optional[RegimePretell]:
        """Find the snapshot ``days_back`` entries before
        ``current_date`` and tag it.

        Returns ``None`` if the series is too short — keeps the
        orchestrator's pretell branch a clean no-op when history
        isn't deep enough.
        """
        if days_back < 1:
            raise ValueError("days_back must be >= 1")
        if not macro_series:
            return None

        # Find current_date index. Series is assumed already sorted
        # ascending; we tolerate unsorted input by sorting on copy.
        ordered = sorted(macro_series, key=lambda m: m.date)
        try:
            idx = next(
                i for i, m in enumerate(ordered)
                if m.date == current_date
            )
        except StopIteration:
            return None

        target_idx = idx - days_back
        if target_idx < 0:
            return None

        target = ordered[target_idx]
        fp = self.generate_fingerprint(target, macro_history=None)
        return RegimePretell(
            days_back=days_back,
            fingerprint=fp,
            source_date=target.date,
        )

    # ─── per-axis classifiers ───────────────────────────────────
    # All accept ``None`` and fall back to the safest middle bucket
    # so a partial feed never blows up.

    @staticmethod
    def _vix_level(vix: Optional[float]) -> str:
        if vix is None:
            return "normal"
        if vix < VIX_LOW:
            return "low"
        if vix < VIX_NORMAL_HI:
            return "normal"
        if vix < VIX_ELEVATED_HI:
            return "elevated"
        return "extreme"

    @staticmethod
    def _yield_curve(
        y2: Optional[float], y10: Optional[float],
    ) -> str:
        if y2 is None or y10 is None:
            return "flat"
        spread_bp = (y10 - y2) * 100.0  # convert pct → bp
        if spread_bp < YC_INVERSION_BP:
            return "inverted"
        if spread_bp <= YC_FLAT_HI_BP:
            return "flat"
        return "steep"

    @staticmethod
    def _dxy_trend(
        dxy: Optional[float], dxy_60d: Optional[float],
    ) -> str:
        if dxy is None or dxy_60d is None or dxy_60d == 0:
            return "strong"
        pct = ((dxy - dxy_60d) / dxy_60d) * 100.0
        if pct >= DXY_BREAKOUT_PCT:
            return "breakout"
        if pct >= DXY_STRONG_PCT:
            return "strong"
        return "weak"

    @staticmethod
    def _credit_spreads(
        hy_bp: Optional[float], ig_bp: Optional[float],
    ) -> str:
        if hy_bp is None or ig_bp is None:
            return "widening"
        spread = hy_bp - ig_bp
        if spread <= CREDIT_TIGHT_HI_BP:
            return "tight"
        if spread <= CREDIT_WIDENING_HI_BP:
            return "widening"
        return "distressed"

    @staticmethod
    def _liquidity(z: Optional[float]) -> str:
        if z is None:
            return "normal"
        if z <= LIQUIDITY_FROZEN_HI:
            return "frozen"
        if z <= LIQUIDITY_CONSTRAINED_HI:
            return "constrained"
        if z >= LIQUIDITY_AMPLE_LO:
            return "ample"
        return "normal"

    @staticmethod
    def _macro_phase(hint: Optional[str]) -> str:
        # We don't try to derive macro phase from numbers in
        # Phase 1 — it's a human-tagged label. Pass through if
        # the operator supplied a known token, otherwise default
        # to ``expansion`` (the safest "do nothing weird" bucket).
        valid = {"expansion", "late_cycle", "recession", "recovery"}
        if hint and hint in valid:
            return hint
        return "expansion"
