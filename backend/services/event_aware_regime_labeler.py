"""RISEDUAL AI — Event-Aware Regime Labeler.

PURPOSE
-------
Label historical rows with:
  * macro regime (vix / yield curve / liquidity / macro phase)
  * named market event (15-year breakdown)
  * event family (crash / bubble / tightening / volatility_event / ...)
  * deterministic ``regime_id`` (sha256 fingerprint of the regime
    + event tuple).

USED FOR
--------
  * Training-data enrichment (Strategist / Auditor / Commander).
  * Backtests, post-mortems, calibration analysis by regime.
  * Regime-conditioned performance tracking.

STRICTLY FORBIDDEN
------------------
This module MUST NOT be imported by, or import from:
  * any execution layer (paper trading, broker routing, options).
  * the live decision stack (Strategist / Auditor / Commander runtime,
    confidence gate, conviction service, risk multipliers).
  * any memory writer (toxic_lessons, prediction_tracker writes,
    ai_alerts writes, regime_memory_retrieval.ingest_memory).

This is DATA CONTEXT, not alpha. The contract is enforced by the
companion test ``tests/test_event_aware_regime_labeler.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


# ─── EVENT DEFINITIONS (15-year window) ───────────────────────────────

MARKET_EVENTS: List[tuple[str, str, str]] = [
    ("2008-09-01", "2009-06-30", "GFC_CRISIS"),
    ("2010-05-06", "2010-05-07", "FLASH_CRASH"),
    ("2015-08-11", "2016-02-29", "CHINA_DEVALUATION"),
    ("2018-02-01", "2018-03-31", "VOL_SPIKE_2018"),
    ("2020-02-20", "2020-04-30", "COVID_CRASH"),
    ("2020-05-01", "2021-12-31", "COVID_RECOVERY"),
    ("2022-01-01", "2023-12-31", "RATE_HIKE_CYCLE"),
    ("2024-01-01", "2025-12-31", "AI_BUBBLE"),
]

_EVENT_FAMILY: Dict[str, str] = {
    "GFC_CRISIS": "crash",
    "COVID_CRASH": "crash",
    "COVID_RECOVERY": "recovery",
    "AI_BUBBLE": "bubble",
    "RATE_HIKE_CYCLE": "tightening",
    "VOL_SPIKE_2018": "volatility_event",
    "FLASH_CRASH": "volatility_event",
    "CHINA_DEVALUATION": "macro_shock",
}

_CRISIS_EVENTS = {"GFC_CRISIS", "COVID_CRASH"}


# ─── DATA STRUCTURES ──────────────────────────────────────────────────


@dataclass(frozen=True)
class RegimeLabel:
    vix_level: str
    yield_curve: str
    liquidity: str
    macro_phase: str
    market_event: str
    event_family: str
    is_crisis: bool
    regime_id: str


@dataclass
class InputRow:
    date: str
    vix: Optional[float] = None
    ten_year: Optional[float] = None
    two_year: Optional[float] = None
    volume: Optional[float] = None
    avg_volume: Optional[float] = None
    sp500: Optional[float] = None


# ─── LABELER ──────────────────────────────────────────────────────────


class EventAwareRegimeLabeler:
    """Pure labeling layer. No state, no I/O, no decision-stack usage."""

    # ---------- helpers ----------

    @staticmethod
    def _valid(v) -> bool:
        return v is not None

    @staticmethod
    def _hash(payload: Dict[str, str]) -> str:
        raw = "|".join(f"{k}:{v}" for k, v in sorted(payload.items()))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ---------- event tagging ----------

    def tag_event(self, date_str: str) -> str:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        for start, end, name in MARKET_EVENTS:
            s = datetime.strptime(start, "%Y-%m-%d")
            e = datetime.strptime(end, "%Y-%m-%d")
            if s <= dt <= e:
                return name
        return "NONE"

    def event_family(self, event: str) -> str:
        return _EVENT_FAMILY.get(event, "normal")

    def is_crisis(self, event: str) -> bool:
        return event in _CRISIS_EVENTS

    # ---------- regime tagging ----------

    def tag_vix(self, vix: Optional[float]) -> str:
        if not self._valid(vix):
            return "unknown"
        if vix < 15:
            return "low"
        if vix < 20:
            return "normal"
        if vix < 30:
            return "elevated"
        return "extreme"

    def tag_yield_curve(
        self, ten: Optional[float], two: Optional[float],
    ) -> str:
        if not self._valid(ten) or not self._valid(two):
            return "unknown"
        spread = ten - two
        if spread > 1:
            return "steep"
        if spread > 0:
            return "flat"
        return "inverted"

    def tag_liquidity(
        self,
        volume: Optional[float],
        avg_volume: Optional[float],
    ) -> str:
        if (
            not self._valid(volume)
            or not self._valid(avg_volume)
            or avg_volume == 0
        ):
            return "unknown"
        ratio = volume / avg_volume
        if ratio < 0.7:
            return "thin"
        if ratio > 1.3:
            return "high"
        return "normal"

    def tag_macro_phase(self, sp500: Optional[float]) -> str:
        # Simplified — training context only.
        if not self._valid(sp500):
            return "unknown"
        if sp500 > 4000:
            return "expansion"
        if sp500 > 3000:
            return "late_cycle"
        return "contraction"

    # ---------- main ----------

    def label_row(self, row: InputRow) -> RegimeLabel:
        event = self.tag_event(row.date)
        parts = {
            "vix": self.tag_vix(row.vix),
            "yc": self.tag_yield_curve(row.ten_year, row.two_year),
            "liq": self.tag_liquidity(row.volume, row.avg_volume),
            "phase": self.tag_macro_phase(row.sp500),
            "event": event,
        }
        return RegimeLabel(
            vix_level=parts["vix"],
            yield_curve=parts["yc"],
            liquidity=parts["liq"],
            macro_phase=parts["phase"],
            market_event=event,
            event_family=self.event_family(event),
            is_crisis=self.is_crisis(event),
            regime_id=self._hash(parts),
        )

    # ---------- batch ----------

    def label_dataset(self, rows: List[InputRow]) -> List[Dict]:
        output: List[Dict] = []
        for r in rows:
            label = self.label_row(r)
            output.append({
                **r.__dict__,
                "regime_label": label.macro_phase,
                "vix_level": label.vix_level,
                "yield_curve": label.yield_curve,
                "liquidity": label.liquidity,
                "market_event": label.market_event,
                "event_family": label.event_family,
                "is_crisis": label.is_crisis,
                "regime_id": label.regime_id,
            })
        return output
