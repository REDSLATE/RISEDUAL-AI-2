"""
Post-Trade Autopsy — pure read-only overlay for CLOSED trades.

Mirrors the shape of ``services.decision_reasoning_overlay`` but is
evaluated at close time instead of entry time. Answers one question:

    *Why did this trade win (or lose)?*

Deterministic. No LLM calls. No network I/O. Every branch explicit
and machine-readable so the operator can query historical autopsies
via reason_codes (e.g., ``db.paper_trades.find({"autopsy.reason_codes":
"LOSS_STOPPED_OUT_HIGH_CONVICTION"})``).

Read-only contract
──────────────────
* ``build_post_trade_autopsy(trade_doc)`` is a pure function — deep-
  copied inputs in the tests prove we never mutate the caller's doc.
* Stamped onto the trade doc at close time in both
  ``paper_trade_closer.close_due_paper_trades`` and
  ``crypto_closer.close_expired_crypto_trades``. Never touches entry
  fields.

Reason code taxonomy
────────────────────
* Price-exit outcomes
    - ``WIN_TOOK_PROFIT``              — hit TP cleanly
    - ``LOSS_STOPPED_OUT``             — hit SL cleanly
* Time-exit outcomes
    - ``WIN_HOLD_EXPIRED``             — positive at hold window
    - ``LOSS_HOLD_EXPIRED``            — negative at hold window
    - ``TIMED_OUT_DAY_TRADE``          — day-trade 21:00 UTC timer
* Reversal / hard exits
    - ``WIN_HARD_EXIT``                — reversal flipped us profitably
    - ``LOSS_HARD_EXIT``               — reversal caught us in drawdown
* Conviction annotations (always composed on top of the above)
    - ``HIGH_CONVICTION_WIN``          — entry ≥0.80 conf + won
    - ``HIGH_CONVICTION_LOSS``         — entry ≥0.80 conf + lost
      (these are the trades the adaptation detector needs to see)
    - ``LOW_CONVICTION_WIN``           — entry <0.65 conf + won (lucky)
    - ``LOW_CONVICTION_LOSS``          — entry <0.65 conf + lost
      (expected; proves the gate was working)
* Meta flags
    - ``INTEGRITY_THROTTLE_WAS_ACTIVE`` — symbol failure / mitigation
      was non-zero at entry but the trade fired anyway. Useful for
      understanding drawdown-regime performance.
    - ``ADVERSARIAL_DISAGREED``        — Commander vote opposed the
      trade direction. When the trade loses this is a strong signal
      Commander should get more authority in the next promotion
      review.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

HIGH_CONVICTION_FLOOR: float = 0.80
LOW_CONVICTION_CEILING: float = 0.65


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _outcome_from_pnl(pnl: float) -> str:
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "flat"


def _classify_exit(close_reason: str, outcome: str) -> list[str]:
    """Map ``(close_reason, outcome)`` to price-vs-time exit reason codes.

    ``close_reason`` is the raw string the closer stamped — handle all
    known variants from ``crypto_closer._check_exit_trigger`` +
    ``paper_trade_closer._compute_close`` + the day-trade exit monitor.
    """
    cr = (close_reason or "").lower()

    if "take_profit" in cr or cr == "tp":
        return ["WIN_TOOK_PROFIT"]
    if "stop_loss" in cr or cr == "sl":
        return ["LOSS_STOPPED_OUT"]

    if "day_trade_eod_timer" in cr:
        return ["TIMED_OUT_DAY_TRADE"]

    if "hard_exit" in cr or "reversal" in cr:
        return ["WIN_HARD_EXIT"] if outcome == "win" else ["LOSS_HARD_EXIT"]

    if "hold_window" in cr or "hold_expired" in cr:
        return (
            ["WIN_HOLD_EXPIRED"]
            if outcome == "win"
            else ["LOSS_HOLD_EXPIRED"]
        )

    # Unknown reason — still report the outcome so the row isn't blank.
    return [f"UNKNOWN_{outcome.upper()}"]


def _conviction_code(entry_conf: float, outcome: str) -> str | None:
    if outcome == "flat":
        return None
    if entry_conf >= HIGH_CONVICTION_FLOOR:
        return (
            "HIGH_CONVICTION_WIN" if outcome == "win"
            else "HIGH_CONVICTION_LOSS"
        )
    if entry_conf > 0 and entry_conf <= LOW_CONVICTION_CEILING:
        return (
            "LOW_CONVICTION_WIN" if outcome == "win"
            else "LOW_CONVICTION_LOSS"
        )
    return None


def _summary_text(
    *, symbol: str, direction: str, outcome: str, pnl: float,
    close_reason: str, entry_conf: float,
) -> str:
    direction = (direction or "").upper() or "?"
    magnitude = abs(pnl)
    sign = "+" if pnl > 0 else ("-" if pnl < 0 else "")
    pnl_str = f"{sign}{magnitude:.2f}"
    conv_str = (
        f" entry conf {entry_conf:.2f}" if entry_conf > 0 else ""
    )
    return (
        f"{symbol} {direction} closed {outcome} "
        f"(pnl={pnl_str}) via {close_reason or 'unknown'}{conv_str}"
    )


def build_post_trade_autopsy(trade_doc: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only autopsy block for a closed trade.

    Input is never mutated. Safe to call on partial documents — any
    missing field degrades to a neutral default.

    Returns a dict with keys:
      * ``summary``        — one-line human explanation
      * ``reason_codes``   — list of machine-readable codes
      * ``what_went_right`` — bullet list
      * ``what_went_wrong`` — bullet list
      * ``meta``           — the numeric inputs the overlay consumed,
                              preserved so future queries can compose
                              without re-reading the row fields.
    """
    doc = copy.deepcopy(trade_doc or {})

    symbol = (
        doc.get("symbol") or doc.get("ticker") or "UNKNOWN"
    )
    direction = (doc.get("direction") or "").upper()

    # PnL sourcing — paper_trades uses ``pnl_usd``; crypto uses ``pnl``.
    pnl = _as_float(doc.get("pnl_usd", doc.get("pnl", 0.0)))
    pnl_pct = _as_float(doc.get("pnl_pct"))
    r_multiple = _as_float(doc.get("r_multiple"))

    outcome = doc.get("outcome") or _outcome_from_pnl(pnl)

    close_reason = (
        doc.get("close_reason") or doc.get("auto_close_reason") or ""
    )

    entry_conf = _as_float(
        doc.get("confidence", doc.get("entry_confidence", 0.0))
    )

    # Primary exit-reason dispatch.
    reason_codes: list[str] = _classify_exit(close_reason, outcome)

    # Conviction annotation.
    conv = _conviction_code(entry_conf, outcome)
    if conv is not None:
        reason_codes.append(conv)

    # Integrity mitigation annotation — presence of a non-zero
    # ``risk_multiplier_at_entry`` below 1.0 means the sizing gate
    # was throttling at entry.
    rm = doc.get("risk_multiplier_at_entry")
    try:
        if rm is not None and 0 < float(rm) < 1.0:
            reason_codes.append("INTEGRITY_THROTTLE_WAS_ACTIVE")
    except (TypeError, ValueError):
        pass

    # Adversarial disagreement — ``adversarial_decision`` stored the
    # Commander's vote. If it opposed the trade's direction, flag it.
    adv = (doc.get("adversarial_decision") or "").upper()
    opposed = (
        (direction in ("LONG", "UP", "BULLISH") and adv in ("SHORT_OR_AVOID", "NO_TRADE"))
        or (direction in ("SHORT", "DOWN", "BEARISH") and adv == "LONG")
    )
    if opposed:
        reason_codes.append("ADVERSARIAL_DISAGREED")

    # Bullet cases — populated from the dominant reason code.
    primary = reason_codes[0] if reason_codes else ""
    what_went_right: list[str] = []
    what_went_wrong: list[str] = []
    if outcome == "win":
        if primary == "WIN_TOOK_PROFIT":
            what_went_right.append(
                "Price reached the take-profit level cleanly."
            )
        elif primary == "WIN_HOLD_EXPIRED":
            what_went_right.append(
                "Position closed positive at hold-window expiry."
            )
        elif primary == "WIN_HARD_EXIT":
            what_went_right.append(
                "Reversal exit captured profit before price gave back."
            )
        if conv == "HIGH_CONVICTION_WIN":
            what_went_right.append(
                "Entry confidence was high — the signal was trustworthy."
            )
        if "ADVERSARIAL_DISAGREED" in reason_codes:
            what_went_wrong.append(
                "Commander disagreed at entry — re-examine whether "
                "Commander's shadow vote needs more authority."
            )
    elif outcome == "loss":
        if primary == "LOSS_STOPPED_OUT":
            what_went_wrong.append(
                "Price hit the stop-loss — thesis invalidated by the "
                "configured risk band."
            )
        elif primary == "LOSS_HOLD_EXPIRED":
            what_went_wrong.append(
                "Position still negative at hold-window expiry — "
                "signal never played out in the allotted horizon."
            )
        elif primary == "LOSS_HARD_EXIT":
            what_went_wrong.append(
                "Hard reversal exit — thesis was invalidated mid-trade."
            )
        elif primary == "TIMED_OUT_DAY_TRADE":
            what_went_wrong.append(
                "Day-trade EOD timer closed the position below entry."
            )
        if conv == "HIGH_CONVICTION_LOSS":
            what_went_wrong.append(
                "Entry confidence was high yet the trade lost — "
                "candidate for adaptation review."
            )
        if "INTEGRITY_THROTTLE_WAS_ACTIVE" in reason_codes:
            what_went_wrong.append(
                "Integrity throttle was active at entry — sizing was "
                "already discounted but the loss still landed."
            )

    summary = _summary_text(
        symbol=symbol, direction=direction, outcome=outcome, pnl=pnl,
        close_reason=close_reason, entry_conf=entry_conf,
    )

    # ── Slippage attribution ──
    # Read the entry-side slippage that was stamped at fill time
    # (see services/slippage_simulator.py + paper traders). Compute
    # the dollar drag at the trade's actual notional so the operator
    # can see what the spread cost vs what the strategy earned.
    slippage_block = _slippage_attribution(doc)
    if slippage_block is not None:
        if slippage_block.get("entry_bps", 0) > 0:
            # High slippage on a winning trade still hurt the return;
            # high slippage on a losing trade made the loss worse.
            tag = (
                f"Entry slippage cost {slippage_block['entry_bps']:.1f} bps "
                f"(${slippage_block['entry_dollar_cost']:.2f})"
            )
            if outcome == "loss":
                what_went_wrong.append(
                    f"{tag} — {slippage_block['fill_method']} fill on the "
                    "wrong side of the book amplified the loss."
                )
            elif outcome == "win":
                what_went_right.append(
                    f"{tag} — strategy still profited despite paying the "
                    "spread."
                )

    return {
        "summary": summary,
        "reason_codes": reason_codes,
        "what_went_right": what_went_right,
        "what_went_wrong": what_went_wrong,
        "meta": {
            "outcome": outcome,
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "r_multiple": round(r_multiple, 4),
            "entry_confidence": round(entry_conf, 4),
            "close_reason": close_reason,
            "direction": direction,
            "symbol": symbol,
            "adversarial_decision": adv or None,
            "risk_multiplier_at_entry": _as_float(rm) if rm is not None else None,
        },
        "slippage": slippage_block,
    }


