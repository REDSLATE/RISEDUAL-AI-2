"""
Recompute the LearningEngine ``_LE_STATS`` global doc + repair
mis-flagged per-trade rows in ``learning_engine_trades``.

The 2026-05-01 direction-token bug (see
``backfill_strong_direction_grades.py``) produced two distinct kinds of
LE corruption:

  Phase 1 — per-trade poisoning
  ─────────────────────────────
  Predictions whose `direction` was STRONG_BUY/WEAK_BUY were silently
  mapped to ``ai_dir="SHORT"`` by the local hardcoded tuple at
  ``prediction_tracker.py:654``. The resolve path then matched a real
  SHORT pending trade (an unrelated user position), wrote the
  prediction's "correct" flag as ``status="win"`` / ``win=True`` on
  that row, and computed PnL using the SHORT formula
  ``(entry - exit_price)``. Result: rows where ``pnl < 0`` but
  ``win == True`` (or ``pnl > 0`` and ``win == False``).

  Phase 2 — rollup divergence
  ───────────────────────────
  ``_LE_STATS.global`` accumulates via ``$inc``, so even after the
  per-trade rows are repaired the rollup needs a re-aggregation to
  catch up with truth.

This script handles BOTH phases in order:

    1. Find rows where ``sign(pnl)`` and ``win`` disagree, flip
       ``status`` / ``win`` to match the math, log every change.
    2. Re-aggregate ``_LE_STATS.global`` from the corrected per-trade
       collection.

Usage
-----
    python -m scripts.recompute_learning_engine_stats --dry-run
    python -m scripts.recompute_learning_engine_stats --apply

Notes
-----
* Mongo writes are idempotent: re-running on a clean collection is a
  no-op (Phase 1 finds 0 rows to flip, Phase 2 produces a doc identical
  to the existing one).
* The previous rollup is moved to ``_id="global_pre_<run_id>"`` on apply
  for one-line rollback.
* Phase 1 changes are appended to ``learning_engine_trade_repair_log``
  for audit / replay.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from motor.motor_asyncio import AsyncIOMotorClient

from ai_core.learning_engine import _TRADES, _STATS, _STATS_DOC_ID

logger = logging.getLogger("recompute_le_stats")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

REPAIR_LOG = "learning_engine_trade_repair_log"
REASON = "direction_token_bug_v1"


# ── Phase 1: per-trade repair ─────────────────────────────────────


async def repair_mis_flagged_trades(db, *, dry_run: bool, run_id: str) -> dict:
    """Flip ``status`` / ``win`` on rows where ``pnl`` sign disagrees.

    A resolved trade's truth: if ``pnl > 0`` → win, ``pnl < 0`` → loss,
    ``pnl == 0`` → leave alone (rare, ambiguous). The DIRECTION on the
    row IS believed to be correct — it came from the trade's own
    side, not the prediction. Only the win/status flag is fixed.

    Pending trades and trades with ``pnl == None`` are skipped — they
    have no math to verify.
    """
    cursor = db[_TRADES].find(
        {"status": {"$in": ["win", "loss"]}, "pnl": {"$ne": None}},
        {"_id": 1, "asset": 1, "direction": 1, "entry": 1, "exit_price": 1,
         "pnl": 1, "r_multiple": 1, "status": 1, "win": 1, "user_id": 1},
    )

    flips: list[dict[str, Any]] = []
    scanned = 0
    async for r in cursor:
        scanned += 1
        try:
            pnl = float(r.get("pnl"))
        except (TypeError, ValueError):
            continue
        if pnl == 0.0:
            continue
        old_status = r.get("status")
        old_win = bool(r.get("win"))
        true_win = pnl > 0
        true_status = "win" if true_win else "loss"

        if old_win == true_win and old_status == true_status:
            continue  # already correct

        flips.append({
            "_id_str": str(r["_id"]),
            "asset": r.get("asset"),
            "direction": r.get("direction"),
            "entry": r.get("entry"),
            "exit_price": r.get("exit_price"),
            "pnl": pnl,
            "r_multiple": r.get("r_multiple"),
            "old_status": old_status,
            "new_status": true_status,
            "old_win": old_win,
            "new_win": true_win,
            "user_id": r.get("user_id"),
            "reason": REASON,
            "run_id": run_id,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
        })

        if not dry_run:
            await db[_TRADES].update_one(
                {"_id": r["_id"]},
                {"$set": {
                    "status": true_status,
                    "win": true_win,
                    "repaired_by": REASON,
                    "repaired_at": datetime.now(timezone.utc).isoformat(),
                    "repaired_run_id": run_id,
                    "original_status_before_repair": old_status,
                    "original_win_before_repair": old_win,
                }},
            )

    if flips and not dry_run:
        await db[REPAIR_LOG].insert_many(
            [{"_id": str(uuid4()), **f} for f in flips]
        )

    logger.info("─" * 70)
    logger.info("Phase 1 — per-trade repair (dry_run=%s)", dry_run)
    logger.info("─" * 70)
    logger.info("Scanned resolved trades : %d", scanned)
    logger.info(
        "%s : %d",
        "Would flip" if dry_run else "Flipped",
        len(flips),
    )
    for f in flips[:10]:
        logger.info(
            "  %-6s %-5s entry=%-7s exit=%-7s pnl=%-8s : %s/win=%s → %s/win=%s",
            f["asset"], f["direction"], f["entry"], f["exit_price"],
            f["pnl"], f["old_status"], f["old_win"],
            f["new_status"], f["new_win"],
        )
    return {"scanned": scanned, "flips": flips}


# ── Phase 2: rollup re-aggregation ────────────────────────────────


async def aggregate_truth(db) -> dict[str, Any]:
    counts: dict[str, int] = {"win": 0, "loss": 0, "pending": 0, "total_resolved": 0}
    r_sum = 0.0
    pnl_sum = 0.0

    cursor = db[_TRADES].find(
        {},
        {"_id": 0, "status": 1, "r_multiple": 1, "pnl": 1},
    )
    async for row in cursor:
        status = row.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
        if status in ("win", "loss"):
            counts["total_resolved"] += 1
            if row.get("r_multiple") is not None:
                try:
                    r_sum += float(row["r_multiple"])
                except (TypeError, ValueError):
                    pass
            if row.get("pnl") is not None:
                try:
                    pnl_sum += float(row["pnl"])
                except (TypeError, ValueError):
                    pass

    return {
        "_id": _STATS_DOC_ID,
        "counts": counts,
        "running": {
            "r_multiple_sum": round(r_sum, 6),
            "pnl_sum": round(pnl_sum, 4),
        },
        "last_update": datetime.now(timezone.utc).isoformat(),
        "recomputed_by": "recompute_learning_engine_stats_v1",
    }


def _diff_summary(old: dict | None, new: dict) -> list[str]:
    out: list[str] = []
    old_counts = (old or {}).get("counts", {})
    new_counts = new["counts"]
    for k in ("win", "loss", "pending", "total_resolved"):
        a, b = int(old_counts.get(k, 0)), int(new_counts.get(k, 0))
        marker = "→" if a == b else "→ ⚠"
        out.append(f"  counts.{k:<15s} {a!s:>6} {marker} {b}")
    old_running = (old or {}).get("running", {})
    new_running = new["running"]
    for k in ("r_multiple_sum", "pnl_sum"):
        a = float(old_running.get(k, 0.0))
        b = float(new_running.get(k, 0.0))
        marker = "→" if abs(a - b) < 1e-6 else "→ ⚠"
        out.append(f"  running.{k:<15s} {a:>10.4f} {marker} {b:.4f}")
    ow = old_counts.get("win", 0); ot = old_counts.get("total_resolved", 0)
    nw = new_counts["win"]; nt = new_counts["total_resolved"]
    ow_pct = (ow / ot * 100) if ot else 0.0
    nw_pct = (nw / nt * 100) if nt else 0.0
    out.append(f"  → derived win_rate     {ow_pct:>5.1f}% → {nw_pct:.1f}%")
    return out


async def recompute_rollup(db, *, dry_run: bool, run_id: str) -> dict:
    old = await db[_STATS].find_one({"_id": _STATS_DOC_ID})
    truth = await aggregate_truth(db)

    logger.info("─" * 70)
    logger.info("Phase 2 — rollup re-aggregation (dry_run=%s)", dry_run)
    logger.info("─" * 70)
    for line in _diff_summary(old, truth):
        logger.info(line)

    if dry_run:
        return {"old": old, "truth": truth, "applied": False}

    if old:
        backup_id = f"global_pre_{run_id}"
        backup = {**old, "_id": backup_id, "archived_at": datetime.now(timezone.utc).isoformat()}
        await db[_STATS].insert_one(backup)
        logger.info("Backed up old rollup to _id=%s", backup_id)

    await db[_STATS].replace_one({"_id": _STATS_DOC_ID}, truth, upsert=True)
    logger.info("Replaced _LE_STATS.global with recomputed truth.")
    return {"old": old, "truth": truth, "applied": True}


async def run(*, dry_run: bool) -> dict:
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    run_id = f"recompute_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid4().hex[:6]}"

    logger.info("=" * 70)
    logger.info("LearningEngine recompute (run_id=%s, dry_run=%s)", run_id, dry_run)
    logger.info("=" * 70)

    p1 = await repair_mis_flagged_trades(db, dry_run=dry_run, run_id=run_id)
    p2 = await recompute_rollup(db, dry_run=dry_run, run_id=run_id)

    logger.info("=" * 70)
    if dry_run:
        logger.info("Dry-run only — no writes. Use --apply to commit.")
    else:
        logger.info("DONE. run_id=%s", run_id)
    return {"phase1": p1, "phase2": p2, "run_id": run_id}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = p.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
