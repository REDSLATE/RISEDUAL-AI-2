"""
Ticker abandonment history — daily snapshots of the gate's decision
per (lane, symbol) so the admin overview can show Δ-since-yesterday.

Read-only by construction
─────────────────────────
* Writes to ``ticker_abandonment_history`` ONLY.
* Never touches the live gate. Never mutates ``paper_trades``,
  ``crypto_paper_trades``, ``predictions``, or
  ``agent_activity``. Snapshots are derived from the same
  read-side helpers the bulk endpoint uses.

Idempotency
───────────
Snapshots are keyed by ``(date, lane, symbol)`` (UTC date). Re-running
on the same day upserts the same row — last-write-wins on the
counters but the action / reason / cooldown should be deterministic
within a day given the same Mongo state.

Δ-detection
───────────
Pure ``compute_action_delta`` function maps two action strings to one
of ``new`` / ``improved`` / ``unchanged`` / ``degraded`` plus an
arrow glyph. Direction follows the operator's spec:

  ABANDON → COOLDOWN → KEEP   (improved, rank goes up)
  KEEP → COOLDOWN → ABANDON   (degraded, rank goes down)

Anything else (no prior snapshot) is ``new``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = "ticker_abandonment_history"

# Action rank: higher rank = healthier. The operator's spec literally
# pins this ordering (ABANDON = "ticker is toxic", KEEP = "ticker is
# eligible"). Anything missing (e.g. an unexpected action string)
# falls to rank=-1 so deltas surface conservatively as "unchanged".
ACTION_RANK: dict[str, int] = {
    "ABANDON": 0,
    "COOLDOWN": 1,
    "KEEP": 2,
}


def _utc_date_str(now: datetime | None = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.strftime("%Y-%m-%d")


def compute_action_delta(today: str, yesterday: str | None) -> dict[str, Any]:
    """Pure mapping from (today, yesterday) action pair to a delta
    record the admin column renders.

    ``arrow`` is a single char so the table column stays narrow.
    ``new`` means we have no prior snapshot — first time the gate
    saw this ticker. The component renders that as a neutral dot
    so it doesn't visually look like a flip.
    """
    if yesterday is None:
        return {"delta": "new", "arrow": "•", "prior_action": None}

    today_rank = ACTION_RANK.get(today, -1)
    prior_rank = ACTION_RANK.get(yesterday, -1)

    if today_rank > prior_rank:
        return {"delta": "improved", "arrow": "↑", "prior_action": yesterday}
    if today_rank < prior_rank:
        return {"delta": "degraded", "arrow": "↓", "prior_action": yesterday}
    return {"delta": "unchanged", "arrow": "→", "prior_action": yesterday}


async def write_snapshot(
    db: Any,
    *,
    lane: str,
    symbol: str,
    action: str,
    reason: str,
    cooldown_minutes: int,
    inputs_view: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Upsert one row keyed by ``(date, lane, symbol)``.

    Never raises into the caller — a snapshot write failure must
    not poison the live admin endpoint that triggered it.
    """
    if db is None:
        return
    n = now or datetime.now(timezone.utc)
    date = _utc_date_str(n)
    sym = symbol.upper()
    doc = {
        "date": date,
        "lane": lane,
        "symbol": sym,
        "action": action,
        "reason": reason,
        "cooldown_minutes": int(cooldown_minutes or 0),
        "inputs": dict(inputs_view),
        "snapshotted_at": n,
    }
    try:
        await db[COLLECTION].update_one(
            {"date": date, "lane": lane, "symbol": sym},
            {"$set": doc},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ticker-abandon-history] snapshot write skipped for %s/%s: %s",
            lane, sym, exc,
        )