def _slippage_attribution(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Compose the realised slippage cost block from the entry +
    exit fields stamped at fill time.

    Returns ``None`` when the trade row predates the slippage stamp
    (legacy historical rows) so the caller can render "—" instead
    of fabricated zeros. When stamped but on the ``mid_only``
    path (legacy provider, no bid/ask), reports zero cost with the
    method preserved so the UI can explain why.
    """
    method = doc.get("slippage_method")
    if method is None:
        return None  # row predates the slippage stamp

    entry_bps = _as_float(doc.get("slippage_bps"))
    # Notional: shares × entry_price for equity, position_size_usd
    # for crypto. Both lanes carry one of these directly.
    entry_price = _as_float(doc.get("entry_price"))
    shares = _as_float(doc.get("shares"))
    crypto_size_usd = _as_float(doc.get("position_size_usd"))

    if shares > 0 and entry_price > 0:
        notional_usd = shares * entry_price
    elif crypto_size_usd > 0:
        notional_usd = crypto_size_usd
    else:
        notional_usd = 0.0

    # Dollar cost = notional × (bps / 10_000). Always non-negative
    # so the panel can render a single P&L drag column.
    entry_dollar_cost = round(
        notional_usd * (abs(entry_bps) / 10_000.0), 4,
    )

    # ── Exit-side slippage (stamped by closers as of 2026-05-04) ──
    exit_method = doc.get("exit_slippage_method")
    exit_bps_raw = doc.get("exit_slippage_bps")
    exit_bps: float | None
    exit_dollar_cost: float | None
    if exit_method is None or exit_bps_raw is None:
        exit_bps = None
        exit_dollar_cost = None
    else:
        exit_bps = round(abs(_as_float(exit_bps_raw)), 2)
        exit_dollar_cost = round(
            notional_usd * (exit_bps / 10_000.0), 4,
        )

    total_bps = round(
        abs(entry_bps) + (exit_bps if exit_bps is not None else 0.0), 2,
    )
    total_dollar_cost = round(
        entry_dollar_cost + (exit_dollar_cost if exit_dollar_cost is not None else 0.0),
        4,
    )

    return {
        "entry_bps": round(abs(entry_bps), 2),
        "entry_dollar_cost": entry_dollar_cost,
        "exit_bps": exit_bps,
        "exit_dollar_cost": exit_dollar_cost,
        "exit_method": exit_method,
        "total_bps": total_bps,
        "total_dollar_cost": total_dollar_cost,
        "fill_method": method,
        "notional_usd": round(notional_usd, 2),
        "quote_source": doc.get("quote_source"),
    }
