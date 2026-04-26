"""Crypto Paper Trader — isolated 24/7 entry executor.

Pipeline per symbol
-------------------
1. ``adversarial_signal()`` — Strategist proposes, Auditor vetoes.
2. ``infer_crypto_regime`` + ``infer_failure_context`` — tag the
   signal with regime/failure metadata so the adaptation layer
   can match against active down-weight rules.
3. ``apply_crypto_adaptations_to_signal`` — runtime confidence
   haircut from any active ``crypto_model_adaptations``.
4. ``log_adversarial_decision`` — every Auditor verdict (CONFIRM,
   VETO, HOLD) hits ``crypto_signal_audit_log`` so the
   ``/api/crypto/strategist-stats`` calibration tile can compute
   the veto rate over rolling windows.
5. ``compute_crypto_position_size`` — confidence-scaled notional
   ($250 base, $1000 cap).
6. ``build_stop_take_profit`` — defensive SL/TP defaults
   (-2% / +4% LONG, +2% / -4% SHORT).
7. Insert into ``crypto_paper_trades`` ONLY.

Architecture rule
-----------------
Reads/writes ONLY ``crypto_paper_trades`` + ``crypto_signal_audit_log``
+ ``crypto_model_adaptations``. Never touches the equity ``paper_trades``
collection or ``ml_paper_trader`` / ``paper_trade_closer`` /
``price_provider.get_quote``.

The architectural firewall is enforced two ways:
* ``is_crypto_symbol()`` filter rejects non-crypto tickers BEFORE
  any quote / DB call (so AAPL passed to this bot can never land
  in the crypto collection).
* AST-verified zero forbidden imports — only ``crypto_strategist``,
  ``crypto_adaptation_service``, ``crypto_signal_audit``,
  ``crypto_memory_writer`` (for the regime classifier).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from services.crypto_strategist import adversarial_signal
from services.crypto_adaptation_service import apply_crypto_adaptations_to_signal
from services.crypto_signal_audit import log_adversarial_decision

logger = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────────────
CRYPTO_SYMBOLS = ["BTC", "ETH", "SOL"]
MIN_CRYPTO_CONFIDENCE = 0.60
BASE_CRYPTO_NOTIONAL = 250.0
MAX_CRYPTO_NOTIONAL = 1000.0


# Canonical crypto registry — symbols this bot will ever trade.
# Kept inline so the bot module is self-contained.
_CRYPTO_REGISTRY: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK",
})


def is_crypto_symbol(symbol: str | None) -> bool:
    """Tolerant crypto check — accepts ``BTC``, ``BTC/USD``, ``btc-usd``."""
    if not symbol:
        return False
    s = str(symbol).strip().upper().replace("/USD", "").replace("-USD", "")
    return s in _CRYPTO_REGISTRY


# ── Position sizing ───────────────────────────────────────────────────────────


def compute_crypto_position_size(confidence: float) -> float:
    """Confidence-scaled paper notional.

    * Below the 0.60 floor → 0 (no fill).
    * 0.60 → $250 (base).
    * 0.95 → ~$396 (linear within the [0.60, 0.95] band).
    * Capped at ``MAX_CRYPTO_NOTIONAL`` so even a perfect-storm
      signal can't blow out the paper portfolio.
    """
    if confidence < MIN_CRYPTO_CONFIDENCE:
        return 0.0

    multiplier = min(max(confidence, 0.6), 0.95)
    size = BASE_CRYPTO_NOTIONAL * (multiplier / 0.60)
    return round(min(size, MAX_CRYPTO_NOTIONAL), 2)


def build_stop_take_profit(entry: float, direction: str) -> dict[str, float]:
    """Defensive symmetric SL/TP defaults.

    LONG: -2% stop, +4% target → 2:1 reward:risk.
    SHORT: +2% stop, -4% target → 2:1 reward:risk.
    """
    if direction.upper() == "SHORT":
        return {
            "stop_loss": round(entry * 1.02, 2),
            "take_profit": round(entry * 0.96, 2),
        }
    return {
        "stop_loss": round(entry * 0.98, 2),
        "take_profit": round(entry * 1.04, 2),
    }


# ── Regime + failure-context taggers ──────────────────────────────────────────


def infer_crypto_regime(signal: dict[str, Any]) -> str:
    """Tag the signal with regime so the adaptation layer can match
    against ``adaptation.regime`` keys."""
    indicators = signal.get("strategist", {}).get("indicators", {})
    rsi = indicators.get("rsi")
    mom = indicators.get("momentum_5b")

    if isinstance(mom, (int, float)) and abs(mom) >= 0.08:
        return "parabolic"
    if isinstance(rsi, (int, float)) and rsi >= 70:
        return "overbought"
    if isinstance(rsi, (int, float)) and rsi <= 30:
        return "oversold"
    if signal.get("direction") == "LONG":
        return "trend_up"
    if signal.get("direction") == "SHORT":
        return "trend_down"
    return "neutral"


def infer_failure_context(signal: dict[str, Any]) -> dict[str, Any]:
    """Pre-tag the LIKELY failure code for this signal so the
    adaptation layer can match on either ``regime`` OR
    ``failure_context.likely_failure_code``."""
    indicators = signal.get("strategist", {}).get("indicators", {})
    rsi = indicators.get("rsi")
    mom = indicators.get("momentum_5b")

    likely = None
    if isinstance(mom, (int, float)) and abs(mom) >= 0.08:
        likely = "PARABOLIC_EXHAUSTION"
    elif isinstance(rsi, (int, float)) and (rsi >= 70 or rsi <= 30):
        likely = "EXTREME_RSI_FAILURE"

    return {"likely_failure_code": likely}


# ── Per-symbol pipeline ───────────────────────────────────────────────────────


QuoteProvider = Callable[[str], Awaitable[dict]]
HistoryProvider = Callable[[str], Awaitable[list[float]]]


async def run_crypto_symbol(
    db: Any,
    symbol: str,
    bars: list[float],
    quote_provider: QuoteProvider,
) -> dict[str, Any]:
    """Run one crypto symbol through the full pipeline.

    Returns either ``{"opened": True, ...}`` on a fill or
    ``{"skipped": True, "reason": ...}`` on any halt. Never raises
    out — the multi-symbol runner above catches and converts to
    ``errors`` entries.
    """
    if db is None:
        return {"symbol": symbol, "skipped": True, "reason": "db_missing"}

    # Architectural firewall — never let a non-crypto symbol land
    # in crypto_paper_trades.
    if not is_crypto_symbol(symbol):
        return {"symbol": symbol, "skipped": True, "reason": "not_crypto_symbol"}

    if not bars or len(bars) < 30:
        return {"symbol": symbol, "skipped": True, "reason": "insufficient_bars"}

    raw_signal = adversarial_signal(bars)

    # Tag regime + failure_context on the proposal so the
    # adaptation layer below can match either key.
    signal = {
        **raw_signal,
        "symbol": symbol,
        "regime": infer_crypto_regime(raw_signal),
        "failure_context": infer_failure_context(raw_signal),
    }

    # Audit log fires on EVERY decision (including HOLDs) so the
    # veto-rate calibration tile sees the full denominator.
    if raw_signal["direction"] == "HOLD":
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {
            "symbol": symbol,
            "skipped": True,
            "reason": raw_signal.get("reason"),
            "signal": raw_signal,
        }

    signal = await apply_crypto_adaptations_to_signal(db, signal)

    if signal["direction"] == "HOLD":
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {
            "symbol": symbol,
            "skipped": True,
            "reason": signal.get("reason"),
            "signal": signal,
        }

    # Live quote (crypto-only path — never touches equity get_quote).
    quote = await quote_provider(symbol)
    entry_price = float((quote or {}).get("price") or 0.0)
    if entry_price <= 0:
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {"symbol": symbol, "skipped": True, "reason": "quote_unavailable"}

    size_usd = compute_crypto_position_size(float(signal["confidence"]))
    if size_usd <= 0:
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {"symbol": symbol, "skipped": True, "reason": "size_zero"}

    quantity = round(size_usd / entry_price, 8)
    stops = build_stop_take_profit(entry_price, signal["direction"])

    strategist = signal.get("strategist", {}) or {}
    auditor = signal.get("auditor", {}) or {}
    indicators = strategist.get("indicators", {}) or {}

    now = datetime.now(timezone.utc)

    trade = {
        "trade_id": str(uuid4()),
        "asset_class": "crypto",
        "symbol": symbol,
        "pair": f"{symbol}/USD",
        "direction": signal["direction"],
        "confidence": float(signal["confidence"]),
        "entry_price": entry_price,
        "quantity": quantity,
        "size_usd": size_usd,
        "status": "open",
        "opened_at": now,
        # ``opened_day`` (UTC date string) — backs the
        # /api/crypto/tier3-contribution distinct-day count without
        # needing a date-trunc aggregation pipeline.
        "opened_day": now.date().isoformat(),
        "source": "crypto_paper_bot",

        "stop_loss": stops["stop_loss"],
        "take_profit": stops["take_profit"],

        # Top-level snapshot fields — denormalised so the closer +
        # memory writer + adaptation queries don't need nested-doc
        # paths.
        "rsi": indicators.get("rsi"),
        "ema20": indicators.get("ema20"),
        "momentum_5b": indicators.get("momentum_5b"),
        "volume_ratio": signal.get("volume_ratio"),
        "regime": signal.get("regime"),

        "strategist_conf": strategist.get("confidence"),
        "auditor_conf": auditor.get("confidence"),
        "strategist_reason": strategist.get("reason"),
        "auditor_reason": auditor.get("reason"),

        "crypto_adaptations_applied": signal.get(
            "crypto_adaptations_applied", []
        ),

        # agent_agreement block — top-level for direct admin queries
        # without nested lookup.
        "agent_agreement": {
            "strategist_direction": strategist.get("direction"),
            "strategist_confidence": strategist.get("confidence"),
            "auditor_verdict": auditor.get("verdict"),
            "auditor_confidence": auditor.get("confidence"),
            "combined_confidence": signal.get("confidence"),
        },

        "metadata": {
            "lane": "crypto",
            "bot_version": "crypto_v3",
            "agent_agreement": {
                "strategist_direction": strategist.get("direction"),
                "auditor_verdict": auditor.get("verdict"),
                "combined_confidence": signal.get("confidence"),
            },
        },
    }

    try:
        await db.crypto_paper_trades.insert_one(trade)
    except Exception as exc:  # noqa: BLE001
        logger.error("[crypto-bot] insert failed for %s: %s", symbol, exc)
        await log_adversarial_decision(
            db, symbol=symbol, signal=signal, final_direction="HOLD",
        )
        return {"symbol": symbol, "skipped": True, "reason": "db_write_failed"}

    await log_adversarial_decision(
        db, symbol=symbol, signal=signal, final_direction=signal["direction"],
    )

    logger.info(
        "[crypto-bot] opened %s %s qty=%.8f @ $%.2f size=$%.2f conf=%.3f",
        symbol, signal["direction"], quantity, entry_price, size_usd,
        signal["confidence"],
    )

    return {
        "symbol": symbol,
        "opened": True,
        "trade_id": trade["trade_id"],
        "direction": trade["direction"],
        "confidence": trade["confidence"],
        "size_usd": trade["size_usd"],
        "entry_price": entry_price,
        "stop_loss": stops["stop_loss"],
        "take_profit": stops["take_profit"],
    }


async def run_crypto_paper_bot(
    db: Any,
    quote_provider: QuoteProvider,
    history_provider: HistoryProvider,
    symbols: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Multi-symbol crypto paper bot runner.

    One pass through ``symbols`` (default ``["BTC", "ETH", "SOL"]``).
    Each symbol failure is captured into ``errors`` so the scheduler
    tick never crashes on a single-symbol fault.
    """
    syms = symbols or CRYPTO_SYMBOLS

    opened: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for symbol in syms:
        try:
            bars = await history_provider(symbol)
            result = await run_crypto_symbol(
                db=db,
                symbol=symbol,
                bars=bars,
                quote_provider=quote_provider,
            )
            if result.get("opened"):
                opened.append(result)
            else:
                skipped.append(result)
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "symbol": symbol,
                "error": str(exc),
                "type": type(exc).__name__,
            })

    return {
        "opened": opened,
        "skipped": skipped,
        "errors": errors,
        "opened_count": len(opened),
        "skipped_count": len(skipped),
        "error_count": len(errors),
    }
