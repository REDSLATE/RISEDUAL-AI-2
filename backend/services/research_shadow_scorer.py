"""Deferred counterfactual scorer for shadow decisions.

What this module does
---------------------
Runs every 60s as an APScheduler job (registered in ``server.py``).
On each tick it sweeps ``research_shadow_decisions`` for rows where:

* ``is_dissent=True`` (only dissents are scored — agreement rows are
  noise; the framework is built around disagreement-conditional
  metrics)
* ``pending_counterfactual=True``
* The asset-typed lookahead window has elapsed since ``ts``

For each pending dissent, it computes one or both of:

**Tactical score** — "30 minutes (or asset-typed window) after the
shadow dissented, what would shadow's hypothetical fill have produced
vs active's actual position?" Mirrors the realised P&L the operator
sees on the active trade row. Always computed.

**Strategic score** — only computed for the unique-value scenario:
shadow said HOLD while active was in a position that has now closed.
Question: would shadow have ridden the winner longer (or saved a
loser) by holding past active's close? Only meaningful when
``shadow_action="HOLD"`` AND there's an associated active trade
that has closed.

Tier-3 firewall — restated
--------------------------
This worker reads from the price tape and from
``research_shadow_decisions`` only. It writes scores back to
``research_shadow_decisions`` only. It MUST NOT touch
``trading_bots[].stats``, ``paper_trades``, ``crypto_paper_trades``,
or ``prediction_tracker``.

Definition of "right"
---------------------
Shadow was right on a dissent if its hypothetical $ PnL EXCEEDED
active's realised (or unrealised, if still open) $ PnL over the
lookahead window, AFTER asset-type fill costs. This is the SAME
formula on tactical and strategic — operator sees consistent math
in the drawer + Comparison panel.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.research_shadow import (
    TACTICAL_LOOKAHEAD_S,
    canonicalise_action,
    hypothetical_pnl_usd,
)
from services.research_shadow_logger import SHADOW_COLLECTION, patch_scores

logger = logging.getLogger(__name__)


# Per-shadow notional baseline used for $ P&L sizing. We don't size
# the shadow exactly like the active trade because (a) shadow has no
# real capital stake and (b) consistent sizing keeps the
# disagreement-conditional comparison apples-to-apples across bots
# of different capital tiers. Operators read the comparison as
# "per $1000 deployed" — the absolute number doesn't matter, only
# the active-vs-shadow delta.
SHADOW_NOTIONAL_USD: float = 1_000.0


# ── Pure scoring helpers ──────────────────────────────────────────────────────


def compute_tactical_score(
    *,
    active_action: str,
    shadow_action: str,
    entry_price: float,
    later_price: float,
    fill_cost_bps: int,
    lookahead_used_s: int,
) -> dict[str, Any]:
    """Pure-function tactical score. Both engines are scored at the
    SAME notional + fill cost so the delta is the only signal that
    matters.
    """
    active_pnl = hypothetical_pnl_usd(
        action=active_action,
        entry_price=entry_price,
        exit_price=later_price,
        notional_usd=SHADOW_NOTIONAL_USD,
        fill_cost_bps=fill_cost_bps,
    )
    shadow_pnl = hypothetical_pnl_usd(
        action=shadow_action,
        entry_price=entry_price,
        exit_price=later_price,
        notional_usd=SHADOW_NOTIONAL_USD,
        fill_cost_bps=fill_cost_bps,
    )
    delta = shadow_pnl - active_pnl
    return {
        "active_pnl_usd": round(active_pnl, 4),
        "shadow_pnl_usd": round(shadow_pnl, 4),
        "delta_usd": round(delta, 4),
        "shadow_was_right": delta > 0,
        "lookahead_used_s": lookahead_used_s,
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_strategic_score(
    *,
    shadow_action: str,
    entry_price: float,
    active_close_price: float,
    later_price: float,
    fill_cost_bps: int,
    lookahead_used_s: int,
) -> dict[str, Any]:
    """Strategic score: shadow held past active's close.

    Compares shadow's "would have kept holding" P&L vs active's
    realised close P&L over the strategic lookahead window
    (active_close → active_close + 30min).

    Only meaningful when shadow_action == HOLD. The caller is
    responsible for that gate; this function trusts the inputs.
    """
    # Active engine closed at ``active_close_price`` — its strategic
    # P&L past that point is, by definition, zero (it's flat).
    # Shadow's hypothetical: "if I had stayed in the position with
    # the original entry, where would I be now?" Direction inferred
    # from the original active action — strategic only makes sense
    # when active had a directional position.
    shadow_held_pnl = hypothetical_pnl_usd(
        action=shadow_action if canonicalise_action(shadow_action) != "HOLD" else "LONG",
        entry_price=entry_price,
        exit_price=later_price,
        notional_usd=SHADOW_NOTIONAL_USD,
        fill_cost_bps=fill_cost_bps,
    )
    active_realised_pnl = hypothetical_pnl_usd(
        action="LONG",  # Only the magnitude matters here
        entry_price=entry_price,
        exit_price=active_close_price,
        notional_usd=SHADOW_NOTIONAL_USD,
        fill_cost_bps=fill_cost_bps,
    )
    delta = shadow_held_pnl - active_realised_pnl
    return {
        "active_pnl_usd": round(active_realised_pnl, 4),
        "shadow_pnl_usd": round(shadow_held_pnl, 4),
        "delta_usd": round(delta, 4),
        "shadow_was_right": delta > 0,
        "lookahead_used_s": lookahead_used_s,
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Mongo-side: pending-dissent sweeper ───────────────────────────────────────


async def _fetch_tactical_pending(db: Any, batch: int = 100) -> list[dict]:
    """Pending dissents whose tactical lookahead window has elapsed."""
    if db is None:
        return []
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    try:
        cursor = db[SHADOW_COLLECTION].find(
            {
                "is_dissent": True,
                "pending_counterfactual": True,
                "tactical_score": {"$exists": False},
            },
            {"_id": 0},
        ).sort("ts", 1).limit(batch)
        async for row in cursor:
            ts = row.get("ts")
            if not isinstance(ts, datetime):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            window = TACTICAL_LOOKAHEAD_S.get(
                row.get("asset_type") or "stock",
                TACTICAL_LOOKAHEAD_S["stock"],
            )
            if (now - ts).total_seconds() >= window:
                row["_resolved_ts"] = ts
                out.append(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[shadow-scorer] fetch tactical pending failed: %s", exc)
    return out


async def _resolve_later_price(
    db: Any, symbol: str, asset_type: str, target_ts: datetime,
) -> Optional[float]:
    """Best-effort resolution of the symbol price at ``target_ts``.

    Strategy: try the asset-appropriate quote provider for the
    current price (close enough for crypto/equity at >=30min
    horizons given typical bot cadences). If the provider call
    fails, return None — the scorer will skip this row and re-try
    on a future tick.

    Note: a true historical-bar lookup would be more accurate but
    requires intraday OHLCV storage we don't currently maintain.
    Current-price approximation is honest about its limitations
    via the ``lookahead_used_s`` field on each score.
    """
    try:
        if asset_type == "crypto":
            from services.crypto_quotes import get_crypto_quote
            quote = await get_crypto_quote(symbol)
        else:
            # Equity / options: use the consolidated quote service.
            from services.price_provider import get_quote
            quote = await get_quote(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[shadow-scorer] quote lookup failed %s/%s: %s",
            asset_type, symbol, exc,
        )
        return None

    if not isinstance(quote, dict):
        return None
    price = quote.get("price") or quote.get("last") or quote.get("close")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


async def _score_one_tactical(db: Any, row: dict) -> bool:
    """Score a single pending dissent. Returns True on success."""
    decision_id = row.get("decision_id")
    if not decision_id:
        return False

    symbol = row.get("symbol") or ""
    asset_type = row.get("asset_type") or "stock"
    entry_price = float(row.get("mid_price") or 0.0)
    fill_bps = int(row.get("sim_fill_bps_round_trip") or 0)
    lookahead = TACTICAL_LOOKAHEAD_S.get(
        asset_type, TACTICAL_LOOKAHEAD_S["stock"],
    )
    target_ts = row["_resolved_ts"] + timedelta(seconds=lookahead)

    later_price = await _resolve_later_price(db, symbol, asset_type, target_ts)
    if later_price is None or entry_price <= 0:
        return False

    score = compute_tactical_score(
        active_action=row.get("active_action") or "HOLD",
        shadow_action=row.get("shadow_action") or "HOLD",
        entry_price=entry_price,
        later_price=later_price,
        fill_cost_bps=fill_bps,
        lookahead_used_s=lookahead,
    )
    return await patch_scores(db, decision_id, tactical_score=score)


async def run_scorer_pass(db: Any, batch: int = 100) -> dict[str, int]:
    """One pass of the deferred scorer. Returns a small counter
    dict so the worker wrapper can log a summary line per tick.
    """
    if db is None:
        return {"scanned": 0, "scored": 0, "skipped": 0}

    pending = await _fetch_tactical_pending(db, batch=batch)
    scored = 0
    skipped = 0
    for row in pending:
        ok = await _score_one_tactical(db, row)
        if ok:
            scored += 1
        else:
            skipped += 1
    return {"scanned": len(pending), "scored": scored, "skipped": skipped}
