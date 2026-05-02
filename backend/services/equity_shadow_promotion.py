"""
Equity Commander Shadow — promotion status gate.

This is the *gate* that decides when Commander graduates from pure
observation (Phase 1) to pre-Tier-3 size braking (Phase 2) on the
equity path.

Phase 1 (current)
    Shadow fires on every equity cycle + every paper-trade entry.
    Zero impact on size, direction, or timing. Pure evidence.

Phase 2 (unlocks when both thresholds clear)
    * ``equity_scored_rows >= MIN_SCORED_ROWS`` — the shadow has
      been back-patched with tactical P&L by the scorer for at
      least this many rows (we can't compute a win-rate without
      scored outcomes).
    * ``equity_shadow_win_rate >= MIN_WIN_RATE`` — on the scored
      slice, ``shadow_was_right`` is true at least this share of
      the time (where "right" = Commander's opposite action would
      have won vs the Strategist's actual action, net of the
      ``fill_cost_bps_applied`` spread).

Phase 3 (explicit operator flag, not covered here)
    Full veto authority. Requires sustained proof beyond Phase 2
    plus an intentional env-flag flip.

Both thresholds tunable via env so ops can tighten/loosen without
a code change. Defaults match the user's stated policy.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MIN_SCORED_ROWS: int = int(os.environ.get("EQUITY_SHADOW_PROMOTE_MIN_ROWS", "50"))
MIN_WIN_RATE: float = float(os.environ.get("EQUITY_SHADOW_PROMOTE_MIN_RATE", "0.70"))

# Bot_id used by the equity ML path when firing the shadow. Matches
# the constant used in ``ml_paper_trader.maybe_paper_trade`` — kept
# as a named constant here so a rename in one place surfaces as a
# test failure rather than silent drift.
EQUITY_BOT_ID: str = "equity_ml_orchestrator"


async def compute_equity_shadow_promotion_status(db: Any) -> dict[str, Any]:
    """Return the current progress toward Phase 2 eligibility.

    Shape matches what the admin dashboard card needs to render:

        {
            "phase": "phase_1_logging_only" | "phase_2_brake_eligible",
            "rows_scored": int,
            "rows_to_go": int,
            "min_rows_required": int,
            "win_rate": float | None,
            "min_win_rate_required": float,
            "brake_eligible": bool,
            "blocker": str | None,
        }

    Never raises — a Mongo hiccup surfaces as a soft "unknown"
    status with ``brake_eligible: false``, so the dashboard doesn't
    accidentally green-light a brake on an ambiguous read.
    """
    try:
        coll = db.research_shadow_decisions
        base_query = {
            "bot_id": EQUITY_BOT_ID,
            "asset_type": "stock",
            "shadow_engine": "adversarial",
        }

        rows_total = await coll.count_documents(base_query)
        rows_scored = await coll.count_documents({
            **base_query,
            "tactical_score.shadow_was_right": {"$exists": True},
        })
        rows_shadow_was_right = await coll.count_documents({
            **base_query,
            "tactical_score.shadow_was_right": True,
        })

        win_rate: float | None = None
        if rows_scored > 0:
            win_rate = round(rows_shadow_was_right / rows_scored, 3)

        enough_rows = rows_scored >= MIN_SCORED_ROWS
        good_rate = (win_rate is not None) and (win_rate >= MIN_WIN_RATE)
        brake_eligible = bool(enough_rows and good_rate)

        blocker: str | None = None
        if brake_eligible:
            phase = "phase_2_brake_eligible"
        else:
            phase = "phase_1_logging_only"
            if not enough_rows:
                blocker = (
                    f"need {MIN_SCORED_ROWS - rows_scored} more "
                    f"scored rows (have {rows_scored}/{MIN_SCORED_ROWS})"
                )
            elif not good_rate:
                blocker = (
                    f"win_rate {win_rate:.2%} below threshold "
                    f"{MIN_WIN_RATE:.0%}"
                )

        return {
            "phase": phase,
            "rows_total": rows_total,
            "rows_scored": rows_scored,
            "rows_shadow_was_right": rows_shadow_was_right,
            "rows_to_go": max(0, MIN_SCORED_ROWS - rows_scored),
            "min_rows_required": MIN_SCORED_ROWS,
            "win_rate": win_rate,
            "min_win_rate_required": MIN_WIN_RATE,
            "brake_eligible": brake_eligible,
            "blocker": blocker,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[equity-shadow-promotion] status read failed: %s", exc,
        )
        return {
            "phase": "phase_1_logging_only",
            "rows_total": 0,
            "rows_scored": 0,
            "rows_shadow_was_right": 0,
            "rows_to_go": MIN_SCORED_ROWS,
            "min_rows_required": MIN_SCORED_ROWS,
            "win_rate": None,
            "min_win_rate_required": MIN_WIN_RATE,
            "brake_eligible": False,
            "blocker": f"status probe failed: {exc}",
        }
