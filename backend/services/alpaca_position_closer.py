"""Alpaca Position Closer — live broker exit engine for brain-opened
positions (2026-02-23).

Background
----------
``services/ml_alpaca_broker.py`` submits BUY orders to Alpaca when
brain signals fire — but until now nothing on the brain side ever
submitted matching close orders. The result was the screenshot the
operator surfaced on 2026-02-23: NVDA 90 sh, AMZN 78 sh, GOOGL 53
sh etc. accumulating for weeks because the brain's only "close"
path was the bookkeeping write in ``paper_trade_closer`` (marks the
``paper_trades`` row ``closed`` and computes PnL from a quote, but
NEVER touches Alpaca).

This service plugs the gap. Schedule: every 5 minutes (matches
``position_reconciler``). For each position Alpaca reports:

1. Resolve entry / peak from the matching ``live_orders`` row.
2. Apply the equity exit cascade
   (``services.tier3_paper_closer._decide_equity_exit``): SL → TP →
   trailing → max-hold. Same rules as Tier-3 paper so paper and
   live grade identically.
3. Options: same envelope on contract price PLUS a hard 5-day-pre-
   expiry close (PDT / assignment hygiene).
4. Fire the close via ``_submit_market_order(side="sell")`` (longs)
   or ``"buy"`` (shorts) / ``"buy_to_close"`` / ``"sell_to_close"``
   for option legs.
5. Mirror the SELL row to ``paper_trades`` (``status="closed"``)
   so the existing closer pipeline / outcome inbox / Sovereign
   learning path picks up the exit just like paper trades.

Doctrine pins
-------------
* **Source of truth** = Alpaca ``GET /v2/positions`` — not the
  ``paper_trades`` ledger (which is known to drift; see the
  2026-02-23 NVDA-23-vs-168 forensic).
* **Idempotent** — skip any position with an in-flight close order
  in ``live_orders`` within the last 10 minutes.
* **Safety rails** — ``ALPACA_POSITION_CLOSER_ENABLED`` must be
  explicitly set ``true`` (default OFF) and dry-run mode
  ``ALPACA_POSITION_CLOSER_DRY_RUN`` logs decisions without firing.
  Operator flips these for the PDT June-4 window after PRD review.
* **Failure-isolated** — one bad symbol never poisons the sweep;
  Alpaca API errors are swallowed and logged.

Env knobs (operator-facing)
---------------------------
================================ =================== =================================
Var                              Default             Effect
================================ =================== =================================
``ALPACA_POSITION_CLOSER_ENABLED``  ``false``        Master switch (default OFF)
``ALPACA_POSITION_CLOSER_DRY_RUN``  ``true``         Log decisions, don't submit
``OPTIONS_PRE_EXPIRY_DAYS``      ``5``               Hard close N days before expiry
``EQUITY_*``                     (see tier3_paper_closer)
``ALPACA_CLOSE_IDEMPOTENCY_S``   ``600``             Skip if SELL submitted in last N s
================================ =================== =================================
"""
from __future__ import annotations

__domain__ = "DTD"

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from services.datetime_utils import ensure_utc
from services.tier3_paper_closer import (
    _decide_equity_exit,
    _resolve_equity_exit_config,
)

logger = logging.getLogger(__name__)


_ALPACA_BASE_URL = os.environ.get(
    "ALPACA_BASE_URL", "https://paper-api.alpaca.markets",
)
_DEFAULT_PRE_EXPIRY_DAYS = 5
_DEFAULT_IDEMPOTENCY_S = 600  # 10 minutes


# ── env helpers ───────────────────────────────────────────────────────


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if v == "":
        return default
    return v in {"1", "true", "yes", "on"}


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _is_enabled() -> bool:
    """Master kill-switch. Default OFF — must be explicitly enabled."""
    return _b("ALPACA_POSITION_CLOSER_ENABLED", False)


def _is_dry_run() -> bool:
    """Dry-run logs decisions without firing real close orders."""
    return _b("ALPACA_POSITION_CLOSER_DRY_RUN", True)


