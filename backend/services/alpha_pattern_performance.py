"""Alpha Pattern Performance — compact Mongo rollups.

Reads resolved outcomes from ``alpha_outcomes`` (one doc per resolved
setup, dedup enforced at setup-creation time so each row is an
independent sample) and produces a compact rollup by pattern.

Storage
-------
* Reads: ``alpha_outcomes`` (Mongo)
* Writes: ``alpha_pattern_rollups`` (Mongo, one doc per pattern) —
  small, operator-facing summary. Nothing raw goes here.

Nothing in this module gates trading. It is read-only telemetry.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def _iter_outcomes(db: Any) -> list[dict]:
    """Return all resolved outcomes as a list of plain dicts.

    In-memory aggregation is fine because ``alpha_outcomes`` has one
    row per resolved setup (dedup applied upstream) and is naturally
    bounded by trading volume. When it grows large we can move this
    to a $group aggregation.
    """
    if db is None:
        return []
    out: list[dict] = []
    cursor = db.alpha_outcomes.find({}, {"_id": 0})
    async for row in cursor:
        out.append(row)
    return out


def _summarize(rows: list[dict]) -> dict:
    """Compute the compact metrics operators want to see."""
    if not rows:
        return {
            "unique_setups": 0, "triggered": 0, "executed": 0,
            "wins": 0, "losses": 0, "win_rate": None,
            "expectancy_r": None, "profit_factor": None,
            "median_mfe_r": None, "median_mae_r": None,
            "avg_slippage_bps": None, "sample_confidence": "low",
        }
    triggered = sum(1 for r in rows if r.get("triggered"))
    executed = sum(1 for r in rows if r.get("order_submitted"))
    r_values = [float(r["realized_r"]) for r in rows
                if isinstance(r.get("realized_r"), (int, float))]
    wins = [x for x in r_values if x > 0]
    losses = [x for x in r_values if x < 0]
    win_rate = (len(wins) / len(r_values)) if r_values else None
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    expectancy = None
    if win_rate is not None:
        expectancy = win_rate * avg_win + (1.0 - win_rate) * avg_loss
    pf = None
    if losses:
        pf = sum(wins) / abs(sum(losses)) if sum(losses) < 0 else None
    mfe = [float(r["mfe_r"]) for r in rows if isinstance(r.get("mfe_r"), (int, float))]
    mae = [float(r["mae_r"]) for r in rows if isinstance(r.get("mae_r"), (int, float))]
    slip = [float(r["slippage_bps"]) for r in rows if isinstance(r.get("slippage_bps"), (int, float))]
    sample_n = len(rows)
    if sample_n < 10:
        confidence = "low"
    elif sample_n < 30:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "unique_setups": sample_n,
        "triggered": triggered,
        "executed": executed,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 3) if win_rate is not None else None,
        "avg_win_r": round(avg_win, 3) if wins else None,
        "avg_loss_r": round(avg_loss, 3) if losses else None,
        "expectancy_r": round(expectancy, 3) if expectancy is not None else None,
        "profit_factor": round(pf, 3) if pf is not None else None,
        "median_mfe_r": round(statistics.median(mfe), 3) if mfe else None,
        "median_mae_r": round(statistics.median(mae), 3) if mae else None,
        "avg_slippage_bps": round(statistics.mean(slip), 2) if slip else None,
        "sample_confidence": confidence,
    }


async def compute_rollups(db: Any) -> dict:
    """Compute the rollups and persist compact docs to Mongo."""
    outcomes = await _iter_outcomes(db)
    by_pattern: dict[str, list[dict]] = {}
    for r in outcomes:
        by_pattern.setdefault(r.get("setup_type") or "unknown", []).append(r)
    now = datetime.now(timezone.utc)
    rollups: dict[str, dict] = {}
    for pattern, rows in by_pattern.items():
        summary = _summarize(rows)
        summary["pattern"] = pattern
        summary["updated_at"] = now.isoformat()
        rollups[pattern] = summary
        if db is not None:
            try:
                await db.alpha_pattern_rollups.update_one(
                    {"_id": pattern},
                    {"$set": {**summary, "updated_at": now}},
                    upsert=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[alpha_pattern_perf] write failed: %s", exc)
    return {"rollups": rollups, "computed_at": now.isoformat(),
            "total_outcomes": len(outcomes)}


async def read_rollups(db: Any) -> list[dict]:
    if db is None:
        return []
    out: list[dict] = []
    cursor = db.alpha_pattern_rollups.find({}, {"_id": 0})
    async for row in cursor:
        if isinstance(row.get("updated_at"), datetime):
            row["updated_at"] = row["updated_at"].isoformat()
        out.append(row)
    out.sort(key=lambda r: (r.get("expectancy_r") or -999.0), reverse=True)
    return out


__all__ = ["compute_rollups", "read_rollups"]
