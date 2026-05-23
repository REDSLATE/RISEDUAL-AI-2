"""Backfill Outcome Pairer (2026-05-22, Gap 1).

Pairs filled BUY/SELL Alpaca fills into round-trip outcomes so
``reconcile_alpaca_orders --enqueue-outcomes`` can hand resolved
trades to the Sovereign outcome inbox. Without round-tripping
we'd have no PnL → no outcome label → MC would still see empty
contributions.

Doctrine
--------
* FIFO per symbol. Walk the mirrored ``paper_trades`` rows in
  ``opened_at`` order: each BUY opens a lot, each SELL closes the
  oldest open lot of that symbol.
* PnL is per-share: ``(exit - entry) * qty`` clamped to whichever
  side of the lot the SELL covers. Labels follow the existing
  reconciler convention (``win`` > +0.5%, ``loss`` < -0.5%,
  ``flat`` otherwise).
* Provenance flows from the BUY lot (where the signal originally
  fired) — the SELL is the resolution event, not a new signal.
* Unmatched SELLs (no preceding BUY in the window) and unmatched
  BUYs (still open at the window's end) are skipped — the inbox
  is for resolved trades only.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable


# Mirror of the reconciler's ``_outcome_label`` thresholds so the
# pairer stays standalone-testable.
_WIN_THRESHOLD = 0.005
_LOSS_THRESHOLD = -0.005


def _outcome_label(pnl_pct: float) -> str:
    if pnl_pct > _WIN_THRESHOLD:
        return "win"
    if pnl_pct < _LOSS_THRESHOLD:
        return "loss"
    return "flat"


def pair_fills_into_outcomes(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """FIFO-pair an iterable of mirrored Alpaca rows into resolved
    outcomes ready for ``enqueue_outcome``.

    ``rows`` must be the paper_trades-shaped docs the reconciler
    just inserted (or would insert in dry-run). Order doesn't
    matter — the pairer sorts by ``opened_at`` internally.

    Returns a list of outcome dicts. Each carries the BUY lot's
    provenance (sovereign_decision_id / prediction_id /
    source_signal / confidence) so MC can join the outcome back
    to the originating signal.
    """
    # Sort by opened_at; rows without a timestamp sink to the end.
    sortable = list(rows)
    sortable.sort(key=lambda r: r.get("opened_at") or 0)

    # Per-symbol FIFO queue of open BUY lots.
    open_lots: dict[str, deque[dict[str, Any]]] = {}
    outcomes: list[dict[str, Any]] = []

    for r in sortable:
        side = (r.get("side") or "").lower()
        sym = (r.get("symbol") or r.get("ticker") or "").upper()
        qty = float(r.get("qty") or r.get("shares") or 0)
        price = float(r.get("entry_price") or 0)
        if not sym or qty <= 0 or price <= 0:
            continue

        if side == "buy":
            open_lots.setdefault(sym, deque()).append({
                "qty_remaining": qty,
                "entry_price": price,
                "opened_at": r.get("opened_at"),
                "buy_row": r,
            })
            continue

        if side != "sell":
            continue

        # SELL: drain oldest BUY lot(s) FIFO.
        lots = open_lots.get(sym)
        if not lots:
            # Unmatched sell — skip (likely a pre-window leg).
            continue
        qty_to_close = qty
        while qty_to_close > 0 and lots:
            lot = lots[0]
            covered = min(lot["qty_remaining"], qty_to_close)
            entry = lot["entry_price"]
            pnl_per_share = price - entry
            pnl_pct = (pnl_per_share / entry) if entry else 0.0
            label = _outcome_label(pnl_pct)
            buy_row = lot["buy_row"]
            outcomes.append({
                "trade_id": f"{buy_row.get('trade_id')}::pair::{r.get('trade_id')}",
                "symbol": sym,
                "direction": "long",  # paired round-trip is a long round
                "confidence": float(buy_row.get("confidence") or 0.0),
                "outcome_label": label,
                "notional": covered * entry,
                "pnl_pct": pnl_pct,
                "sovereign_decision_id": buy_row.get("sovereign_decision_id"),
                "prediction_id": buy_row.get("prediction_id"),
                "source_signal": buy_row.get("source_signal"),
                "buy_trade_id": buy_row.get("trade_id"),
                "sell_trade_id": r.get("trade_id"),
                "opened_at": lot["opened_at"],
                "closed_at": r.get("opened_at"),
            })
            lot["qty_remaining"] -= covered
            qty_to_close -= covered
            if lot["qty_remaining"] <= 1e-9:
                lots.popleft()
        # ``qty_to_close > 0`` here means the SELL exceeded our
        # open lots (likely a leg from before the backfill window).
        # We silently drop the residue rather than fabricate a
        # short outcome.

    return outcomes


__all__ = [
    "_outcome_label",
    "pair_fills_into_outcomes",
]