async def fetch_prior_action(
    db: Any, *, lane: str, symbol: str, now: datetime | None = None,
) -> str | None:
    """Return the most recent prior snapshot's action for this
    (lane, symbol), strictly excluding today.

    "Yesterday" is loose by design — if the snapshot job missed a
    day (e.g. backend was down), we still want to compare today's
    decision against the last one we have.
    """
    if db is None:
        return None
    n = now or datetime.now(timezone.utc)
    today = _utc_date_str(n)
    try:
        doc = await db[COLLECTION].find_one(
            {"lane": lane, "symbol": symbol.upper(), "date": {"$lt": today}},
            {"_id": 0, "action": 1},
            sort=[("date", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ticker-abandon-history] prior read skipped for %s/%s: %s",
            lane, symbol, exc,
        )
        return None
    if not doc:
        return None
    return doc.get("action")


async def snapshot_today(db: Any, now: datetime | None = None) -> dict[str, int]:
    """Walk every (lane, symbol) the bulk overview discovers, compute
    the gate decision, write the snapshot. Idempotent on the same day.

    Returns counts by action so the daily script + on-demand endpoint
    can both render a one-liner status. Never raises.
    """
    if db is None:
        return {"abandon": 0, "cooldown": 0, "keep": 0, "errors": 0}

    # Deferred import — keeps this module light and breaks any
    # potential cycle with the gate's input helpers.
    from services.ticker_abandonment import decide_ticker_exit
    from services.ticker_abandonment_stats import (
        RECENT_WINDOW_DAYS, compute_crypto_inputs, compute_equity_inputs,
    )

    n = now or datetime.now(timezone.utc)
    since = n - timedelta(days=RECENT_WINDOW_DAYS)

    equity_symbols: set[str] = set()
    crypto_symbols: set[str] = set()
    try:
        for sym in await db["paper_trades"].distinct(
            "ticker", {"opened_at": {"$gte": since}},
        ):
            if sym:
                equity_symbols.add(str(sym).upper())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[abandon-snapshot] equity discovery failed: %s", exc)
    try:
        for sym in await db["crypto_paper_trades"].distinct(
            "symbol", {"opened_at": {"$gte": since}},
        ):
            if sym:
                crypto_symbols.add(str(sym).upper())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[abandon-snapshot] crypto discovery failed: %s", exc)
    try:
        for sym in await db["agent_activity"].distinct(
            "symbol",
            {
                "type": {"$in": ["paper_trade_open", "paper_trade_skip"]},
                "created_at": {"$gte": since},
            },
        ):
            if not sym:
                continue
            s = str(sym).upper()
            if s in {
                "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE",
                "AVAX", "LINK", "DOT", "MATIC", "BNB",
            }:
                crypto_symbols.add(s)
            else:
                equity_symbols.add(s)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[abandon-snapshot] activity discovery failed: %s", exc)

    counts = {"abandon": 0, "cooldown": 0, "keep": 0, "errors": 0}

    async def _snap_one(lane: str, sym: str, inputs) -> None:
        try:
            decision = decide_ticker_exit(
                symbol=sym,
                recent_signals=inputs.recent_signals,
                recent_rejections=inputs.recent_rejections,
                recent_losses=inputs.recent_losses,
                recent_wins=inputs.recent_wins,
                avg_confidence=inputs.avg_confidence,
                avg_rr=inputs.avg_rr,
                last_profitable_at=inputs.last_profitable_at,
            )
            inputs_view = {
                "recent_signals": inputs.recent_signals,
                "recent_rejections": inputs.recent_rejections,
                "recent_wins": inputs.recent_wins,
                "recent_losses": inputs.recent_losses,
                "avg_confidence": round(inputs.avg_confidence, 4),
                "avg_rr": round(inputs.avg_rr, 4),
                "last_profitable_at": (
                    inputs.last_profitable_at.isoformat()
                    if inputs.last_profitable_at else None
                ),
            }
            await write_snapshot(
                db,
                lane=lane,
                symbol=sym,
                action=decision.action,
                reason=decision.reason,
                cooldown_minutes=decision.cooldown_minutes,
                inputs_view=inputs_view,
                now=n,
            )
            key = decision.action.lower()
            if key in counts:
                counts[key] += 1
        except Exception as exc:  # noqa: BLE001
            counts["errors"] += 1
            logger.debug(
                "[abandon-snapshot] decision failed for %s/%s: %s",
                lane, sym, exc,
            )

    for sym in sorted(equity_symbols):
        inputs = await compute_equity_inputs(db, sym)
        await _snap_one("equity", sym, inputs)
    for sym in sorted(crypto_symbols):
        inputs = await compute_crypto_inputs(db, sym)
        await _snap_one("crypto", sym, inputs)

    return counts
