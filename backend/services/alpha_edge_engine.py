"""Alpha Edge Engine — non-blocking confidence modifier.

Reads resolved outcomes from ``alpha_outcomes`` and computes expectancy
per ``(pattern × regime × time_bucket × rvol_bucket)``. Returns a
modifier in the range ``[0.75, 1.15]`` that Alpha applies to
``TradeIntent.confidence`` before handing off to the executor.

Design principles
-----------------
1. **Never a hard gate.** Fewer than 10 independent samples → DISCOVERING,
   neutral 1.00 modifier. Positive edge → mild boost. Negative edge →
   mild reduction. Nothing here can *stop* a trade.
2. **No Cartesian product explosion.** Buckets only exist once they
   have real observations. We never materialize (pattern × regime × ...
   × all combinations) upfront.
3. **Storage split.** Only compact rollups go to Mongo. The Edge
   Engine reads outcomes on demand for rollup compute; raw event
   telemetry stays in the SQLite hot store.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── modifier table (from user spec) ─────────────────────────────


def edge_modifier(expectancy_r: Optional[float], samples: int) -> float:
    if samples < 10 or expectancy_r is None:
        return 1.00  # DISCOVERING — never punish new setups
    if expectancy_r >= 0.50:
        return 1.15
    if expectancy_r >= 0.20:
        return 1.07
    if expectancy_r >= 0.00:
        return 1.00
    if expectancy_r >= -0.20:
        return 0.90
    return 0.75


def edge_state(expectancy_r: Optional[float], samples: int) -> str:
    if samples < 10 or expectancy_r is None:
        return "DISCOVERING"
    if expectancy_r > 0:
        return "POSITIVE"
    if expectancy_r < 0:
        return "NEGATIVE"
    return "FLAT"


# ─── bucket helpers ──────────────────────────────────────────────


def time_bucket(dt: datetime) -> str:
    """ET-approximate 60-min bucket. Uses same DST logic as the RTH gate."""
    month, day = dt.month, dt.day
    is_dst = ((month > 3 or (month == 3 and day >= 8)) and
              (month < 11 or (month == 11 and day <= 7)))
    offset = 4 if is_dst else 5
    et_hour = (dt.hour - offset) % 24
    return f"{et_hour:02d}:00-{et_hour+1:02d}:00"


def rvol_bucket(rvol: float) -> str:
    if rvol < 1.0:
        return "rvol_lt_1"
    if rvol < 2.0:
        return "rvol_1_2"
    if rvol < 5.0:
        return "rvol_2_5"
    return "rvol_gt_5"


def spread_bucket(bps: float) -> str:
    if bps < 20:
        return "tight_lt_20"
    if bps < 50:
        return "med_20_50"
    return "wide_gt_50"


# ─── rollup compute ──────────────────────────────────────────────


def _summarize(rows: list[dict]) -> dict:
    r_values = [float(r["realized_r"]) for r in rows
                if isinstance(r.get("realized_r"), (int, float))]
    if not r_values:
        return {"samples": len(rows), "expectancy_r": None,
                "state": "DISCOVERING", "modifier": 1.00}
    wins = [x for x in r_values if x > 0]
    losses = [x for x in r_values if x < 0]
    win_rate = len(wins) / len(r_values)
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    expectancy = win_rate * avg_win + (1.0 - win_rate) * avg_loss
    samples = len(r_values)
    return {
        "samples": samples,
        "win_rate": round(win_rate, 3),
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3),
        "expectancy_r": round(expectancy, 3),
        "state": edge_state(expectancy, samples),
        "modifier": edge_modifier(expectancy, samples),
    }


async def compute_rollups(db: Any) -> dict:
    """Group ``alpha_outcomes`` by (pattern × slow_regime × fast_regime)
    and persist compact rollups.

    Only rows with ``realized_r`` are treated as resolved samples — an
    outcome that never got a real fill (execute_switch_off, executor_rejected,
    exec_lock_conflict, invalidated_before_trigger) is counted as
    "observed but not measured" and never poisons the expectancy.
    Every measured outcome is one unique resolved setup (dedup applied
    at setup-creation time), so counts are honest samples.
    """
    if db is None:
        return {"rollups": {}, "computed_at": datetime.now(timezone.utc).isoformat()}
    grouped: dict[tuple, list[dict]] = {}
    observed_by_key: dict[tuple, int] = {}
    async for row in db.alpha_outcomes.find({}, {"_id": 0}):
        pattern = row.get("setup_type") or "unknown"
        slow = row.get("regime") or "UNKNOWN"
        fast = row.get("fast_regime") or "UNKNOWN"
        key = (pattern, slow, fast)
        observed_by_key[key] = observed_by_key.get(key, 0) + 1
        # Only rows with a realized_r are edge-sample-worthy.
        if isinstance(row.get("realized_r"), (int, float)):
            grouped.setdefault(key, []).append(row)

    now = datetime.now(timezone.utc)
    rollups: list[dict] = []
    for key, rows in grouped.items():
        pattern, slow, fast = key
        summary = _summarize(rows)
        summary.update({
            "pattern": pattern,
            "slow_regime": slow,
            "fast_regime": fast,
            "regime": slow,  # back-compat with the old panel column
            "observed": observed_by_key.get(key, 0),
            "updated_at": now.isoformat(),
        })
        rollups.append(summary)
        try:
            await db.alpha_edge_rollups.update_one(
                {"pattern": pattern, "slow_regime": slow, "fast_regime": fast},
                {"$set": {**summary, "updated_at": now}},
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[alpha_edge_engine] rollup write failed: %s", exc)
    return {"rollups": rollups, "computed_at": now.isoformat()}


async def read_rollups(db: Any) -> list[dict]:
    if db is None:
        return []
    out: list[dict] = []
    cursor = db.alpha_edge_rollups.find({}, {"_id": 0})
    async for row in cursor:
        if isinstance(row.get("updated_at"), datetime):
            row["updated_at"] = row["updated_at"].isoformat()
        out.append(row)
    out.sort(key=lambda r: (r.get("expectancy_r") or -999.0), reverse=True)
    return out


# ─── lookup (called from the tick before intent creation) ────────


async def lookup(db: Any, *, pattern: str, regime: str,
                  fast_regime: str = "UNKNOWN") -> dict:
    """Return the current edge summary for (pattern × slow_regime × fast_regime).

    Precedence when the fine-grained bucket has too few samples:
      1. (pattern, slow, fast) — most specific
      2. (pattern, slow, *)   — collapse fast_regime
      3. neutral DISCOVERING
    A DISCOVERING result **must** be treated as neutral, never as a rejection.
    """
    if db is None:
        return {"pattern": pattern, "slow_regime": regime, "fast_regime": fast_regime,
                "state": "DISCOVERING", "modifier": 1.00, "samples": 0}
    # Try the fine-grained bucket first
    for query in (
        {"pattern": pattern, "slow_regime": regime, "fast_regime": fast_regime},
        {"pattern": pattern, "slow_regime": regime},
    ):
        try:
            doc = await db.alpha_edge_rollups.find_one(query, {"_id": 0})
        except Exception:  # noqa: BLE001
            doc = None
        if doc and (doc.get("samples") or 0) >= 10:
            if isinstance(doc.get("updated_at"), datetime):
                doc["updated_at"] = doc["updated_at"].isoformat()
            return doc
    # Neither bucket has enough samples yet.
    return {"pattern": pattern, "slow_regime": regime, "fast_regime": fast_regime,
            "state": "DISCOVERING", "modifier": 1.00, "samples": 0}


__all__ = [
    "edge_modifier",
    "edge_state",
    "time_bucket",
    "rvol_bucket",
    "spread_bucket",
    "compute_rollups",
    "read_rollups",
    "lookup",
]
