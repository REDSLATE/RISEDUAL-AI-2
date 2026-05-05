"""RISEDUAL AI — Regime-Conditioned Performance Tracker.

PURPOSE
-------
Measure win rate, avg return, and calibration gap (``confidence -
realised win rate``) per regime / event / event-family. Consumes
rows already enriched by ``services.event_aware_regime_labeler``.

USED FOR
--------
  * Strategist accuracy breakdowns by event family (crash / bubble /
    tightening / normal / ...).
  * Auditor calibration drift detection during crisis regimes.
  * Detecting silent overfit to a single regime (e.g. AI bubble).
  * Post-mortem dashboards.

STRICTLY FORBIDDEN
------------------
This module MUST NOT be imported by, or import from:
  * any execution layer (paper trading, broker routing, options).
  * the live decision stack (Strategist / Auditor / Commander runtime,
    confidence gate, conviction service, risk multipliers).
  * any memory writer (toxic_lessons, prediction_tracker writes,
    ai_alerts writes, regime_memory_retrieval.ingest_memory).

Boundary discipline (so the contract stays auditable):
    Labeler   → labels history
    Tracker   → measures performance (this file)
    Commander / Council / Auditor runtime → not touched
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


# ─── DATA STRUCTURES ──────────────────────────────────────────────────


@dataclass
class ResolvedTradeRow:
    """A single resolved trade enriched with regime labels.

    ``win`` and ``pnl_pct`` may be ``None`` for unresolved rows; those
    rows are counted as ``unresolved`` and excluded from win-rate /
    avg-pnl / calibration-gap math.
    """

    symbol: str
    date: str
    action: str
    confidence: Optional[float]
    win: Optional[bool]
    pnl_pct: Optional[float]

    # Labeler-emitted fields
    regime_label: str
    vix_level: str
    yield_curve: str
    liquidity: str
    market_event: str
    event_family: str
    is_crisis: bool
    regime_id: str


@dataclass
class RegimePerformance:
    bucket: str
    samples: int
    wins: int
    losses: int
    unresolved: int
    win_rate: Optional[float]
    avg_pnl_pct: Optional[float]
    avg_confidence: Optional[float]
    calibration_gap: Optional[float]  # ``avg_confidence - win_rate``


# ─── TRACKER ──────────────────────────────────────────────────────────


class RegimePerformanceTracker:
    """Analytics-only tracker. Stateless. Pure functions over rows."""

    @staticmethod
    def _safe_avg(values: List[Optional[float]]) -> Optional[float]:
        clean = [v for v in values if v is not None]
        if not clean:
            return None
        return sum(clean) / len(clean)

    def _summarize_bucket(
        self,
        bucket: str,
        rows: List[ResolvedTradeRow],
    ) -> RegimePerformance:
        resolved = [r for r in rows if r.win is not None]
        wins = sum(1 for r in resolved if r.win is True)
        losses = sum(1 for r in resolved if r.win is False)
        unresolved = len(rows) - len(resolved)

        win_rate = wins / len(resolved) if resolved else None
        avg_pnl = self._safe_avg(
            [r.pnl_pct for r in resolved if r.pnl_pct is not None]
        )
        avg_conf = self._safe_avg(
            [r.confidence for r in resolved if r.confidence is not None]
        )

        calibration_gap: Optional[float] = None
        if win_rate is not None and avg_conf is not None:
            calibration_gap = avg_conf - win_rate

        return RegimePerformance(
            bucket=bucket,
            samples=len(rows),
            wins=wins,
            losses=losses,
            unresolved=unresolved,
            win_rate=win_rate,
            avg_pnl_pct=avg_pnl,
            avg_confidence=avg_conf,
            calibration_gap=calibration_gap,
        )

    def group_by(
        self,
        rows: List[ResolvedTradeRow],
        field: str,
    ) -> Dict[str, RegimePerformance]:
        buckets: Dict[str, List[ResolvedTradeRow]] = defaultdict(list)
        for row in rows:
            value = getattr(row, field, "unknown")
            buckets[str(value)].append(row)
        return {
            bucket: self._summarize_bucket(bucket, bucket_rows)
            for bucket, bucket_rows in buckets.items()
        }

    def full_report(self, rows: List[ResolvedTradeRow]) -> Dict[str, Any]:
        """Per-dimension performance breakdown.

        The envelope intentionally carries explicit safety flags so any
        downstream consumer that accidentally tries to use this for
        sizing / direction / memory promotion cannot pretend the
        contract was unclear.
        """
        return {
            "safe_for_trade_promotion": False,
            "safe_for_memory": False,
            "can_change_direction": False,
            "can_increase_risk": False,
            "reports": {
                "by_market_event": {
                    k: asdict(v)
                    for k, v in self.group_by(rows, "market_event").items()
                },
                "by_event_family": {
                    k: asdict(v)
                    for k, v in self.group_by(rows, "event_family").items()
                },
                "by_regime_label": {
                    k: asdict(v)
                    for k, v in self.group_by(rows, "regime_label").items()
                },
                "by_vix_level": {
                    k: asdict(v)
                    for k, v in self.group_by(rows, "vix_level").items()
                },
                "by_liquidity": {
                    k: asdict(v)
                    for k, v in self.group_by(rows, "liquidity").items()
                },
                "by_yield_curve": {
                    k: asdict(v)
                    for k, v in self.group_by(rows, "yield_curve").items()
                },
                "by_regime_id": {
                    k: asdict(v)
                    for k, v in self.group_by(rows, "regime_id").items()
                },
            },
        }


# ─── EXAMPLE (smoke only — not imported) ─────────────────────────────


if __name__ == "__main__":  # pragma: no cover
    from pprint import pprint

    rows = [
        ResolvedTradeRow(
            symbol="NVDA", date="2024-06-01", action="BUY",
            confidence=0.72, win=True, pnl_pct=1.8,
            regime_label="expansion", vix_level="low",
            yield_curve="inverted", liquidity="high",
            market_event="AI_BUBBLE", event_family="bubble",
            is_crisis=False, regime_id="abc123",
        ),
        ResolvedTradeRow(
            symbol="SPY", date="2020-03-16", action="SELL",
            confidence=0.68, win=False, pnl_pct=-2.4,
            regime_label="contraction", vix_level="extreme",
            yield_curve="flat", liquidity="thin",
            market_event="COVID_CRASH", event_family="crash",
            is_crisis=True, regime_id="xyz789",
        ),
    ]
    pprint(RegimePerformanceTracker().full_report(rows))
