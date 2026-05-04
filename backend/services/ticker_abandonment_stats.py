"""
Ticker Abandonment — input-aggregation helpers.

Pulls the rolling-window inputs (``recent_signals``,
``recent_rejections``, ``recent_wins``, ``recent_losses``,
``avg_confidence``, ``avg_rr``, ``last_profitable_at``) from
existing Mongo collections so the pure decision function in
``services.ticker_abandonment.decide_ticker_exit`` has real data
to evaluate.

Two flavours
────────────
* :func:`compute_equity_inputs` — reads ``agent_activity`` (for
  signals + rejections) and ``paper_trades`` (for wins/losses/
  RR/last-profitable).
* :func:`compute_crypto_inputs` — reads ``agent_activity``
  filtered by ``lane="crypto"`` if present, plus
  ``crypto_paper_trades``. Falls back to symbol-only filtering
  when the lane discriminator isn't on the rows.

Both helpers degrade gracefully — if Mongo reads fail, they
return zero-filled inputs which the gate maps to KEEP
("not_enough_recent_signals"). A read outage must not slam the
gate shut on every ticker.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _i(env_key: str, default: int) -> int:
    try:
        return int(os.environ.get(env_key, default))
    except (TypeError, ValueError):
        return default


# Window for "recent" — operator-tunable. 30 days is the same window
# Tier 3 readiness uses, so the two views agree.
RECENT_WINDOW_DAYS: int = _i("TICKER_ABANDONMENT_WINDOW_DAYS", 30)


@dataclass(frozen=True)
class TickerExitInputs:
    symbol: str
    recent_signals: int
    recent_rejections: int
    recent_wins: int
    recent_losses: int
    avg_confidence: float
    avg_rr: float
    last_profitable_at: datetime | None
    window_days: int


_EMPTY_INPUTS = TickerExitInputs(
    symbol="",
    recent_signals=0,
    recent_rejections=0,
    recent_wins=0,
    recent_losses=0,
    avg_confidence=0.0,
    avg_rr=0.0,
    last_profitable_at=None,
    window_days=RECENT_WINDOW_DAYS,
)


def _empty_for(symbol: str) -> TickerExitInputs:
    """Zero-filled inputs for the gate's KEEP fallback path."""
    return TickerExitInputs(
        symbol=symbol,
        recent_signals=0,
        recent_rejections=0,
        recent_wins=0,
        recent_losses=0,
        avg_confidence=0.0,
        avg_rr=0.0,
        last_profitable_at=None,
        window_days=RECENT_WINDOW_DAYS,
    )


async def _activity_counts(
    db: Any, symbol: str, since: datetime,
) -> tuple[int, int, float]:
    """Returns ``(signals, rejections, avg_conf)`` from agent_activity.

    ``signals`` = paper_trade_open + paper_trade_skip events.
    ``rejections`` = paper_trade_skip events.
    ``avg_conf`` = mean of confidence across both event types where
    a confidence value is present (in metadata.confidence).
    """
    try:
        cursor = db["agent_activity"].find(
            {
                "symbol": symbol.upper(),
                "type": {"$in": ["paper_trade_open", "paper_trade_skip"]},
                "created_at": {"$gte": since},
            },
            {
                "_id": 0, "type": 1, "metadata.confidence": 1,
                "created_at": 1,
            },
        ).limit(2_000)
        rows = await cursor.to_list(length=2_000)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ticker-abandon] activity read failed for %s: %s", symbol, exc)
        return 0, 0, 0.0

    signals = len(rows)
    rejections = sum(1 for r in rows if r.get("type") == "paper_trade_skip")
    confs = []
    for r in rows:
        c = (r.get("metadata") or {}).get("confidence")
        if c is None:
            continue
        try:
            cv = float(c)
        except (TypeError, ValueError):
            continue
        # Normalise to 0-1 (event metadata may carry either scale).
        if cv > 1.01:
            cv = cv / 100.0
        if 0.0 <= cv <= 1.0:
            confs.append(cv)
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    return signals, rejections, avg_conf


