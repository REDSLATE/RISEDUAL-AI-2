"""Deterministic TP/SL path simulator.

Two entry points:
  * `get_trade_result_from_df(signal, df, start_index, lookahead=20)` —
    synchronous, DataFrame-based, matches the user-supplied drop-in
    spec verbatim. Used by the backtester.
  * `get_trade_result_from_bars(signal, bars, start_index, lookahead=20)` —
    same logic but accepts a list[dict] with `high`/`low`/`close` keys,
    so callers that don't have pandas don't pay the import cost.

Pending semantics:
  * Not enough future candles → `status="pending"`, `win=None`. Learning
    must filter these out. The live path uses this when the lookahead
    simply doesn't exist yet (real-time signal, no future bars).
  * Explicit SL/TP ordering: SL is checked BEFORE TP per bar to stay
    conservative — if both are hit in the same candle we don't know
    which triggered first, and pessimism is the safer assumption for
    training. This mirrors common backtesting conventions.
"""
from __future__ import annotations

from typing import Any, Iterable

from .models import Signal, TradeResult


def _build_result(entry: float, exit_price: float, pnl: float, win: bool,
                  stop_loss: float | None = None) -> TradeResult:
    """Compose a resolved (win/loss) TradeResult with r_multiple."""
    # r_multiple uses |entry - stop_loss| as the denominator when SL is
    # known (that's the fixed risk the trader signed up for). Falls
    # back to |entry - exit_price| only when SL is absent — correct for
    # e.g. timeout exits with no SL configured.
    if stop_loss is not None and stop_loss != entry:
        risk = abs(entry - stop_loss)
    else:
        risk = abs(entry - exit_price) if entry != exit_price else 0.0
    r_multiple = pnl / risk if risk != 0 else 0.0
    return TradeResult(
        pnl=float(pnl),
        exit_price=float(exit_price),
        win=bool(win),
        status="win" if win else "loss",
        r_multiple=round(r_multiple, 4),
    )


def _pending(entry: float) -> TradeResult:
    """Third-label state for unresolved trades (insufficient future)."""
    return TradeResult(
        pnl=0.0,
        exit_price=float(entry),
        win=None,
        status="pending",
        r_multiple=0.0,
    )


def _walk_bars(signal: Signal, bars: Iterable[dict]) -> TradeResult | None:
    """Advance through future bars, emit first TP/SL hit. None → no hit."""
    entry = signal.entry
    tp = signal.take_profit
    sl = signal.stop_loss
    direction = signal.direction

    for row in bars:
        high = float(row["high"])
        low = float(row["low"])

        if direction == "LONG":
            if low <= sl:
                return _build_result(entry, sl, sl - entry, False, stop_loss=sl)
            if high >= tp:
                return _build_result(entry, tp, tp - entry, True, stop_loss=sl)
        else:  # SHORT
            if high >= sl:
                return _build_result(entry, sl, entry - sl, False, stop_loss=sl)
            if low <= tp:
                return _build_result(entry, tp, entry - tp, True, stop_loss=sl)
    return None


def get_trade_result_from_bars(signal: Signal, bars: list[dict],
                               start_index: int, lookahead: int = 20) -> TradeResult:
    """Evaluate the trade over a bar list starting AFTER `start_index`.

    `bars` MUST contain dicts with `high`, `low`, `close` keys (strings
    or floats — we coerce). Use list[dict] instead of pandas when the
    caller doesn't already have a DataFrame to avoid the import cost.
    """
    n = len(bars)
    if start_index + 1 >= n:
        return _pending(signal.entry)

    # Explicit `start_index + 1` slicing — we only ever look FORWARD,
    # never including the entry bar. Enforced so the signature and
    # behaviour are honest about data usage.
    future = bars[start_index + 1 : start_index + 1 + lookahead]
    if not future:
        return _pending(signal.entry)

    hit = _walk_bars(signal, future)
    if hit is not None:
        return hit

    # Timeout — mark to last close. `win` is determined purely by pnl
    # sign, not by whether TP/SL was touched. Matches real-life
    # "trade expired at end of window, I took what I had" semantics.
    last_price = float(future[-1]["close"])
    if signal.direction == "LONG":
        pnl = last_price - signal.entry
    else:
        pnl = signal.entry - last_price
    return _build_result(
        entry=signal.entry,
        exit_price=last_price,
        pnl=pnl,
        win=(pnl > 0),
        stop_loss=signal.stop_loss,
    )


def get_trade_result_from_df(signal: Any, df: Any, start_index: int,
                             lookahead: int = 20) -> dict:
    """Drop-in entry matching the user-supplied spec.

    Accepts a pandas-like DataFrame with `high`/`low`/`close` columns.
    Returns a dict (not a TradeResult) for backwards compatibility with
    the original spec's consumer code. Internally delegates to the
    bar-list implementation so the core logic is not duplicated.

    `signal` may be a `Signal` dataclass OR any object exposing
    `.entry / .stop_loss / .take_profit / .direction` — the original
    spec used a plain namespaced object, so we keep that flexible.
    """
    # Coerce signal to the dataclass if it's a bare namespace. Accessor
    # pattern identical either way.
    if not isinstance(signal, Signal):
        signal = Signal(
            asset=getattr(signal, "asset", "UNKNOWN"),
            direction=getattr(signal, "direction", "LONG"),
            entry=float(getattr(signal, "entry")),
            stop_loss=float(getattr(signal, "stop_loss")),
            take_profit=float(getattr(signal, "take_profit")),
            confidence=float(getattr(signal, "confidence", 0)),
        )

    # Pandas DataFrame → list of dicts. iloc access here keeps the
    # original spec's slicing semantics intact.
    n = len(df)
    if start_index + 1 >= n:
        return _pending(signal.entry).as_dict()

    future_df = df.iloc[start_index + 1 : start_index + 1 + lookahead]
    if len(future_df) == 0:
        return _pending(signal.entry).as_dict()

    # Convert to dict-rows; pandas `.iterrows()` would also work but
    # list[dict] keeps this function portable if we ever strip pandas.
    bars = future_df.to_dict("records")
    # Reuse the core logic (start_index arg is 0 since we already sliced).
    result = get_trade_result_from_bars(signal, bars, start_index=-1, lookahead=lookahead)
    return result.as_dict()
