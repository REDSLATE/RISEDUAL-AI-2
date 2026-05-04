"""
One-off: fit a confidence calibration model from the last 90 days of
verified predictions and persist it as the active model.

Why this is a script, not a scheduled job
─────────────────────────────────────────
The first calibration is reviewed by a human before being trusted to
back the Tier 3 readiness gate (per the operator decision on
2026-05-04). Once we have a few weeks of "calibrated readiness"
evidence we can promote this to a weekly APScheduler tick; not yet.

Usage
─────
    cd /app/backend
    python -m scripts.fit_calibration_from_history          # apply
    python -m scripts.fit_calibration_from_history --print  # don't write,
                                                            # just show

The ``--print`` mode runs the same fit logic but does not persist
the new active model — useful for previewing the ECE-before/after
delta before committing.

Exit codes
──────────
* 0 — success (model fit + persisted, OR --print produced output)
* 1 — insufficient rows (no model written; existing one keeps serving)
* 2 — Mongo I/O failure
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient


async def _amain(args: argparse.Namespace) -> int:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("error: MONGO_URL / DB_NAME not set", file=sys.stderr)
        return 2

    # Lazy import after env validation so the script fails fast on
    # missing config without spinning up sklearn.
    from services.calibration_service import (
        LOOKBACK_DAYS, MIN_CALIBRATION_ROWS, MAX_CALIBRATED_CONFIDENCE,
        fit_isotonic_calibration, get_active_calibration,
    )

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    if args.print_only:
        # Run the fit but don't trust the persistence side-effect to
        # roll back cleanly. Cheaper to fit-and-revert.
        prior = await get_active_calibration(db)
        new = await fit_isotonic_calibration(
            db,
            lookback_days=args.lookback_days,
            min_rows=args.min_rows,
            max_calibrated=args.max_calibrated,
        )
        if new is None:
            print("preview: insufficient rows for fit "
                  f"(need >= {args.min_rows} verified)")
            return 1
        # Roll back: mark the new doc inactive, restore the prior one.
        await db["calibration_models"].update_one(
            {"version": new["version"]}, {"$set": {"active": False}},
        )
        if prior is not None:
            await db["calibration_models"].update_one(
                {"version": prior["version"]}, {"$set": {"active": True}},
            )
        _print_summary(new, prefix="PREVIEW")
        return 0

    new = await fit_isotonic_calibration(
        db,
        lookback_days=args.lookback_days,
        min_rows=args.min_rows,
        max_calibrated=args.max_calibrated,
    )
    if new is None:
        print(f"insufficient rows for fit "
              f"(need >= {args.min_rows} verified in last "
              f"{args.lookback_days} days)")
        return 1

    _print_summary(new, prefix="ACTIVE")
    return 0


def _print_summary(doc: dict, *, prefix: str) -> None:
    print(f"[{prefix}] version           : {doc['version']}")
    print(f"[{prefix}] n_rows            : {doc['n_rows']}")
    print(f"[{prefix}] ece_before_pp     : {doc['ece_before_pp']}")
    print(f"[{prefix}] ece_after_pp      : {doc['ece_after_pp']}")
    print(f"[{prefix}] max_calibrated    : {doc['max_calibrated_confidence']}")
    print(f"[{prefix}] applies_to        : {doc['calibration_applies_to']}")
    print(f"[{prefix}] knots ({len(doc['knots'])}):")
    for k in doc["knots"]:
        print(f"   x={k['x']:.3f}  →  y={k['y']:.3f}")


def _parser() -> argparse.ArgumentParser:
    from services.calibration_service import (
        LOOKBACK_DAYS, MIN_CALIBRATION_ROWS, MAX_CALIBRATED_CONFIDENCE,
    )
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    p.add_argument("--min-rows", type=int, default=MIN_CALIBRATION_ROWS)
    p.add_argument("--max-calibrated", type=float, default=MAX_CALIBRATED_CONFIDENCE)
    p.add_argument(
        "--print", dest="print_only", action="store_true",
        help="Preview the fit without persisting the new active model.",
    )
    return p


if __name__ == "__main__":
    args = _parser().parse_args()
    sys.exit(asyncio.run(_amain(args)))
