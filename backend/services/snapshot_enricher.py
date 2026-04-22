"""Snapshot-enricher — stamps execution economics onto a
`features_snapshots` row once the prediction resolves.

Why post-resolve and not at capture-time?
  * `entry_price` and `direction` *could* be stamped at capture time,
    but `exit_price` and `stop_loss` only exist after the trade
    closes. Doing all four in one place keeps the audit trail
    consistent (a snapshot either has the full execution block or
    none of it) and keeps the hypothesis-log hot path untouched.

Canonical call site:
    from services.snapshot_enricher import stamp_execution_on_snapshot

    await stamp_execution_on_snapshot(
        db=db,
        prediction_id=pred["prediction_id"],
        entry_price=float(entry),
        exit_price=float(price_now),
        stop_loss=float(stop) if stop else None,
        direction="LONG" or "SHORT",
    )

Once enough snapshots carry these fields, `ml_retrain_service` can
route R-multiple-weighted training via
`ai_core.risk_weighting.compute_sample_weight_from_trade`.

Never raises — a Mongo hiccup must not break the outcome-labeling
pipeline, which is the primary caller.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_COLLECTION = "features_snapshots"


async def stamp_execution_on_snapshot(
    db: Any,
    prediction_id: str,
    entry_price: Optional[float],
    exit_price: Optional[float],
    stop_loss: Optional[float],
    direction: Optional[str],
) -> bool:
    """Update the features_snapshot matching `prediction_id` with
    the four execution-economics fields.

    Returns True if a row was updated, False otherwise (no match,
    no prediction_id, or any exception — all silent to avoid
    cascading into the outcome-labeling caller).

    The fields are only set when non-None so a partial-data close
    (e.g. stop_loss not configured) doesn't wipe fields that a
    prior pass populated. Direction is normalised to uppercase to
    match `risk_weighting.compute_r_multiple` expectations.
    """
    if not prediction_id:
        return False

    update: dict[str, Any] = {}
    if entry_price is not None:
        try:
            update["entry_price"] = float(entry_price)
        except (TypeError, ValueError):
            pass
    if exit_price is not None:
        try:
            update["exit_price"] = float(exit_price)
        except (TypeError, ValueError):
            pass
    if stop_loss is not None:
        try:
            update["stop_loss"] = float(stop_loss)
        except (TypeError, ValueError):
            pass
    if direction is not None:
        dir_str = str(direction).strip().upper()
        if dir_str in ("LONG", "SHORT"):
            update["direction"] = dir_str

    if not update:
        # Nothing valid to write — don't even issue the Mongo round-trip.
        return False

    update["execution_enriched_at"] = datetime.now(timezone.utc)
    # Bump schema_version to 4 for rows that carry the execution block.
    # Training pipelines can filter on `schema_version >= 4` to select
    # R-eligible rows without inspecting every field.
    update["schema_version"] = 4

    try:
        result = await db[_COLLECTION].update_one(
            {"prediction_id": prediction_id},
            {"$set": update},
        )
    except Exception as e:
        logger.warning(
            "snapshot_enricher: update failed for prediction_id=%s: %s",
            prediction_id, e,
        )
        return False

    return bool(getattr(result, "matched_count", 0))
