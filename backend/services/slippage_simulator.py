"""
Slippage Simulator — pure-function fill-price model for the paper
traders.

Why this exists
───────────────
Until 2026-05-04 both paper traders (equity + crypto) recorded the
mid as the entry price. With Kraken as the primary crypto quote and
Alpaca's IEX feed as the primary equity quote, the ``bid`` / ``ask``
fields now flow through every quote dict — so we can simulate the
realistic fact that a market order fills at the unfavourable side
of the book:

    LONG  entry → fills at ASK (buying from a seller)
    SHORT entry → fills at BID (selling into a buyer)

Same for exits: longs sell at the bid, shorts cover at the ask.

The realised slippage cost is then the spread's contribution to the
trade's P&L — which the autopsy can later attribute as a
performance drag distinct from the strategist's directional accuracy.

Pure function
─────────────
``apply_entry_slippage(quote, direction)`` is deterministic, has no
I/O, no Mongo, no clock dependency. Returns ``(fill_price,
slippage_bps, method)`` where ``method`` is one of:

* ``"ask_fill"``   — filled at ask (LONG entry)
* ``"bid_fill"``   — filled at bid (SHORT entry)
* ``"mid_only"``   — provider didn't expose bid/ask; mid was the
                     best we had. Slippage = 0.
* ``"unknown"``    — quote was empty / malformed; caller should
                     skip the trade.

The trade row stamps ``entry_quote_bid``, ``entry_quote_ask``,
``slippage_bps``, ``slippage_method`` so the autopsy + future
calibration work can audit exactly how much spread cost each fill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SlippageResult:
    fill_price: float
    slippage_bps: float
    method: str  # ask_fill / bid_fill / mid_only / unknown
    bid: float | None
    ask: float | None
    mid: float | None


_LONG_ALIASES = {"LONG", "BUY", "UP", "BULLISH", "STRONG_BUY", "WEAK_BUY"}
_SHORT_ALIASES = {"SHORT", "SELL", "DOWN", "BEARISH", "STRONG_SELL", "WEAK_SELL"}


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f <= 0:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _direction_side(direction: str) -> str | None:
    d = (direction or "").upper().strip()
    if d in _LONG_ALIASES:
        return "long"
    if d in _SHORT_ALIASES:
        return "short"
    return None


def apply_entry_slippage(
    quote: dict[str, Any] | None, direction: str,
) -> SlippageResult:
    """Compute the realistic entry fill price for a market order.

    ``quote`` may be ``None`` / empty (legacy fallback providers
    like Mongo cache that don't expose bid/ask) — in that case we
    degrade gracefully to mid-only with ``slippage_bps=0``.

    The bot's existing flow short-circuits on ``fill_price <= 0`` so
    the ``unknown`` method effectively skips the trade.
    """
    if not quote:
        return SlippageResult(
            fill_price=0.0, slippage_bps=0.0, method="unknown",
            bid=None, ask=None, mid=None,
        )

    bid = _coerce_float(quote.get("bid"))
    ask = _coerce_float(quote.get("ask"))
    last = _coerce_float(quote.get("last"))
    price = _coerce_float(quote.get("price"))

    # Best estimate of the mid. Prefer explicit price (which is the
    # provider's mid when bid+ask were both populated), fall back to
    # last trade.
    mid = price or last

    side = _direction_side(direction)

    # No usable provider data at all.
    if mid is None and bid is None and ask is None:
        return SlippageResult(
            fill_price=0.0, slippage_bps=0.0, method="unknown",
            bid=None, ask=None, mid=None,
        )

    # Only mid available — degrade to mid_only with zero slippage.
    if bid is None or ask is None or side is None:
        fallback = mid or bid or ask or 0.0
        return SlippageResult(
            fill_price=round(fallback, 6),
            slippage_bps=0.0,
            method="mid_only",
            bid=bid, ask=ask, mid=mid,
        )

    # Both bid+ask present — apply directional slippage.
    if side == "long":
        fill_price = ask
        method = "ask_fill"
    else:  # short
        fill_price = bid
        method = "bid_fill"

    if mid is None or mid <= 0:
        # Defensive — should be unreachable given mid was set above,
        # but guard against future quote-shape changes.
        slip_bps = 0.0
    else:
        slip_bps = ((fill_price - mid) / mid) * 10_000.0
        # For SHORT, ``fill_price - mid`` is negative; the
        # SHORT-seller is hit on the bid, which IS adverse from the
        # short's perspective (selling cheaper than mid = worse
        # entry). Flip the sign so ``slippage_bps`` is always
        # interpreted consistently as "how much worse than mid".
        if side == "short":
            slip_bps = -slip_bps

    return SlippageResult(
        fill_price=round(fill_price, 6),
        slippage_bps=round(slip_bps, 2),
        method=method,
        bid=bid, ask=ask, mid=mid,
    )


def apply_exit_slippage(
    quote: dict[str, Any] | None, direction: str,
) -> SlippageResult:
    """Closing-side mirror of ``apply_entry_slippage``.

    ``direction`` is the ORIGINAL trade direction:

    * Closing a LONG  → SELLS at bid
    * Closing a SHORT → BUYS at ask

    Same fall-through rules as the entry side.
    """
    if not quote:
        return SlippageResult(
            fill_price=0.0, slippage_bps=0.0, method="unknown",
            bid=None, ask=None, mid=None,
        )

    bid = _coerce_float(quote.get("bid"))
    ask = _coerce_float(quote.get("ask"))
    last = _coerce_float(quote.get("last"))
    price = _coerce_float(quote.get("price"))
    mid = price or last

    side = _direction_side(direction)

    if mid is None and bid is None and ask is None:
        return SlippageResult(
            fill_price=0.0, slippage_bps=0.0, method="unknown",
            bid=None, ask=None, mid=None,
        )

    if bid is None or ask is None or side is None:
        fallback = mid or bid or ask or 0.0
        return SlippageResult(
            fill_price=round(fallback, 6),
            slippage_bps=0.0,
            method="mid_only",
            bid=bid, ask=ask, mid=mid,
        )

    if side == "long":
        # Closing a long → sell at bid.
        fill_price = bid
        method = "bid_fill"
    else:
        # Closing a short → buy at ask.
        fill_price = ask
        method = "ask_fill"

    if mid is None or mid <= 0:
        slip_bps = 0.0
    else:
        # For close-LONG-at-bid: bid < mid → negative delta is
        # adverse; flip sign so positive bps = "worse than mid".
        slip_bps = ((mid - fill_price) / mid) * 10_000.0
        if side == "short":
            # Closing a short BUYS at ask → ask > mid is adverse;
            # flip again so positive = adverse.
            slip_bps = -slip_bps

    return SlippageResult(
        fill_price=round(fill_price, 6),
        slippage_bps=round(slip_bps, 2),
        method=method,
        bid=bid, ask=ask, mid=mid,
    )
