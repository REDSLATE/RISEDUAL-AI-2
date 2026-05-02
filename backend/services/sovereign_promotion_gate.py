"""
Sovereign AI Promotion Gate — per-core unlock controller.

Mirrors ``equity_shadow_promotion`` but tracks resolved sovereign decisions
instead of adversarial shadow rows. Provides:

* ``compute_sovereign_promotion_status(db, asset_type)`` — the current gate
  state for a single core (equity / crypto).
* ``is_sovereign_authority(db, asset_type)`` — single-bool helper for the
  paper-traders to check "should I hand control to Sovereign this tick?".
* Demotion watcher: once in authority, a rolling 30-day win-rate < 55%
  auto-reverts to shadow until the gate re-earns it.

Thresholds are env-tunable; defaults match the approved policy:
    min samples    >= 500
    win rate       >= 70%
    calibration    >= 65%
    demote below   <  55%  over rolling 30d
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

AssetType = Literal["equity", "crypto"]

MIN_RESOLVED: int = int(os.environ.get("SOVEREIGN_PROMOTE_MIN_ROWS", "500"))
MIN_WIN_RATE: float = float(os.environ.get("SOVEREIGN_PROMOTE_MIN_RATE", "0.70"))
MIN_CALIBRATION: float = float(os.environ.get("SOVEREIGN_PROMOTE_MIN_CAL", "0.65"))
DEMOTE_BELOW: float = float(os.environ.get("SOVEREIGN_DEMOTE_BELOW", "0.55"))
DEMOTE_WINDOW_DAYS: int = int(os.environ.get("SOVEREIGN_DEMOTE_WINDOW_DAYS", "30"))
# Horizon used for gate math — 60m by default (fastest outcome to land).
GATE_HORIZON: str = os.environ.get("SOVEREIGN_GATE_HORIZON", "60m")


async def compute_sovereign_promotion_status(
    db: Any,
    asset_type: AssetType,
) -> dict[str, Any]:
    """Current Sovereign AI gate state for a single core.

    Never raises — Mongo hiccups return a ``phase_1`` shadow-only snapshot so
    the paper-traders default to the Commander-led path.
    """
    try:
        coll = db["sovereign_decisions"]
        base = {"asset_type": asset_type, "shadow": True}

        rows_total = await coll.count_documents({"asset_type": asset_type})
        rows_resolved = await coll.count_documents(
            {**base, "resolved": True, f"outcomes.{GATE_HORIZON}": {"$exists": True}},
        )
        rows_right = await coll.count_documents(
            {**base, f"outcomes.{GATE_HORIZON}.was_right": True},
        )

        # Calibration score average (across resolved rows only)
        cal_avg: float | None = None
        if rows_resolved > 0:
            pipe = [
                {
                    "$match": {
                        **base,
                        f"outcomes.{GATE_HORIZON}": {"$exists": True},
                    },
                },
                {"$group": {"_id": None, "avg": {"$avg": "$calibration_score"}}},
            ]
            agg = await coll.aggregate(pipe).to_list(length=1)
            if agg:
                cal_avg = round(float(agg[0]["avg"]), 4)

        win_rate: float | None = None
        if rows_resolved > 0:
            win_rate = round(rows_right / rows_resolved, 4)

        # Rolling demotion check — only relevant when already promoted
        cutoff = datetime.now(timezone.utc) - timedelta(days=DEMOTE_WINDOW_DAYS)
        rolling_resolved = await coll.count_documents(
            {
                **base,
                f"outcomes.{GATE_HORIZON}": {"$exists": True},
                "created_at": {"$gte": cutoff},
            },
        )
        rolling_right = await coll.count_documents(
            {
                **base,
                f"outcomes.{GATE_HORIZON}.was_right": True,
                "created_at": {"$gte": cutoff},
            },
        )
        rolling_rate: float | None = None
        if rolling_resolved > 0:
            rolling_rate = round(rolling_right / rolling_resolved, 4)

        gate_rows_ok = rows_resolved >= MIN_RESOLVED
        gate_rate_ok = (win_rate is not None) and (win_rate >= MIN_WIN_RATE)
        gate_cal_ok = (cal_avg is not None) and (cal_avg >= MIN_CALIBRATION)

        # Hard demotion: rolling 30d < DEMOTE_BELOW after being promoted
        demoted = (
            gate_rows_ok
            and rolling_rate is not None
            and rolling_rate < DEMOTE_BELOW
        )

        promoted = gate_rows_ok and gate_rate_ok and gate_cal_ok and not demoted

        blocker: str | None = None
        if not gate_rows_ok:
            blocker = (
                f"need {MIN_RESOLVED - rows_resolved} more resolved rows "
                f"(have {rows_resolved}/{MIN_RESOLVED})"
            )
        elif not gate_rate_ok:
            blocker = (
                f"win_rate {win_rate:.2%} below {MIN_WIN_RATE:.0%} threshold"
            )
        elif not gate_cal_ok:
            blocker = (
                f"calibration {cal_avg:.2%} below {MIN_CALIBRATION:.0%} threshold"
            )
        elif demoted:
            blocker = (
                f"DEMOTED: rolling {DEMOTE_WINDOW_DAYS}d win rate "
                f"{rolling_rate:.2%} fell below {DEMOTE_BELOW:.0%} floor"
            )

        phase = "phase_2_authority" if promoted else "phase_1_shadow_only"

        return {
            "asset_type": asset_type,
            "phase": phase,
            "promoted": promoted,
            "demoted": demoted,
            "rows_total": rows_total,
            "rows_resolved": rows_resolved,
            "rows_right": rows_right,
            "rows_to_go": max(0, MIN_RESOLVED - rows_resolved),
            "min_rows_required": MIN_RESOLVED,
            "win_rate": win_rate,
            "min_win_rate_required": MIN_WIN_RATE,
            "calibration_avg": cal_avg,
            "min_calibration_required": MIN_CALIBRATION,
            "rolling_win_rate": rolling_rate,
            "rolling_window_days": DEMOTE_WINDOW_DAYS,
            "demote_below": DEMOTE_BELOW,
            "gate_horizon": GATE_HORIZON,
            "blocker": blocker,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sovereign-promotion] status read failed for %s: %s",
            asset_type, exc,
        )
        return {
            "asset_type": asset_type,
            "phase": "phase_1_shadow_only",
            "promoted": False,
            "demoted": False,
            "rows_total": 0,
            "rows_resolved": 0,
            "rows_right": 0,
            "rows_to_go": MIN_RESOLVED,
            "min_rows_required": MIN_RESOLVED,
            "win_rate": None,
            "min_win_rate_required": MIN_WIN_RATE,
            "calibration_avg": None,
            "min_calibration_required": MIN_CALIBRATION,
            "rolling_win_rate": None,
            "rolling_window_days": DEMOTE_WINDOW_DAYS,
            "demote_below": DEMOTE_BELOW,
            "gate_horizon": GATE_HORIZON,
            "blocker": f"status probe failed: {exc}",
        }


async def is_sovereign_authority(db: Any, asset_type: AssetType) -> bool:
    """Single-bool helper for traders. Fail-safe default is False."""
    try:
        st = await compute_sovereign_promotion_status(db, asset_type)
        return bool(st.get("promoted", False))
    except Exception:  # noqa: BLE001
        return False


# ─── Bounded-contribution API ──────────────────────────────────────────────────


# Maximum confidence bump/reduction Sovereign is allowed to apply. The hard
# cap is intentionally small — Sovereign can nudge production, never shove it.
MAX_SOVEREIGN_CONFIDENCE_DELTA: float = float(
    os.environ.get("SOVEREIGN_MAX_CONF_DELTA", "0.08")
)


def apply_sovereign_contribution(
    *,
    production_confidence: float,
    production_action: str,
    sovereign_decision: dict[str, Any] | None,
    promotion_state: dict[str, Any] | None,
) -> tuple[float, dict[str, Any]]:
    """Bounded contribution from Sovereign AI to the production decision.

    Invariants (pinned by tests):
    * Returns the production confidence unchanged when Sovereign is NOT
      promoted, when there is no sovereign decision to reference, or when
      the production action is ``HOLD`` (a HOLD must never be converted to
      an entry by Sovereign).
    * When promoted AND sovereign's action matches production's action
      (both LONG or both SHORT or both equivalently directional), the
      contribution is a confidence BUMP capped at ``MAX_SOVEREIGN_CONFIDENCE_DELTA``.
    * When promoted AND sovereign's action CONTRADICTS production's
      action, the contribution is a confidence REDUCTION capped at the
      same delta. Sovereign CANNOT flip the action — only soften
      conviction on contradiction.
    * Returned confidence is clamped to ``[0.0, 1.0]``.

    Never raises — on any malformed input, returns production confidence
    unchanged with ``reason="no_contribution"``.
    """
    try:
        base = max(0.0, min(1.0, float(production_confidence)))
        prod_action = (production_action or "").upper()

        meta: dict[str, Any] = {
            "applied": False,
            "reason": "no_contribution",
            "delta": 0.0,
            "sovereign_action": None,
            "sovereign_confidence": None,
            "promoted": False,
        }

        promoted = bool((promotion_state or {}).get("promoted", False))
        meta["promoted"] = promoted

        if not promoted:
            meta["reason"] = "not_promoted"
            return base, meta

        if not sovereign_decision:
            meta["reason"] = "no_sovereign_decision"
            return base, meta

        # HOLD lane: Sovereign cannot turn a HOLD into a trade
        if prod_action == "HOLD":
            meta["reason"] = "production_hold_immutable"
            return base, meta

        sov_action = (sovereign_decision.get("action") or "").upper()
        sov_conf = float(sovereign_decision.get("confidence") or 0.0)
        meta["sovereign_action"] = sov_action
        meta["sovereign_confidence"] = sov_conf

        # Normalize directional synonyms
        def _dir(a: str) -> str:
            if a in {"LONG", "BUY", "STRONG_BUY", "UP", "BULLISH"}:
                return "LONG"
            if a in {"SHORT", "SELL", "STRONG_SELL", "DOWN", "BEARISH"}:
                return "SHORT"
            return a

        prod_dir = _dir(prod_action)
        sov_dir = _dir(sov_action)

        cap = MAX_SOVEREIGN_CONFIDENCE_DELTA

        if sov_dir == "HOLD":
            # Sovereign wants to hold but production is going — soften conviction
            # proportional to sovereign confidence in the hold.
            delta = -min(cap, sov_conf * cap)
            meta.update(applied=True, reason="sovereign_hold_softens", delta=delta)
            return max(0.0, min(1.0, base + delta)), meta

        if sov_dir == prod_dir:
            # Aligned — bump confidence
            delta = +min(cap, sov_conf * cap)
            meta.update(applied=True, reason="aligned_bump", delta=delta)
            return max(0.0, min(1.0, base + delta)), meta

        if sov_dir and sov_dir != prod_dir:
            # Contradicting direction — reduce confidence, NEVER flip action
            delta = -min(cap, sov_conf * cap)
            meta.update(applied=True, reason="contra_reduces", delta=delta)
            return max(0.0, min(1.0, base + delta)), meta

        meta["reason"] = "unknown_sovereign_action"
        return base, meta

    except Exception as exc:  # noqa: BLE001
        logger.debug("[sovereign_contribution] safe no-op: %s", exc)
        return float(production_confidence or 0.0), {
            "applied": False,
            "reason": "contribution_error",
            "error": str(exc),
        }
