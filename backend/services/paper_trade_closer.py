"""Paper-Trade Auto-Closer — APScheduler job that closes paper
trades opened by the AI paper trader after a fixed holding window.

DTD-tagged: writes execution-state to ``paper_trades``.

The 2026-04-25 audit found 5 paper_trades (all `direction='down'`,
all from 2026-04-16) sitting in `status: 'open'` for 9 days because
NO code path closed them. The `prediction_labeler` only updates
`features_snapshots` and `predictions` — it never touches
`paper_trades`. The result was a misleading "live days 2/30" Tier 3
metric and a stale paper-portfolio that didn't reflect any of the
underlying market moves.

This service fills that gap. It runs hourly. For every trade in
`paper_trades` that has been open for ≥ ``PAPER_TRADE_HOLD_HOURS``
(default 24h), it fetches the current quote, computes P&L
direction-aware (longs get `current - entry`, shorts get `entry -
current`), writes the close fields, and stamps the paper_positions
row.

Failure-safe
------------
* Per-trade try/except — one quote failure doesn't poison the
  batch.
* No raise from the scheduler entry point. The product still runs
  if the closer is broken; we just see "open" trades pile up
  again, which the new self-test tripwire (see future task)
  would surface.
* Idempotent — `update_one({status: 'open', ...})` ensures we
  never double-close.

Operator knobs
--------------
* ``PAPER_TRADE_HOLD_HOURS`` (env, default 24)
* ``PAPER_TRADE_CLOSER_DISABLED`` (env, default off — kill switch)
"""
from __future__ import annotations

__domain__ = "DTD"

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_HOLD_HOURS = 24

# Outcome-label threshold for observation_fill rows (shares=0).
# Mirrors ``services.backfill_outcome_pairer._outcome_label`` so
# every Sovereign outcome — whether from a real Kelly-sized trade,
# an Alpaca backfill pair, or an observation_fill rung — uses the
# same ±0.5% win/loss/flat cut-off. Keeping this in lock-step is
# critical: a drift here makes MC's learning signal incoherent
# across receipt types.
_OBSERVATION_PCT_THRESHOLD = 0.005


def _hold_hours() -> int:
    raw = os.environ.get("PAPER_TRADE_HOLD_HOURS")
    if not raw:
        return _DEFAULT_HOLD_HOURS
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        logger.warning(
            "[paper-closer] invalid PAPER_TRADE_HOLD_HOURS=%r — using default %d",
            raw, _DEFAULT_HOLD_HOURS,
        )
        return _DEFAULT_HOLD_HOURS


def _is_disabled() -> bool:
    return os.environ.get("PAPER_TRADE_CLOSER_DISABLED", "").lower() in (
        "1", "true", "yes", "on",
    )


def _compute_close(direction: str, entry: float, current: float,
                   shares: float) -> tuple[float, float, str]:
    """Direction-aware P&L. ``direction='down'`` = short — gains
    when price falls. Returns (pnl_usd, pnl_pct, outcome).

    2026-02-23 fix: the outcome label is now computed from
    ``pnl_pct``, not ``pnl_usd``. The previous logic
    (``"win" if pnl_usd > 0 else …``) was correct for real Kelly-
    sized trades but always returned ``"flat"`` for
    ``observation_fill`` rows (where ``shares=0`` → ``pnl_usd=0``
    by construction). That silently destroyed the Sovereign
    learning signal for every observation rung — MC saw a flood
    of "flat" outcomes regardless of whether Alpha's directional
    bet was right.

    The pct-based rule mirrors
    ``backfill_outcome_pairer._outcome_label`` so the win/loss/flat
    cut-off (±0.5%) is consistent across receipt types — real
    fills, Alpaca backfill pairs, and observation rungs all grade
    on the same scale.
    """
    if direction == "down":
        pnl_usd = (entry - current) * shares
        pnl_pct = (entry - current) / entry if entry else 0.0
    else:
        pnl_usd = (current - entry) * shares
        pnl_pct = (current - entry) / entry if entry else 0.0
    if pnl_pct > _OBSERVATION_PCT_THRESHOLD:
        outcome = "win"
    elif pnl_pct < -_OBSERVATION_PCT_THRESHOLD:
        outcome = "loss"
    else:
        outcome = "flat"
    return round(pnl_usd, 2), round(pnl_pct, 4), outcome


async def _fetch_price(ticker: str, db: Any) -> float | None:
    """Live quote first, cached fallback second. Returns None if
    neither path produces a price — the trade stays open and the
    next hourly tick retries."""
    try:
        from services.price_provider import get_quote
        quote = await get_quote(ticker)
        price = quote.get("price") or quote.get("c") or quote.get("close")
        if price is not None:
            return float(price)
    except Exception as exc:
        logger.warning(
            "[paper-closer] live quote failed for %s: %s — trying cache",
            ticker, exc,
        )
    try:
        cached = await db["price_cache"].find_one(
            {"key": f"quote_{ticker.upper()}"},
            sort=[("updated_at", -1)],
        )
        if cached:
            data = cached.get("data") or {}
            cp = data.get("price")
            if cp is not None:
                return float(cp)
    except Exception as exc:
        logger.warning("[paper-closer] cache fallback failed for %s: %s", ticker, exc)
    return None


