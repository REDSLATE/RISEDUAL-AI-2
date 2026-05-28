"""Kill-Switch Profile — runtime state tracker + live gate
(2026-02-26, P2 follow-up).

Wires the named discipline profiles defined in
``services.kill_switch_profiles`` into a runnable gate that the
paper-trade emission path can call before firing a trade.

Doctrine
--------
* **No-op when no profile is configured.** A clean install behaves
  exactly as before. The gate only does work once an operator has
  explicitly enabled a profile via the admin route.
* **Per-asset_type configuration.** Equity and crypto cores are
  governed independently so the operator can run Warrior discipline
  on the small-account equity slice while leaving the global crypto
  brain ungated.
* **Session boundary = midnight UTC.** Daily P&L and consecutive-loss
  counts reset at the day rollover. Same boundary the rest of the
  brain uses (``datetime.now(timezone.utc)``); no calendar-aware
  ET/UTC translation here — the operator's toolkit talks calendar
  days in their local zone but for an automated brain UTC midnight
  is the cleaner cut.
* **Fail-soft.** Any Mongo hiccup, missing config, or schema mismatch
  returns "no halt" — the gate must NEVER hard-stop the brain due
  to its own internal failure. The discipline is an overlay, not a
  load-bearing safety surface.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

from services.kill_switch_profiles import (
    ProfileEvaluation,
    evaluate_profile,
    get_profile,
)

logger = logging.getLogger(__name__)


# Singleton config collection — same pattern as
# ``confidence_gate_overrides``.
_CONFIG_COLL = "kill_switch_profile_global"


# ── Config read / write ──────────────────────────────────────────


async def get_active_profile_config(
    db: Any, asset_type: str,
) -> Optional[dict[str, Any]]:
    """Read the active profile override for ``asset_type``.

    Returns the document (without ``_id``) or ``None`` when no
    override is set. Never raises — config-read failure degrades
    to no-op.
    """
    if db is None:
        return None
    try:
        doc = await db[_CONFIG_COLL].find_one(
            {"_id": f"{asset_type}:current"},
            {"_id": 0, "asset_type": 1, "profile_key": 1,
             "starting_equity_usd": 1, "enabled": 1,
             "set_by": 1, "set_at": 1, "note": 1},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ks_runtime] config read failed asset=%s: %s",
            asset_type, exc,
        )
        return None
    if not doc:
        return None
    if not doc.get("enabled", True):
        return None
    return doc


async def set_active_profile_config(
    db: Any,
    *,
    asset_type: str,
    profile_key: str,
    starting_equity_usd: float,
    set_by: str,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert the per-asset_type profile override. Caller must have
    already validated the profile exists."""
    now = datetime.now(timezone.utc)
    doc = {
        "_id": f"{asset_type}:current",
        "asset_type": asset_type,
        "profile_key": profile_key,
        "starting_equity_usd": float(starting_equity_usd),
        "enabled": True,
        "set_by": set_by,
        "set_at": now,
        "note": (note or "").strip()[:500] or None,
    }
    await db[_CONFIG_COLL].update_one(
        {"_id": doc["_id"]}, {"$set": doc}, upsert=True,
    )
    logger.info(
        "[ks_runtime] profile activated asset=%s profile=%s equity=$%.2f by=%s",
        asset_type, profile_key, starting_equity_usd, set_by,
    )
    return {k: v for k, v in doc.items() if k != "_id"}


async def clear_active_profile_config(
    db: Any, asset_type: str,
) -> bool:
    """Remove the active override. Returns True iff a row was deleted."""
    res = await db[_CONFIG_COLL].delete_one(
        {"_id": f"{asset_type}:current"},
    )
    return bool(res.deleted_count)


# ── Session state computation ────────────────────────────────────


def _session_start_utc(now: datetime) -> datetime:
    """UTC midnight of the supplied moment."""
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


_TRADE_COLL_FOR_ASSET = {
    "equity": "paper_trades",
    "crypto": "crypto_paper_trades",
}


async def compute_session_stats(
    db: Any,
    *,
    asset_type: str,
    starting_equity_usd: float,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Tally today's realised P&L and consecutive-loss streak.

    Reads from the ML-paper-trader trade collection (``paper_trades``
    for equity, ``crypto_paper_trades`` for crypto). Manual user
    paper trades use the same collection but a different schema; we
    filter on ``status=="closed"`` AND require a numeric ``pnl_usd``
    field, which is only present on ML-trader receipts. Manual rows
    are ignored cleanly.
    """
    now = now or datetime.now(timezone.utc)
    session_start = _session_start_utc(now)
    coll_name = _TRADE_COLL_FOR_ASSET.get(asset_type, "paper_trades")

    realized_pnl = 0.0
    consecutive_losses = 0
    closes_today = 0

    try:
        coll = db[coll_name]
        # Sum today's realized P&L (closed ML trades)
        pipe = [
            {"$match": {
                "status": "closed",
                "closed_at": {"$gte": session_start},
                "pnl_usd": {"$exists": True, "$ne": None},
            }},
            {"$group": {"_id": None, "total": {"$sum": "$pnl_usd"},
                        "n": {"$sum": 1}}},
        ]
        agg = await coll.aggregate(pipe).to_list(length=1)
        if agg:
            realized_pnl = float(agg[0].get("total") or 0.0)
            closes_today = int(agg[0].get("n") or 0)

        # Consecutive-loss streak: most recent closes first, count
        # contiguous outcome=='loss'. Stops on the first non-loss.
        cur = coll.find(
            {
                "status": "closed",
                "closed_at": {"$gte": session_start},
                "outcome": {"$in": ["win", "loss"]},
            },
            {"_id": 0, "outcome": 1, "closed_at": 1},
        ).sort("closed_at", -1).limit(50)
        async for row in cur:
            if (row.get("outcome") or "").lower() == "loss":
                consecutive_losses += 1
            else:
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ks_runtime] session stats failed asset=%s: %s",
            asset_type, exc,
        )

    return {
        "asset_type": asset_type,
        "session_start_utc": session_start.isoformat(),
        "starting_equity_usd": float(starting_equity_usd),
        "realized_pnl_usd_today": round(realized_pnl, 2),
        "consecutive_losses_today": consecutive_losses,
        "closes_today": closes_today,
    }


# ── Live gate ────────────────────────────────────────────────────


async def check_session_halt(
    db: Any, asset_type: str,
) -> Optional[ProfileEvaluation]:
    """Return the active profile's evaluation if any halt rule has
    fired this session; ``None`` when no profile is active or no
    halt condition is met.

    Doctrine: never raises. Any failure short-circuits to "no halt"
    so the discipline overlay can't ground the brain on its own bug.
    """
    config = await get_active_profile_config(db, asset_type)
    if not config:
        return None
    profile = get_profile(config.get("profile_key") or "")
    if profile is None:
        return None

    starting_equity = float(config.get("starting_equity_usd") or 0.0)
    if starting_equity <= 0:
        return None

    try:
        stats = await compute_session_stats(
            db,
            asset_type=asset_type,
            starting_equity_usd=starting_equity,
        )
        ev = evaluate_profile(
            profile.key,
            starting_equity_usd=starting_equity,
            realized_pnl_usd_today=stats["realized_pnl_usd_today"],
            consecutive_losses_today=stats["consecutive_losses_today"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ks_runtime] evaluate failed asset=%s: %s", asset_type, exc,
        )
        return None

    if not ev.halt:
        return None
    return ev


__all__ = [
    "get_active_profile_config",
    "set_active_profile_config",
    "clear_active_profile_config",
    "compute_session_stats",
    "check_session_halt",
]
