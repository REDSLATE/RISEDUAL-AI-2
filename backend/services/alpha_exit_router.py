"""Alpha Exit Router — position-aware intent classifier (P1-B).

Single source of truth for translating a raw Alpha intent
(``direction`` + optional ``intent_action`` + optional ``exit_only``
flag) into an unambiguous execution kind:

    open_long  | close_long  | open_short  | close_short  | no_op

Why this exists
---------------
Alpha's upstream signals emit directions like "SELL" / "BUY", which
are inherently ambiguous once the account can hold *either* long or
short positions:

* "SELL" against a LONG position must become **SELL_TO_CLOSE**, never
  SELL_SHORT (that would flip us from a legitimate close into an
  entirely new, uncovered short exposure).
* "BUY" against a SHORT position must become **BUY_TO_COVER**, never
  a new BUY_TO_OPEN long (which would leave the short leg dangling and
  add opposite exposure on top of it).

Historically the executor had `open_long` + `close_long` only; SELL
mapped to close_long by side-effect. With Foundation v2.2 short entries
landing next (P1-A), that heuristic no longer holds — we need explicit
close semantics that cannot accidentally open a new position.

Contract
--------
Callers pass:
* ``direction`` – raw upstream direction string
* ``exit_only`` – True when the intent is an explicit exit (e.g. from the
  Day-Trade Exit Monitor). When True, the router will NEVER classify
  as open_long / open_short; it will return no_op instead.
* ``intent_action`` – explicit action override
  (``SELL_TO_CLOSE`` / ``BUY_TO_COVER`` / ``OPEN_LONG`` / ``OPEN_SHORT``).
  When present, it wins over direction inference.
* ``broker_position_side`` – ``"long"`` / ``"short"`` / ``None`` from
  the broker's ``get_positions`` snapshot for this symbol.
* ``broker_position_qty`` – absolute qty from the broker (>=0). The
  broker's view is authoritative — Alpha's internal row is never
  trusted for close-sizing.

Returns a ``Classification`` dataclass:
* ``kind`` – one of the 5 strings above
* ``reason`` – short machine-readable label explaining the decision
* ``close_qty`` – qty to send on a close order (0 for opens / no_op)

Doctrine
--------
* **Fail closed on ambiguity.** If a caller sends OPEN_LONG while
  a short is open (or OPEN_SHORT while a long is open), we return
  ``no_op`` with reason ``ambiguous_reverse``. Reversing exposure must
  be a *deliberate* two-step: close first, then open.
* **Broker wins.** The classifier only reads what the broker reported.
  Alpha's Mongo row is never consulted here — that's the whole point.
* **Pure.** No I/O, no async, no side effects. Callers do the position
  fetch and hand it in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


BUY_TOKENS = {
    "BUY", "LONG", "STRONG_BUY", "WEAK_BUY", "UP", "BULLISH",
}
SELL_TOKENS = {
    "SELL", "STRONG_SELL", "WEAK_SELL", "DOWN", "BEARISH",
}
SHORT_TOKENS = {
    "SHORT", "SELL_SHORT", "OPEN_SHORT",
}
EXPLICIT_CLOSE_LONG = {"SELL_TO_CLOSE", "CLOSE_LONG"}
EXPLICIT_CLOSE_SHORT = {"BUY_TO_COVER", "CLOSE_SHORT"}
EXPLICIT_OPEN_LONG = {"OPEN_LONG", "BUY_TO_OPEN"}
EXPLICIT_OPEN_SHORT = {"OPEN_SHORT", "SELL_TO_OPEN"}


@dataclass(frozen=True)
class Classification:
    kind: str  # open_long | close_long | open_short | close_short | no_op
    reason: str
    close_qty: float = 0.0

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "close_qty": self.close_qty,
        }


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().upper()


def _long_position(side: Optional[str], qty: float) -> bool:
    return _norm(side) in ("LONG", "BUY") and qty > 0


def _short_position(side: Optional[str], qty: float) -> bool:
    return _norm(side) in ("SHORT", "SELL") and qty > 0


def classify(
    *,
    direction: Optional[str],
    exit_only: bool = False,
    intent_action: Optional[str] = None,
    broker_position_side: Optional[str] = None,
    broker_position_qty: float = 0.0,
) -> Classification:
    """Classify an intent given the broker's authoritative position view.

    See module docstring for full contract.
    """
    action = _norm(intent_action)
    direction_u = _norm(direction)
    qty_abs = abs(float(broker_position_qty or 0.0))
    has_long = _long_position(broker_position_side, qty_abs)
    has_short = _short_position(broker_position_side, qty_abs)

    # ── 1. Explicit action overrides always win ─────────────────────
    if action in EXPLICIT_CLOSE_LONG:
        if has_long:
            return Classification("close_long", "explicit_sell_to_close", qty_abs)
        return Classification("no_op", "no_long_to_close")
    if action in EXPLICIT_CLOSE_SHORT:
        if has_short:
            return Classification("close_short", "explicit_buy_to_cover", qty_abs)
        return Classification("no_op", "no_short_to_cover")
    if action in EXPLICIT_OPEN_LONG:
        if has_short:
            # Explicit reverse — refuse. Operator must close short first.
            return Classification("no_op", "ambiguous_reverse_short_open")
        if exit_only:
            return Classification("no_op", "exit_only_blocks_open_long")
        return Classification("open_long", "explicit_open_long")
    if action in EXPLICIT_OPEN_SHORT:
        if has_long:
            return Classification("no_op", "ambiguous_reverse_long_open")
        if exit_only:
            return Classification("no_op", "exit_only_blocks_open_short")
        return Classification("open_short", "explicit_open_short")

    # ── 2. Direction inference (position-aware) ─────────────────────
    if direction_u in BUY_TOKENS:
        if has_short:
            # BUY signal against an open short → cover, never open new long.
            return Classification("close_short", "buy_covers_short", qty_abs)
        if exit_only:
            return Classification("no_op", "exit_only_no_short_to_cover")
        return Classification("open_long", "buy_opens_long")

    if direction_u in SELL_TOKENS:
        if has_long:
            # SELL signal against an open long → close, never open new short.
            return Classification("close_long", "sell_closes_long", qty_abs)
        if exit_only:
            return Classification("no_op", "exit_only_no_long_to_close")
        # No long and no exit_only → this becomes an OPEN_SHORT candidate.
        # We DON'T open shorts here — that's P1-A / v2.2 territory.
        # Return open_short so the executor's short-capability gate can
        # handle it. Without that gate, the executor will log
        # ``short_signal_only`` and skip.
        return Classification("open_short", "sell_opens_short_candidate")

    if direction_u in SHORT_TOKENS:
        if has_long:
            return Classification("no_op", "ambiguous_short_signal_on_long")
        if exit_only:
            return Classification("no_op", "exit_only_blocks_open_short")
        return Classification("open_short", "explicit_short_direction")

    return Classification("no_op", "non_directional")


__all__ = ["classify", "Classification"]
