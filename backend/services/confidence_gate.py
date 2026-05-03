"""
Confidence Gate — drawdown / loss-streak / calibration-gap aware threshold.

Replaces the static ``_MIN_PAPER_CONFIDENCE`` with a dynamic version that
raises the bar when the system is in a drawdown, on a loss streak, or
when the calibration gap (predicted vs actual win rate) widens.

Imported and applied in BOTH cores (equity ``ml_paper_trader`` + crypto
``crypto_paper_trader``) BEFORE the existing min-confidence check, so the
gate composes cleanly with the symbol_failure_memory penalty and the
Sovereign AI bounded contribution.

Pure threshold ladder (matches operator spec):

| Condition                     | Threshold delta |
|-------------------------------|-----------------|
| drawdown >= 10%               | +0.05           |
| drawdown >= 15%               | +0.10           |
| calibration_gap >= 8%         | +0.05           |
| loss_streak >= 3              | +0.05           |
| (cap)                         | 0.90 max        |

The 10% and 15% drawdown branches stack, so a 15% drawdown contributes
+0.15 (not +0.10). Operator-confirmed semantics.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Base threshold — overridable via env so ops can shift the floor.
BASE_MIN_CONFIDENCE: float = float(
    os.environ.get("CONFIDENCE_GATE_BASE", "0.70")
)
MAX_THRESHOLD: float = float(
    os.environ.get("CONFIDENCE_GATE_CAP", "0.90")
)


@dataclass(frozen=True)
class ConfidenceThreshold:
    """Result of the dynamic threshold calculation."""

    base: float
    delta: float
    threshold: float
    drawdown: float
    loss_streak: int
    calibration_gap: float
    reasons: list[str]

    def passes(self, confidence: float) -> bool:
        return confidence >= self.threshold


def compute_dynamic_threshold(
    *,
    drawdown: float = 0.0,
    loss_streak: int = 0,
    calibration_gap: float = 0.0,
    base: float = BASE_MIN_CONFIDENCE,
) -> ConfidenceThreshold:
    """Pure threshold computation. Tested independently of any I/O."""
    delta = 0.0
    reasons: list[str] = []

    # Drawdown branches stack — operator spec: +0.05 at 10% AND +0.10 at 15%.
    if drawdown >= 0.10:
        delta += 0.05
        reasons.append("drawdown>=10%_+0.05")
    if drawdown >= 0.15:
        delta += 0.10
        reasons.append("drawdown>=15%_+0.10")

    if calibration_gap >= 0.08:
        delta += 0.05
        reasons.append("calibration_gap>=8%_+0.05")

    if loss_streak >= 3:
        delta += 0.05
        reasons.append("loss_streak>=3_+0.05")

    if not reasons:
        reasons.append("baseline")

    threshold = min(base + delta, MAX_THRESHOLD)
    return ConfidenceThreshold(
        base=base,
        delta=round(delta, 4),
        threshold=round(threshold, 4),
        drawdown=round(drawdown, 4),
        loss_streak=loss_streak,
        calibration_gap=round(calibration_gap, 4),
        reasons=reasons,
    )


async def _read_drawdown_and_loss_streak(
    db: Any,
    *,
    asset_type: Literal["equity", "crypto"],
) -> tuple[float, int]:
    """Pull ``max_drawdown`` + ``loss_streak`` from the existing track-record
    builder. Fail-safe: any error returns (0.0, 0)."""
    try:
        if asset_type == "crypto":
            from services.risk_budget_gateway import build_track_record_for_crypto_bot
            track = await build_track_record_for_crypto_bot()
        else:
            # Equity — same generic builder, scoped to paper_trades
            from services.risk_budget_gateway import _build_track_record_generic
            track = await _build_track_record_generic(
                collection_name="paper_trades",
                match={"status": "closed"},
                veto_collection=None,
                veto_match=None,
                lookback_days=30,
            )
        return float(track.max_drawdown or 0.0), int(track.loss_streak or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[confidence_gate] track lookup failed (%s): %s", asset_type, exc)
        return 0.0, 0


async def _read_calibration_gap(
    db: Any,
    *,
    asset_type: Literal["equity", "crypto"],
) -> float:
    """Average of two calibration-gap sources:

    1. ``equity_shadow_promotion``-style win-rate vs avg-confidence delta
       on the existing paper trades.
    2. Sovereign decisions resolved-rate vs avg-conviction delta.

    Fail-safe: missing data degrades to 0.0 (no penalty). The two sources
    are averaged when both are available; if only one is, it stands alone.
    """
    if db is None:
        return 0.0

    gaps: list[float] = []

    # Source 1 — paper_trades native: win_rate vs avg confidence
    try:
        coll = "paper_trades" if asset_type == "equity" else "crypto_paper_trades"
        match: dict[str, Any] = {"status": "closed", "outcome": {"$in": ["win", "loss", "flat"]}}
        pipe = [
            {"$match": match},
            {"$group": {
                "_id": None,
                "wins": {"$sum": {"$cond": [{"$eq": ["$outcome", "win"]}, 1, 0]}},
                "total": {"$sum": 1},
                "avg_conf": {"$avg": "$confidence"},
            }},
        ]
        agg = await db[coll].aggregate(pipe).to_list(length=1)
        if agg and agg[0]["total"] >= 10:
            row = agg[0]
            win_rate = row["wins"] / row["total"]
            avg_conf = float(row.get("avg_conf") or 0.0)
            # Calibration gap: if the system claimed avg confidence X but
            # actual win rate is X-Y, that's a Y-point calibration gap.
            gap = max(0.0, avg_conf - win_rate)
            gaps.append(gap)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[confidence_gate] paper_trades calibration probe failed: %s", exc)

    # Source 2 — sovereign_decisions resolved subset
    try:
        sov_coll = "sovereign_decisions"
        horizon_key = "outcomes.60m"
        sov_match: dict[str, Any] = {
            "asset_type": asset_type,
            "resolved": True,
            horizon_key: {"$exists": True},
        }
        pipe2 = [
            {"$match": sov_match},
            {"$group": {
                "_id": None,
                "rights": {"$sum": {"$cond": [{"$eq": [f"${horizon_key}.was_right", True]}, 1, 0]}},
                "total": {"$sum": 1},
                "avg_conf": {"$avg": "$confidence"},
            }},
        ]
        agg2 = await db[sov_coll].aggregate(pipe2).to_list(length=1)
        if agg2 and agg2[0]["total"] >= 10:
            row = agg2[0]
            right_rate = row["rights"] / row["total"]
            avg_conf = float(row.get("avg_conf") or 0.0)
            gaps.append(max(0.0, avg_conf - right_rate))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[confidence_gate] sovereign calibration probe failed: %s", exc)

    if not gaps:
        return 0.0
    return sum(gaps) / len(gaps)


async def get_dynamic_confidence_threshold(
    db: Any,
    *,
    asset_type: Literal["equity", "crypto"] = "equity",
) -> ConfidenceThreshold:
    """Public entry — compute the dynamic threshold from live state.

    Reads:
    * ``max_drawdown`` + ``loss_streak`` from the track-record builder.
    * ``calibration_gap`` averaged across paper_trades and
      sovereign_decisions resolved subsets.

    Never raises — any data hiccup degrades to the static baseline.
    """
    drawdown, loss_streak = await _read_drawdown_and_loss_streak(
        db, asset_type=asset_type,
    )
    cal_gap = await _read_calibration_gap(db, asset_type=asset_type)
    return compute_dynamic_threshold(
        drawdown=drawdown,
        loss_streak=loss_streak,
        calibration_gap=cal_gap,
    )