# ── Alpaca position introspection ─────────────────────────────────────


async def _fetch_alpaca_positions(
    client: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    """Return live Alpaca positions (equity + options interleaved as
    Alpaca returns them). Empty list on any error so a transient
    API blip can't crash the closer."""
    from services.ml_alpaca_broker import _alpaca_headers
    try:
        resp = await client.get(
            f"{_ALPACA_BASE_URL}/v2/positions",
            headers=_alpaca_headers(),
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json() or []
        logger.warning(
            "[alpaca-closer] /v2/positions returned %d",
            resp.status_code,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpaca-closer] positions fetch failed: %s", exc)
    return []


# ── lookup helpers ────────────────────────────────────────────────────


def _is_option_symbol(sym: str) -> bool:
    """Crude OCC-style detection: equity symbols are ≤ 5 chars and
    pure letters; OCC option symbols are 15+ chars and contain
    digits + a ``C``/``P`` strike marker."""
    if not sym:
        return False
    if len(sym) <= 5 and sym.isalpha():
        return False
    return any(ch.isdigit() for ch in sym) and ("C" in sym or "P" in sym)


def _parse_expiry_from_occ(occ_symbol: str) -> Optional[datetime]:
    """OCC option symbols embed YYMMDD after the root. e.g.
    ``SPY261218C00510000`` → expiry 2026-12-18. Returns the expiry
    UTC at market close (21:00Z) or None if unparseable."""
    if not occ_symbol or len(occ_symbol) < 15:
        return None
    # Strip the equity root (variable length, alpha only).
    i = 0
    while i < len(occ_symbol) and occ_symbol[i].isalpha():
        i += 1
    yymmdd = occ_symbol[i:i + 6]
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    try:
        year = 2000 + int(yymmdd[0:2])
        month = int(yymmdd[2:4])
        day = int(yymmdd[4:6])
        # 21:00Z ≈ US market close. Sufficient for date arithmetic.
        return datetime(year, month, day, 21, 0, 0, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def _decide_option_exit(
    *,
    entry_price: float,
    current_price: float,
    peak_price: float | None,
    opened_at: datetime,
    expiry: datetime | None,
    cfg: dict[str, float | bool],
    pre_expiry_days: int,
    now: datetime | None = None,
) -> str | None:
    """Options exit cascade. Same SL/TP/trail/hold envelope as equity
    PLUS a hard close N days before expiry (assignment hygiene)."""
    now = now or datetime.now(timezone.utc)
    # 1) Pre-expiry close — highest priority for options.
    if expiry is not None:
        days_left = (expiry - now).total_seconds() / 86400.0
        if days_left <= float(pre_expiry_days):
            return "pre_expiry"
    # 2) Standard equity cascade on the contract price.
    return _decide_equity_exit(
        entry_price=entry_price,
        current_price=current_price,
        peak_price=peak_price,
        opened_at=opened_at,
        cfg=cfg,
        now=now,
    )


async def _resolve_open_anchor(
    db: Any, symbol: str,
) -> dict[str, Any] | None:
    """Find the matching ``live_orders`` row that opened this
    position. Walks both BUY orders (longs) and SELL orders (shorts).
    Most-recent submitted_at within the lookback window wins."""
    since = datetime.now(timezone.utc) - timedelta(days=60)
    row = await db["live_orders"].find_one(
        {"ticker": symbol, "submitted_at": {"$gte": since}},
        {"_id": 0},
        sort=[("submitted_at", -1)],
    )
    return row


async def _close_order_in_flight(
    db: Any, symbol: str, idempotency_s: int,
) -> bool:
    """True if we already submitted a SELL/close order for this
    symbol within the idempotency window — prevents double-firing
    on adjacent ticks before Alpaca's position reflects the close."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=idempotency_s)
    existing = await db["live_orders"].find_one(
        {
            "ticker": symbol,
            "side": {"$in": ["sell", "buy_to_close", "sell_to_close"]},
            "submitted_at": {"$gte": cutoff},
        },
        {"_id": 1},
    )
    return existing is not None


# ── close-order submission ────────────────────────────────────────────


async def _submit_close(
    client: httpx.AsyncClient,
    symbol: str,
    qty: float,
    side: str,
    *,
    dry_run: bool,
) -> str | None:
    """Submit a market close. ``side`` is the Alpaca-side string —
    ``"sell"`` to close a long equity, ``"buy"`` to close a short,
    ``"sell_to_close"`` / ``"buy_to_close"`` for option legs."""
    if dry_run:
        logger.info(
            "[alpaca-closer] DRY-RUN — would submit %s qty=%s %s",
            side, qty, symbol,
        )
        return f"dry-run-{symbol}-{side}"
    from services.ml_alpaca_broker import _alpaca_headers
    payload: dict[str, Any] = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    try:
        resp = await client.post(
            f"{_ALPACA_BASE_URL}/v2/orders",
            headers=_alpaca_headers(),
            json=payload,
            timeout=15.0,
        )
        if resp.status_code in (200, 201):
            return (resp.json() or {}).get("id")
        logger.warning(
            "[alpaca-closer] close rejected for %s: status=%d body=%s",
            symbol, resp.status_code, (resp.text or "")[:200],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpaca-closer] close failed for %s: %s", symbol, exc)
    return None


# ── main sweep ────────────────────────────────────────────────────────


async def close_due_alpaca_positions(db: Any) -> dict[str, Any]:
    """Walk live Alpaca positions, fire close orders for those past
    their exit rule, mirror to ``paper_trades`` for the Sovereign
    learning pipeline.

    Returns operator stats. Always returns cleanly — never raises.
    """
    if not _is_enabled():
        return {
            "enabled": False, "dry_run": _is_dry_run(),
            "evaluated": 0, "closed": 0, "errors": 0,
            "reasons": {},
        }

    if db is None:
        return {"enabled": True, "evaluated": 0, "closed": 0,
                "errors": 0, "reasons": {}}

    cfg = _resolve_equity_exit_config()
    pre_expiry_days = _i("OPTIONS_PRE_EXPIRY_DAYS", _DEFAULT_PRE_EXPIRY_DAYS)
    idempotency_s = _i("ALPACA_CLOSE_IDEMPOTENCY_S", _DEFAULT_IDEMPOTENCY_S)
    dry_run = _is_dry_run()
    now = datetime.now(timezone.utc)

    stats: dict[str, Any] = {
        "enabled": True, "dry_run": dry_run,
        "evaluated": 0, "closed": 0, "errors": 0,
        "skipped_no_anchor": 0, "skipped_idempotent": 0,
        "reasons": {
            "stop_loss": 0, "take_profit": 0, "trailing_stop": 0,
            "hold_window_expired": 0, "pre_expiry": 0,
        },
    }

    async with httpx.AsyncClient() as client:
        positions = await _fetch_alpaca_positions(client)
        if not positions:
            return stats

        for pos in positions:
            try:
                symbol = (pos.get("symbol") or "").strip()
                if not symbol:
                    continue
                stats["evaluated"] += 1

                qty = float(pos.get("qty") or 0)
                current_price = float(pos.get("current_price") or 0)
                if qty == 0 or current_price <= 0:
                    continue

                # Look up the open anchor so we know entry + opened_at
                # + peak. Without it we don't know the rule baseline
                # → skip rather than guess (safer than a wrong close).
                anchor = await _resolve_open_anchor(db, symbol)
                if anchor is None:
                    stats["skipped_no_anchor"] += 1
                    continue

                # entry_price comes from the anchor; Alpaca's
                # ``avg_entry_price`` is a useful fallback when
                # ``live_orders`` doesn't carry one (older rows).
                entry_price = float(
                    anchor.get("entry_price")
                    or pos.get("avg_entry_price") or 0,
                )
                if entry_price <= 0:
                    continue

                opened_at = ensure_utc(anchor.get("submitted_at")) or now
                peak_price = anchor.get("peak_price")

                # Idempotency — don't double-fire on adjacent ticks
                # before Alpaca's position list reflects the close.
                if await _close_order_in_flight(
                    db, symbol, idempotency_s,
                ):
                    stats["skipped_idempotent"] += 1
                    continue

                # Decide exit (option vs equity cascade).
                is_option = _is_option_symbol(symbol)
                if is_option:
                    expiry = _parse_expiry_from_occ(symbol)
                    reason = _decide_option_exit(
                        entry_price=entry_price,
                        current_price=current_price,
                        peak_price=peak_price,
                        opened_at=opened_at,
                        expiry=expiry,
                        cfg=cfg,
                        pre_expiry_days=pre_expiry_days,
                        now=now,
                    )
                else:
                    reason = _decide_equity_exit(
                        entry_price=entry_price,
                        current_price=current_price,
                        peak_price=peak_price,
                        opened_at=opened_at,
                        cfg=cfg,
                        now=now,
                    )

                if reason is None:
                    continue

                # Determine close side.
                if is_option:
                    close_side = "sell_to_close" if qty > 0 else "buy_to_close"
                else:
                    close_side = "sell" if qty > 0 else "buy"

                order_id = await _submit_close(
                    client, symbol, abs(qty), close_side, dry_run=dry_run,
                )
                if not order_id:
                    stats["errors"] += 1
                    continue

                # Record the close in live_orders so the next sweep's
                # idempotency check sees it.
                try:
                    await db["live_orders"].insert_one({
                        "order_id": order_id,
                        "ticker": symbol,
                        "side": close_side,
                        "qty": abs(qty),
                        "entry_price": entry_price,
                        "exit_price": current_price,
                        "submitted_at": now,
                        "close_reason": reason,
                        "source_layer": "alpaca_position_closer",
                        "dry_run": dry_run,
                        "schema_version": 1,
                    })
                except Exception as db_exc:  # noqa: BLE001
                    logger.warning(
                        "[alpaca-closer] live_orders write failed: %s",
                        db_exc,
                    )

                # Mirror to paper_trades so the existing closer
                # pipeline writes a Sovereign outcome (consistent
                # learning signal across paper + live).
                try:
                    await db["paper_trades"].insert_one({
                        "trade_id": f"alpaca-close-{order_id}",
                        "alpaca_order_id": order_id,
                        "ticker": symbol, "symbol": symbol,
                        "direction": "down" if qty > 0 else "up",
                        "side": close_side,
                        "shares": abs(qty), "qty": abs(qty),
                        "entry_price": entry_price,
                        "exit_price": current_price,
                        "position_usd": abs(qty) * current_price,
                        "status": "closed",
                        "opened_at": opened_at,
                        "closed_at": now,
                        "source_layer": "alpaca_position_closer",
                        "receipt_type": "real_fill",
                        "synthetic": False,
                        "eligible_for_learning": True,
                        "close_reason": reason,
                        "auto_closed": True,
                        "auto_close_reason": reason,
                        "dry_run": dry_run,
                    })
                except Exception as mirror_exc:  # noqa: BLE001
                    logger.warning(
                        "[alpaca-closer] paper_trades mirror failed: %s",
                        mirror_exc,
                    )

                stats["closed"] += 1
                stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
                logger.info(
                    "[alpaca-closer] %s closed %s qty=%s reason=%s "
                    "entry=%.4f exit=%.4f%s",
                    "DRY-RUN" if dry_run else "LIVE",
                    symbol, abs(qty), reason,
                    entry_price, current_price,
                    " (in-flight)" if not dry_run else "",
                )
            except Exception as per_pos_exc:  # noqa: BLE001
                stats["errors"] += 1
                logger.warning(
                    "[alpaca-closer] per-position error %s: %s",
                    pos.get("symbol"), per_pos_exc,
                )

    return stats


__all__ = [
    "_decide_option_exit",
    "_is_option_symbol",
    "_parse_expiry_from_occ",
    "close_due_alpaca_positions",
]
