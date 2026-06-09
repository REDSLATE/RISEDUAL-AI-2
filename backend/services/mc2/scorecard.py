"""MC2 — local scorecard rollup.

Reads ``mc2_outcomes`` and aggregates win/loss/flat counts per
brain. This is the function that finally unblocks the
``total_resolved=0`` pain that Original MC was failing to compute
because crypto outcomes never made it across the wire.

Phase A keeps the shape minimal — Phase B will layer in time
windows (1d / 7d / 30d) and per-lane breakdowns. For now: one
all-time roll-up per brain.
"""
from __future__ import annotations

import logging
from typing import Any

from services.mc2.state import get_db

logger = logging.getLogger(__name__)


async def get_scorecard(brain: str = "alpha") -> dict[str, Any]:
    """Return win/loss/flat counts + win-rate for the given brain.

    Shape::

        {
          "brain": "alpha",
          "total_resolved": int,
          "win": int,
          "loss": int,
          "flat": int,
          "win_rate": float,   # wins / (wins + losses), or 0.0 if no resolved trades
        }

    Returns the zeroed-out shape when MC2 has seen zero outcomes
    for this brain (or when the DB handle is unset). Never raises.
    """
    base = {
        "brain": brain,
        "total_resolved": 0,
        "win": 0,
        "loss": 0,
        "flat": 0,
        "win_rate": 0.0,
    }
    db = get_db()
    if db is None:
        return base

    try:
        pipeline = [
            {"$match": {"brain": brain}},
            {"$group": {
                "_id": "$outcome_label",
                "count": {"$sum": 1},
            }},
        ]
        cursor = db.mc2_outcomes.aggregate(pipeline)
        async for row in cursor:
            label = (row.get("_id") or "flat").lower()
            count = int(row.get("count") or 0)
            if label in ("win", "loss", "flat"):
                base[label] = count
    except Exception as exc:  # noqa: BLE001
        logger.warning("[mc2] scorecard rollup failed for brain=%s: %s",
                       brain, exc)
        return base

    base["total_resolved"] = base["win"] + base["loss"] + base["flat"]
    denom = base["win"] + base["loss"]
    base["win_rate"] = round(base["win"] / denom, 4) if denom > 0 else 0.0
    return base


__all__ = ["get_scorecard"]