async def _trade_outcomes(
    db: Any, *, collection: str, symbol_field: str, symbol: str,
    since: datetime,
) -> tuple[int, int, float, datetime | None]:
    """Returns ``(wins, losses, avg_rr, last_profitable_at)`` from a
    paper-trade collection.

    Win/loss attribution uses the row's ``outcome`` field
    ("win"/"loss") which both equity ``paper_trades`` and crypto
    ``crypto_paper_trades`` populate via the closer.
    """
    try:
        # Use opened_at as the window anchor so we count every trade
        # opened in the last N days regardless of close state. Open
        # trades contribute to neither win nor loss until closed.
        cursor = db[collection].find(
            {
                symbol_field: symbol.upper() if symbol_field == "ticker" else symbol,
                "opened_at": {"$gte": since},
            },
            {
                "_id": 0, "outcome": 1, "pnl_pct": 1, "r_multiple": 1,
                "closed_at": 1,
            },
        ).limit(2_000)
        rows = await cursor.to_list(length=2_000)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ticker-abandon] %s read failed for %s: %s",
            collection, symbol, exc,
        )
        return 0, 0, 0.0, None

    wins = 0
    losses = 0
    rrs: list[float] = []
    last_profit: datetime | None = None
    for r in rows:
        outcome = r.get("outcome")
        if outcome == "win":
            wins += 1
            ca = r.get("closed_at")
            if isinstance(ca, datetime):
                if last_profit is None or ca > last_profit:
                    last_profit = ca
        elif outcome == "loss":
            losses += 1
        rr = r.get("r_multiple")
        if rr is None:
            # Fallback — if r_multiple isn't stamped, derive a coarse
            # proxy from pnl_pct. The decision function only needs
            # avg_rr to compare against 1.2× so directional sign
            # matters more than absolute scale.
            pp = r.get("pnl_pct")
            if pp is not None:
                try:
                    rr = float(pp)
                except (TypeError, ValueError):
                    rr = None
        if rr is not None:
            try:
                rrs.append(float(rr))
            except (TypeError, ValueError):
                pass
    avg_rr = sum(rrs) / len(rrs) if rrs else 0.0
    return wins, losses, avg_rr, last_profit


async def compute_equity_inputs(
    db: Any, symbol: str, *, window_days: int | None = None,
) -> TickerExitInputs:
    """Aggregate equity ticker_abandonment inputs from Mongo."""
    if db is None or not symbol:
        return _empty_for(symbol)

    days = window_days or RECENT_WINDOW_DAYS
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sym = symbol.upper()

    signals, rejections, avg_conf = await _activity_counts(db, sym, since)
    wins, losses, avg_rr, last_profit = await _trade_outcomes(
        db, collection="paper_trades", symbol_field="ticker",
        symbol=sym, since=since,
    )
    return TickerExitInputs(
        symbol=sym,
        recent_signals=signals,
        recent_rejections=rejections,
        recent_wins=wins,
        recent_losses=losses,
        avg_confidence=avg_conf,
        avg_rr=avg_rr,
        last_profitable_at=last_profit,
        window_days=days,
    )


async def compute_crypto_inputs(
    db: Any, symbol: str, *, window_days: int | None = None,
) -> TickerExitInputs:
    """Aggregate crypto ticker_abandonment inputs from Mongo."""
    if db is None or not symbol:
        return _empty_for(symbol)

    days = window_days or RECENT_WINDOW_DAYS
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sym = symbol.upper()

    signals, rejections, avg_conf = await _activity_counts(db, sym, since)
    wins, losses, avg_rr, last_profit = await _trade_outcomes(
        db, collection="crypto_paper_trades", symbol_field="symbol",
        symbol=sym, since=since,
    )
    return TickerExitInputs(
        symbol=sym,
        recent_signals=signals,
        recent_rejections=rejections,
        recent_wins=wins,
        recent_losses=losses,
        avg_confidence=avg_conf,
        avg_rr=avg_rr,
        last_profitable_at=last_profit,
        window_days=days,
    )