async def close_due_paper_trades(db: Any) -> dict:
    """Close every AI-driven paper trade past its hold window.

    Returns ``{closed, skipped, errors, holds}`` summary so the
    scheduler entry can log a one-liner.
    """
    if _is_disabled():
        logger.info("[paper-closer] disabled via env — skipping")
        return {"closed": 0, "skipped": 0, "errors": 0, "holds": 0,
                "disabled": True}

    if db is None:
        return {"closed": 0, "skipped": 0, "errors": 0, "holds": 0}

    hold_hours = _hold_hours()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hold_hours)
    now = datetime.now(timezone.utc)

    # Close rows from BOTH paths:
    #  * "open"             — real paper trades (Kelly sized > $0)
    #  * "observation_open" — first-rung observation_fill receipts
    #    (Kelly sized $0). Schema is paper_trades-shaped but
    #    ``shares = 0``, so the PnL math still runs cleanly and
    #    stamps a `pnl_pct` + `outcome` we can grade against — the
    #    learning signal lives in ``pnl_pct`` not in $ exposure.
    # Manual UI buy/sell rows (Schema B, no `status`) are still
    # excluded.
    cursor = db["paper_trades"].find(
        {
            "status": {"$in": ["open", "observation_open"]},
            "opened_at": {"$type": "date", "$lt": cutoff},
        },
        {"_id": 0},
    ).limit(500)

    closed = 0
    skipped = 0
    errors = 0
    holds = 0  # quote failures — stays open, retried next tick
    quote_cache: dict[str, float | None] = {}

    async for t in cursor:
        try:
            ticker = t["ticker"]
            entry = float(t.get("entry_price") or 0)
            shares = float(t.get("shares") or 0)
            direction = str(t.get("direction") or "up").lower()
            trade_id = t.get("trade_id")
            is_observation = t.get("status") == "observation_open"

            # Observation receipts (shares=0) compute pct PnL against
            # market price without dollar exposure — the learning
            # value is in the rate/direction, not the size. Real
            # trades still require shares > 0.
            min_required = 0.0 if is_observation else 1.0
            if not (ticker and trade_id and entry > 0 and shares >= min_required):
                logger.warning(
                    "[paper-closer] malformed trade row skipped: %s", trade_id,
                )
                skipped += 1
                continue

            if ticker not in quote_cache:
                quote_cache[ticker] = await _fetch_price(ticker, db)
            current = quote_cache[ticker]
            if current is None:
                holds += 1
                continue

            # Live Alpaca quote at close — needed for exit slippage.
            # Falls back to mid (current) when bid/ask unavailable.
            exit_quote: dict | None = None
            try:
                from services.alpaca_equity_quotes import get_alpaca_equity_quote
                exit_quote = await get_alpaca_equity_quote(ticker)
            except Exception as _eq_exc:  # noqa: BLE001
                logger.debug(
                    "[paper-closer] live exit-quote probe failed for %s: %s",
                    ticker, _eq_exc,
                )

            from services.slippage_simulator import apply_exit_slippage
            _xslip = apply_exit_slippage(
                exit_quote or {"price": current}, direction,
            )
            # Realised exit fill — bid for close-LONG, ask for close-SHORT.
            # ``mid_only`` keeps the existing ``current`` price unchanged.
            if _xslip.method in ("ask_fill", "bid_fill"):
                current = _xslip.fill_price

            pnl_usd, pnl_pct, outcome = _compute_close(
                direction, entry, current, shares,
            )

            # Post-trade autopsy — pure overlay built from the
            # about-to-be-closed state. Stamped alongside the close
            # fields so a single $set lands both together.
            try:
                from services.post_trade_autopsy import (
                    build_post_trade_autopsy,
                )
                autopsy = build_post_trade_autopsy({
                    **t,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "outcome": outcome,
                    "close_reason": f"hold_window_{hold_hours}h",
                    "auto_close_reason": f"hold_window_{hold_hours}h",
                    "exit_slippage_bps": _xslip.slippage_bps,
                    "exit_slippage_method": _xslip.method,
                    "exit_quote_bid": _xslip.bid,
                    "exit_quote_ask": _xslip.ask,
                    "exit_quote_mid": _xslip.mid,
                })
            except Exception as _ap_exc:  # noqa: BLE001
                logger.debug(
                    "[paper-closer] autopsy build failed: %s", _ap_exc,
                )
                autopsy = None

            update_set = {
                "status": "observation_closed" if is_observation else "closed",
                "closed_at": now,
                "exit_price": current,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "outcome": outcome,
                "auto_closed": True,
                "auto_close_reason": f"hold_window_{hold_hours}h",
                # Exit-side slippage stamp — mirrors entry stamp.
                "exit_quote_bid": _xslip.bid,
                "exit_quote_ask": _xslip.ask,
                "exit_quote_mid": _xslip.mid,
                "exit_slippage_bps": _xslip.slippage_bps,
                "exit_slippage_method": _xslip.method,
                "exit_quote_source": (exit_quote or {}).get("source"),
            }
            if autopsy is not None:
                update_set["autopsy"] = autopsy

            res = await db["paper_trades"].update_one(
                {"trade_id": trade_id,
                 "status": "observation_open" if is_observation else "open"},
                {"$set": update_set},
            )
            if res.modified_count:
                closed += 1
                # ── Stage 3: backfill realised outcome onto the
                # matching decision_pairs row (if one was filed at
                # trade-open time). Best-effort — never blocks the
                # close.
                try:
                    from services.decision_outcome_writer import (
                        write_outcome_for_trade,
                    )
                    await write_outcome_for_trade(
                        db,
                        trade_id=trade_id,
                        direction=direction,
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        outcome_label=outcome,
                        closed_at=now,
                    )
                except Exception as _ow_exc:  # noqa: BLE001
                    logger.debug(
                        "[paper-closer] outcome write failed for %s: %s",
                        ticker, _ow_exc,
                    )

                # ── 2026-05-22: enqueue outcome for the Sovereign
                # sidecar's inbox so its LocalState._outcomes can
                # populate. Without this, MC sees Alpha contribute
                # an empty `recent_outcomes` array on every tick
                # (the empty-payload screenshot regression).
                # Real fills AND observation closes both feed the
                # bridge — both are learning-eligible.
                try:
                    from services.sovereign_outcome_bridge import (
                        enqueue_outcome,
                    )
                    await enqueue_outcome(
                        db,
                        brain="alpha",
                        trade_id=trade_id,
                        symbol=ticker,
                        direction=direction,
                        confidence=float(t.get("confidence") or 0.0),
                        outcome_label=outcome,
                        notional=float(t.get("position_usd") or 0.0),
                        extras={
                            "receipt_type": t.get("receipt_type") or "real_fill",
                            "pnl_pct": pnl_pct,
                        },
                        # 2026-05-22 (Gap 2): forward provenance so MC
                        # gets the audit lineage on its recent_outcomes
                        # snapshot. None when the trade row never
                        # carried a signal match (synthetic / pre-
                        # backfill rows); the bridge stores them as
                        # null and the sidecar omits them on the wire.
                        sovereign_decision_id=t.get("sovereign_decision_id"),
                        prediction_id=t.get("prediction_id"),
                        source_signal=t.get("source_signal"),
                    )
                except Exception as _bridge_exc:  # noqa: BLE001
                    logger.debug(
                        "[paper-closer] outcome bridge enqueue failed: %s",
                        _bridge_exc,
                    )

                # ── Phase 4 (2026-02-27): backfill the realised
                # outcome onto every Shelly memory in the decision
                # cluster (Alpha + council brains + MC + shared).
                # Without this, the federation's reasoning paths
                # forever report "Not enough Shelly memory yet" —
                # they only count memories with outcome.pnl_pct.
                # Fail-soft; per-node try/except inside the helper.
                try:
                    from shelly.outcome_backfill import (
                        backfill_outcome_for_trade,
                    )
                    await backfill_outcome_for_trade(
                        db,
                        ticker=ticker,
                        direction=direction,
                        trade_id=trade_id,
                        opened_at=t.get("opened_at"),
                        closed_at=now,
                        pnl_pct=pnl_pct,
                        outcome_label=outcome,
                    )
                except Exception as _shelly_exc:  # noqa: BLE001
                    logger.debug(
                        "[paper-closer] shelly outcome backfill failed: %s",
                        _shelly_exc,
                    )
                # Sync paper_positions roster (best-effort)
                try:
                    await db["paper_positions"].update_many(
                        {"ticker": ticker, "status": "open"},
                        {"$set": {"status": "closed", "closed_at": now}},
                    )
                except Exception as exc:
                    logger.warning(
                        "[paper-closer] paper_positions sync failed for %s: %s",
                        ticker, exc,
                    )
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            logger.exception("[paper-closer] row failed: %s", exc)

    if closed or holds or errors:
        logger.info(
            "[paper-closer] hold=%dh closed=%d skipped=%d holds=%d errors=%d",
            hold_hours, closed, skipped, holds, errors,
        )

    return {
        "closed": closed,
        "skipped": skipped,
        "errors": errors,
        "holds": holds,
        "hold_hours": hold_hours,
    }
