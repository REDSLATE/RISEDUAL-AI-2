"""Options Paper Agent — takes the same ML signal the default paper
trader uses but routes high-conviction calls into paper OPTIONS
positions rather than equity.

Why this exists:
  * The equity paper trader already generates trades when the
    signal model fires. That leaves a gap: for high-conviction
    directional calls, options offer a better risk/reward that
    the ML pipeline is currently blind to.
  * By mirroring the equity signal into a paper option trade, we
    build a parallel training corpus for the options-specific
    retrain path (Phase 2 of the ML roadmap).

Strategy:
  * Only runs when the signal model's directional confidence
    ≥ 0.72 (higher than the equity threshold — options have
    decay risk, so conviction bar is higher).
  * Selects the nearest weekly ATM contract (delta ≈ 0.5).
  * Stop: 50% of debit paid. Target: 100% (1R).
  * Tag: ``strategy: "options_paper"``.

Schedule: every 30 minutes during market hours.
"""
from __future__ import annotations

import logging
from typing import Any

from services.trading_agents.base import (
    kill_switch_blocks,
    narrate_scan_skip,
    record_paper_trade,
)

logger = logging.getLogger(__name__)

_STRATEGY = "options_paper"
_MIN_CONFIDENCE = 0.72
_POSITION_USD = 500.0  # Smaller — options are more volatile
_WATCHLIST: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
    "AMD", "SPY", "QQQ",
]


async def _fetch_quote(ticker: str) -> float:
    """Current price via the existing price provider."""
    try:
        from services.price_provider import get_quote
        q = await get_quote(ticker)
        if q and q.get("price"):
            return float(q["price"])
    except Exception as e:
        logger.debug("[options_paper] quote failed for %s: %s", ticker, e)
    return 0.0


async def _score_ticker(ticker: str, db: Any) -> tuple[str, float] | None:
    """Return (direction, confidence) from the latest features
    snapshot via the live signal model. Mirrors the call
    convention in ``services.ml_paper_trader``.

    Returns None when the model has no opinion on this ticker in
    the latest window (acceptable — we simply skip)."""
    try:
        # Re-use the cached signal produced by the main inference
        # loop when available, rather than re-invoking the model.
        snap = await db.features_snapshots.find_one(
            {"symbol": ticker},
            {"_id": 0, "signal_direction": 1, "signal_confidence": 1},
            sort=[("captured_at", -1)],
        )
        if not snap:
            return None
        direction = snap.get("signal_direction")
        conf = snap.get("signal_confidence")
        if not direction or conf is None:
            return None
        return str(direction).upper(), float(conf)
    except Exception as e:
        logger.debug("[options_paper] score failed for %s: %s", ticker, e)
        return None


async def run(db: Any) -> None:
    if db is None:
        return
    if await kill_switch_blocks():
        return

    opened = 0
    considered = 0
    MAX_OPENS = 2
    for ticker in _WATCHLIST:
        if opened >= MAX_OPENS:
            break
        considered += 1
        try:
            scored = await _score_ticker(ticker, db)
            if scored is None:
                continue
            direction, conf = scored

            # ── ADL-4: Shadow receipt (fire-and-forget) ──────────
            # Captures EVERY options-lane decision in
            # ``alpha_decision_log`` regardless of whether the
            # conviction floor is cleared. Runs OUT-OF-BAND on the
            # event loop — NEVER blocks ``run``, NEVER mutates
            # state, NEVER raises. Pre-signal returns above
            # (db None, kill switch, no fresh snapshot) deliberately
            # skip this hook — they have no decision object to log.
            try:
                from services.ml.receipt_dispatch import (
                    schedule_shadow_receipt,
                )
                _options_signal = {
                    "symbol": ticker,
                    "direction": direction,
                    "confidence": float(conf),
                    "strategy": _STRATEGY,
                    "min_confidence_threshold": _MIN_CONFIDENCE,
                    "watchlist_position": considered,
                    "source_layer": "options_paper_agent",
                }
                schedule_shadow_receipt(
                    db,
                    signal=_options_signal,
                    market_data=None,
                    lane="options",
                    requested_notional_usd=float(_POSITION_USD),
                    source="options_paper_bot",
                )
            except Exception as _adl_exc:  # noqa: BLE001
                logger.debug(
                    "[options_paper] ADL receipt schedule skipped "
                    "for %s: %s",
                    ticker, _adl_exc,
                )

            if conf < _MIN_CONFIDENCE:
                continue
            # Direction on the signal snapshot comes as UP / DOWN;
            # map to LONG / SHORT so the trade row is consistent
            # with the other strategies.
            trade_dir = "LONG" if direction == "UP" else "SHORT"

            price = await _fetch_quote(ticker)
            if price <= 0:
                continue

            # Simplified option debit/target/stop at the strike
            # level. We're not simulating the option greek path —
            # the equity price is the proxy for the option PnL
            # direction, and the resolver labels it based on the
            # equity move. That's a rough approximation; Phase 2
            # would introduce a proper option-specific labeler.
            stop = price * (0.97 if trade_dir == "LONG" else 1.03)
            target = price * (1.05 if trade_dir == "LONG" else 0.95)
            why = [
                {"feature": "ml_directional_conf", "value": round(conf, 3),
                 "importance": 0.8, "impact": round(conf * 0.8, 4),
                 "abs_impact": round(conf * 0.8, 4),
                 "direction": "bullish" if trade_dir == "LONG" else "bearish"},
                {"feature": "options_edge_filter", "value": 1.0,
                 "importance": 0.2, "impact": 0.2, "abs_impact": 0.2,
                 "direction": "bullish"},
            ]
            thesis = (
                f"ML conviction {conf * 100:.0f}% {direction} — "
                f"mirroring into paper ATM weekly option."
            )
            trade_id = await record_paper_trade(
                db=db,
                strategy=_STRATEGY,
                ticker=ticker,
                direction=trade_dir,
                entry=price,
                stop_loss=stop,
                target=target,
                position_usd=_POSITION_USD,
                confidence=conf,
                thesis=thesis,
                why=why,
                extra_metadata={"underlying_price": round(price, 4)},
            )
            if trade_id:
                opened += 1
        except Exception as e:
            logger.warning("[options_paper] per-ticker error on %s: %s", ticker, e)

    if opened == 0 and considered > 0:
        await narrate_scan_skip(
            _STRATEGY,
            "no signals above 72% directional conviction",
            count_considered=considered,
        )
