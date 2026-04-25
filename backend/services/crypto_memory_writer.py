"""Crypto Trade Memory Writer — isolated learning surface.

Mirror of the equity ``learning_engine_trades`` / ``features_snapshots``
pipeline, kept structurally separate so a closed crypto fill never
contaminates the equity training set (and vice versa).

Collection
----------
``crypto_trade_memory`` — one row per closed crypto trade, indexed
by ``trade_id`` (upsert), enriched with:

* ``regime`` — coarse market state at entry (parabolic, overbought,
  oversold, trend_up, trend_down, neutral). Used by the adaptation
  detector to bucket repeated failure patterns.
* ``failure_code`` — None on wins; one of ``LIQUIDITY_GAP``,
  ``PARABOLIC_EXHAUSTION``, ``EXTREME_RSI_FAILURE``, ``TREND_FAKEOUT``
  on losses. The taxonomy is what the adaptation service keys on
  when it down-weights similar setups in the future.
* ``agent_context`` — Strategist/Auditor confidences and reasons at
  the moment the trade was opened. Persists explainability after
  the underlying ``crypto_paper_trades`` doc is archived.

Architecture rule
-----------------
This module reads/writes ``crypto_trade_memory`` ONLY. It never
touches:

    * learning_engine_trades   (equity)
    * features_snapshots       (equity)
    * paper_trades             (equity)
    * model_adaptations        (equity)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def classify_crypto_regime(trade: dict[str, Any]) -> str:
    """Coarse regime taxonomy keyed off RSI + 5-bar momentum.

    Order matters — parabolic comes first because a parabolic move
    often pegs RSI to ≥70, and we want the "exhaustion-risk" label
    not the milder "overbought" one.
    """
    rsi = trade.get("rsi")
    momentum = trade.get("momentum_5b")

    if isinstance(momentum, (int, float)) and abs(momentum) >= 0.08:
        return "parabolic"

    if isinstance(rsi, (int, float)):
        if rsi >= 70:
            return "overbought"
        if rsi <= 30:
            return "oversold"

    if isinstance(momentum, (int, float)) and momentum > 0:
        return "trend_up"

    if isinstance(momentum, (int, float)) and momentum < 0:
        return "trend_down"

    return "neutral"


def classify_crypto_failure(trade: dict[str, Any]) -> str | None:
    """Categorise WHY a losing trade lost.

    Returns None on wins (``r_multiple >= 0``). Order matters —
    liquidity gaps trump everything because they invalidate any
    technical signal; parabolic exhaustion before plain RSI extreme
    because the dynamics are different (mean reversion vs.
    capitulation).
    """
    r_multiple = trade.get("r_multiple", 0)
    rsi = trade.get("rsi")
    momentum = trade.get("momentum_5b")
    volume_ratio = trade.get("volume_ratio")

    if r_multiple is None or r_multiple >= 0:
        return None

    if isinstance(volume_ratio, (int, float)) and volume_ratio < 0.7:
        return "LIQUIDITY_GAP"

    if isinstance(momentum, (int, float)) and abs(momentum) >= 0.08:
        return "PARABOLIC_EXHAUSTION"

    if isinstance(rsi, (int, float)):
        if rsi >= 70 or rsi <= 30:
            return "EXTREME_RSI_FAILURE"

    return "TREND_FAKEOUT"


async def write_crypto_trade_memory(
    db: Any,
    trade: dict[str, Any],
) -> dict[str, Any]:
    """Write a closed crypto trade into isolated crypto memory.

    No-ops gracefully when ``db`` is None or the trade isn't closed
    (so callers don't have to defensively branch). Upserts on
    ``trade_id`` so a re-close (e.g. closer retry after a transient
    crash) doesn't double-write.
    """
    if db is None:
        return {"written": False, "reason": "db_missing"}

    if trade.get("status") != "closed":
        return {"written": False, "reason": "trade_not_closed"}

    memory_doc = {
        "trade_id": trade.get("trade_id"),
        "asset_class": "crypto",
        "symbol": trade.get("symbol"),
        "pair": trade.get("pair") or f"{trade.get('symbol')}/USD",
        "direction": trade.get("direction"),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "quantity": trade.get("quantity"),
        "pnl": trade.get("pnl"),
        "r_multiple": trade.get("r_multiple"),
        "outcome": "win" if trade.get("r_multiple", 0) > 0 else "loss",

        "strategy_snapshot": {
            "rsi": trade.get("rsi"),
            "ema20": trade.get("ema20"),
            "momentum_5b": trade.get("momentum_5b"),
            "volume_ratio": trade.get("volume_ratio"),
        },

        "regime": classify_crypto_regime(trade),
        "failure_code": classify_crypto_failure(trade),

        "agent_context": {
            "strategist_conf": trade.get("strategist_conf"),
            "auditor_conf": trade.get("auditor_conf"),
            "combined_conf": trade.get("confidence"),
            "strategist_reason": trade.get("strategist_reason"),
            "auditor_reason": trade.get("auditor_reason"),
        },

        "source": "crypto_paper_bot",
        "created_at": datetime.now(timezone.utc),
        "closed_at": trade.get("closed_at"),
    }

    await db.crypto_trade_memory.update_one(
        {"trade_id": memory_doc["trade_id"]},
        {"$set": memory_doc},
        upsert=True,
    )

    return {
        "written": True,
        "trade_id": memory_doc["trade_id"],
        "failure_code": memory_doc["failure_code"],
        "regime": memory_doc["regime"],
    }
