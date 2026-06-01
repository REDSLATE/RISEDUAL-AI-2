"""Crypto live executor — Kraken-backed real-money execution.

Doctrine (operator directive 2026-06-01)
----------------------------------------
This module is the **only** path that hits Kraken for real fills.
Everything routed through here writes to ``crypto_live_trades`` —
NEVER ``crypto_paper_trades``. Hard collection separation is the
only safe move: every downstream stat (shadow scorer, PnL
dashboards, calibration, federation memory) reads from the paper
collection, and if we mixed live rows in we'd silently corrupt
months of evidence.

Default OFF
-----------
The wire only fires when ``RISEDUAL_CRYPTO_LIVE_EXEC=1`` is set.
Anything else (unset, ``0``, ``false``) → :func:`maybe_route_live`
returns False and the caller falls back to the simulated paper
path. This is the operator's master switch; revert is one env
change away.

Hard caps (NOT env-configurable for the first deploy)
-----------------------------------------------------
  * Notional ≤ $25 / trade  (override via RISEDUAL_CRYPTO_LIVE_NOTIONAL_USD)
  * LONG only — no shorts, no margin
  * BTC + ETH only — extend allowlist as confidence grows
  * Max 3 concurrent open live positions
  * Max 10 live trades / 24h

A request that fails ANY cap falls back to paper silently (with a
warning log). We do NOT raise — the caller's existing paper path
must continue to fire so Alpha keeps learning even when live is
saturated or disabled.

Order shape sent to Kraken
--------------------------
``KrakenTradingService.place_order(pair, qty, "buy", "market")``
which translates to a ``/0/private/AddOrder`` POST with HMAC-SHA512
auth signed by ``KRAKEN_API_KEY`` / ``KRAKEN_API_SECRET``. Market
orders fill against best bid/ask. No stop / no limit / no margin.

Trade row written
-----------------
Mirrors ``crypto_paper_trades`` schema so the closer + dashboards
can read both collections symmetrically. Adds:

  * ``is_live = True``                — discriminator vs paper
  * ``kraken_order_id``               — broker fill ID for reconcile
  * ``kraken_pair``                   — exchange-format pair sent
  * ``live_notional_usd``             — actual notional after cap
  * ``live_safety_caps``              — snapshot of caps at fill time
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Hard caps — see module docstring ───────────────────────────────────


# Symbol allowlist. Kept inside the module so operator extending the
# allowlist requires a code review, not just an env flip.
LIVE_SYMBOL_ALLOWLIST: frozenset[str] = frozenset({"BTC", "ETH"})

# Default $25 notional; env overrides for operator who wants to bump
# without redeploy. Min floor $5 protects against fat-finger zeros.
_DEFAULT_NOTIONAL_USD: float = 25.0
_MIN_NOTIONAL_USD: float = 5.0

MAX_OPEN_LIVE_POSITIONS: int = 3
MAX_DAILY_LIVE_TRADES: int = 10

# Hard stop-loss percentage from entry price (operator directive
# 2026-06-01: "SL needs to be in place. No more than 3%."). Stop
# leg placed immediately after the market buy fills. If the SL leg
# fails to place, the live row is still written but flagged
# ``stop_loss_placed=False`` so the closer + operator dashboard
# surface the orphan-protection condition loudly.
HARD_STOP_LOSS_PCT: float = 0.03

# Hard take-profit percentage from entry price (operator directive
# 2026-06-01: "TP at 4%+ or greater"). Limit-SELL leg placed
# immediately after the market buy fills, alongside the SL. If the
# TP leg fails to place, the live row is still written but flagged
# ``take_profit_placed=False`` — the SL still protects downside;
# TP failure only means the position rides naked on the upside
# until the operator attaches one manually.
#
# NOTE on order coexistence: Kraken does NOT auto-link SL + TP as
# an OCO pair on the AddOrder endpoint. Both orders are live on
# the book simultaneously. When ONE fires, the other becomes an
# orphan that will fire if price ever revisits its trigger — at
# which point we'd be net SHORT the position by accident. The
# closer/reconcile loop (follow-up task) must cancel the orphan
# the moment a position-closing fill is detected. Until that
# closer is built, the operator MUST manually cancel the orphan
# in the Kraken UI after either leg fills.
HARD_TAKE_PROFIT_PCT: float = 0.04

# Internal-to-Kraken symbol map. Kraken uses legacy "XBT" prefix for
# Bitcoin in the AddOrder API; if we ever extend the allowlist past
# BTC/ETH this dict must be extended in lockstep.
_KRAKEN_PAIR: dict[str, str] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
}


# ── Entry-eligibility gates ────────────────────────────────────────────


def _live_exec_enabled() -> bool:
    """Master switch. ``RISEDUAL_CRYPTO_LIVE_EXEC=1`` arms the wire."""
    return os.environ.get("RISEDUAL_CRYPTO_LIVE_EXEC", "").strip() in (
        "1", "true", "True", "yes", "on",
    )


def _resolve_notional_usd() -> float:
    """Notional per live trade. Operator-overridable via env, floored
    at $5 and capped at $1,000 (defense-in-depth against a fat-finger
    env-var override like ``25000``)."""
    raw = os.environ.get("RISEDUAL_CRYPTO_LIVE_NOTIONAL_USD")
    if not raw:
        return _DEFAULT_NOTIONAL_USD
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_NOTIONAL_USD
    return max(_MIN_NOTIONAL_USD, min(1000.0, v))


def _normalize_symbol(symbol: str) -> str:
    """Strip /USD, /USDT, -USD suffix variants → bare base ticker."""
    s = (symbol or "").upper().strip()
    for suffix in ("/USDT", "/USDC", "/USD", "-USD", "USDT", "USDC"):
        if s.endswith(suffix):
            return s.removesuffix(suffix)
    return s


def kraken_pair(symbol: str) -> Optional[str]:
    """Map Alpha-internal symbol (``BTC``, ``BTC/USD``) to Kraken's
    AddOrder pair format. Returns None for unsupported symbols —
    caller must fall back to paper."""
    return _KRAKEN_PAIR.get(_normalize_symbol(symbol))


async def is_eligible_for_live(
    db: Any, symbol: str, direction: str,
) -> tuple[bool, str]:
    """Single source of truth for live-routability.

    Returns ``(eligible, reason)``. Reason is a short kebab-case
    code on rejection (``"live_disabled"``, ``"short_blocked"`` …)
    so callers can log + count without needing free-text parsing.
    """
    if not _live_exec_enabled():
        return False, "live_disabled"

    # Direction tokens routed through the canonical helper per
    # the no-local-direction-tuples doctrine — accepts BUY/LONG/UP
    # /BULLISH/STRONG_BUY/WEAK_BUY all of which normalize to "LONG".
    from services.prediction_tracker import canonical_ai_dir
    if canonical_ai_dir(direction) != "LONG":
        return False, "short_blocked"

    base = _normalize_symbol(symbol)
    if base not in LIVE_SYMBOL_ALLOWLIST:
        return False, "symbol_not_allowlisted"
    if kraken_pair(symbol) is None:
        return False, "kraken_pair_unmapped"

    # Capacity caps — read from crypto_live_trades only.
    try:
        open_count = await db.crypto_live_trades.count_documents(
            {"status": "open"},
        )
        if open_count >= MAX_OPEN_LIVE_POSITIONS:
            return False, "open_position_cap_reached"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live] open-count probe failed: %s", exc)
        return False, "capacity_probe_failed"

    cutoff_24h = (
        datetime.now(timezone.utc) - timedelta(hours=24)
    ).replace(tzinfo=None)
    try:
        daily_count = await db.crypto_live_trades.count_documents(
            {"opened_at": {"$gte": cutoff_24h}},
        )
        if daily_count >= MAX_DAILY_LIVE_TRADES:
            return False, "daily_trade_cap_reached"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live] daily-count probe failed: %s", exc)
        return False, "capacity_probe_failed"

    return True, "ok"


# ── Order placement ────────────────────────────────────────────────────


def _kraken_client():
    """Lazy-built Kraken broker. Returns None when keys missing —
    caller must fall back to paper. Imported inside the function so
    the module imports cleanly in test environments without keys."""
    api_key = os.environ.get("KRAKEN_API_KEY", "").strip()
    api_secret = os.environ.get("KRAKEN_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return None
    try:
        from services.broker_service import KrakenTradingService
        return KrakenTradingService(api_key=api_key, api_secret=api_secret)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live] kraken client init failed: %s", exc)
        return None


def place_live_market_buy(
    symbol: str, notional_usd: float, ref_price: float,
) -> Optional[dict[str, Any]]:
    """Submit a Kraken market BUY for ``notional_usd`` worth of
    ``symbol`` at approximately ``ref_price``, then immediately
    place a hard stop-loss SELL at ``ref_price × (1 - HARD_STOP_LOSS_PCT)``.

    Returns ``{kraken_order_id, kraken_pair, qty, notional_usd,
    stop_loss_price, stop_loss_order_id, stop_loss_placed}`` on
    success or ``None`` if the BUY itself failed. **Note:** if the
    BUY succeeds but the SL placement fails, this function still
    returns a populated dict with ``stop_loss_placed=False`` —
    the operator must manually attach a stop to avoid open-ended
    downside. The caller flags this loudly in the live trade row.

    qty computed from ``notional_usd / ref_price``. Kraken sizes its
    minimums at 0.0001 BTC / 0.005 ETH so $25 at typical prices
    clears comfortably; this function does NOT enforce min-volume
    — that's Kraken's job and returning their error verbatim is more
    informative than us pre-rejecting.
    """
    if ref_price <= 0:
        logger.warning("[crypto-live] refusing zero/neg ref_price: %s", ref_price)
        return None
    pair = kraken_pair(symbol)
    if pair is None:
        return None
    client = _kraken_client()
    if client is None:
        return None

    qty = round(notional_usd / ref_price, 8)
    try:
        resp = client.place_order(
            symbol=pair, qty=qty, side="buy", order_type="market",
        )
        if not resp:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.error("[crypto-live] BUY place_order raised: %s", exc)
        return None

    fill = {
        "kraken_order_id": resp.get("id") or "",
        "kraken_pair": pair,
        "qty": qty,
        "notional_usd": round(qty * ref_price, 4),
        "stop_loss_price": 0.0,
        "stop_loss_order_id": "",
        "stop_loss_placed": False,
        "take_profit_price": 0.0,
        "take_profit_order_id": "",
        "take_profit_placed": False,
    }

    # ── Hard stop-loss leg ────────────────────────────────────────
    # Operator directive 2026-06-01: every live position MUST have
    # a -3% stop attached. We place it immediately after the BUY
    # fills. Kraken's "stop-loss" ordertype fires a market SELL
    # when the last-traded price <= the trigger price.
    sl_trigger = round(ref_price * (1.0 - HARD_STOP_LOSS_PCT), 2)
    try:
        sl_resp = client.place_order(
            symbol=pair,
            qty=qty,
            side="sell",
            order_type="stop-loss",
            stop_price=sl_trigger,
        )
        if sl_resp and sl_resp.get("id"):
            fill["stop_loss_price"] = sl_trigger
            fill["stop_loss_order_id"] = sl_resp.get("id") or ""
            fill["stop_loss_placed"] = True
            logger.info(
                "[crypto-live] SL placed @ $%.2f (-%.1f%%) order_id=%s",
                sl_trigger, HARD_STOP_LOSS_PCT * 100.0,
                sl_resp.get("id"),
            )
        else:
            logger.error(
                "[crypto-live] CRITICAL — BUY filled but SL placement "
                "returned empty. fill=%s trigger=$%.2f — OPERATOR MUST "
                "MANUALLY ATTACH STOP",
                fill["kraken_order_id"], sl_trigger,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[crypto-live] CRITICAL — BUY filled but SL placement "
            "RAISED: %s. fill=%s trigger=$%.2f — OPERATOR MUST "
            "MANUALLY ATTACH STOP",
            exc, fill["kraken_order_id"], sl_trigger,
        )

    # ── Hard take-profit leg ──────────────────────────────────────
    # Operator directive 2026-06-01: TP at 4%+. We place a limit
    # SELL at entry × 1.04. The "+ or greater" framing is honoured
    # because a limit order at $X fills at >= $X (price-time priority
    # at or above the limit) — partial fills above 4% are explicitly
    # acceptable. Independent of the SL leg: failure to place TP does
    # NOT undo a successful SL (downside is still protected).
    tp_target = round(ref_price * (1.0 + HARD_TAKE_PROFIT_PCT), 2)
    try:
        tp_resp = client.place_order(
            symbol=pair,
            qty=qty,
            side="sell",
            order_type="limit",
            limit_price=tp_target,
        )
        if tp_resp and tp_resp.get("id"):
            fill["take_profit_price"] = tp_target
            fill["take_profit_order_id"] = tp_resp.get("id") or ""
            fill["take_profit_placed"] = True
            logger.info(
                "[crypto-live] TP placed @ $%.2f (+%.1f%%) order_id=%s",
                tp_target, HARD_TAKE_PROFIT_PCT * 100.0,
                tp_resp.get("id"),
            )
        else:
            logger.error(
                "[crypto-live] TP placement returned empty. "
                "fill=%s target=$%.2f — upside ride is open-ended "
                "until operator attaches TP manually",
                fill["kraken_order_id"], tp_target,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[crypto-live] TP placement RAISED: %s. fill=%s "
            "target=$%.2f — upside ride is open-ended until "
            "operator attaches TP manually",
            exc, fill["kraken_order_id"], tp_target,
        )

    return fill


# ── Caller-facing entry: try-live-else-fall-through ────────────────────


async def maybe_route_live(
    db: Any, trade: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Attempt to route ``trade`` to Kraken live. Returns the
    persisted live-trade row on success, or None on any rejection
    (caller MUST then fall back to the paper path).

    Argument is the in-memory trade dict the paper trader has
    assembled — same shape that would be inserted into
    ``crypto_paper_trades``. We never mutate the input.

    Doctrine: this is a hard binary. The caller does
    ``maybe_route_live(...)`` and either gets a live row back (which
    has already been persisted to ``crypto_live_trades``) or None
    (route paper instead). No fallthrough where SOMETIMES the order
    fires but the row goes to paper — that would be a Tier-3 firewall
    breach equivalent.
    """
    symbol = str(trade.get("symbol") or "")
    direction = str(trade.get("direction") or "")
    eligible, reason = await is_eligible_for_live(db, symbol, direction)
    if not eligible:
        if reason != "live_disabled":
            # Only log when caps are saturating — silent under
            # the default-off state, otherwise noise floods logs.
            logger.info(
                "[crypto-live] not eligible symbol=%s direction=%s reason=%s",
                symbol, direction, reason,
            )
        return None

    ref_price = float(trade.get("entry_price") or 0.0)
    notional = _resolve_notional_usd()

    placed = place_live_market_buy(symbol, notional, ref_price)
    if placed is None:
        # Kraken rejected (min-vol, insufficient funds, network).
        # Caller falls back to paper. We log loudly so the operator
        # sees the rejection in real time.
        logger.error(
            "[crypto-live] order rejected by Kraken — symbol=%s notional=$%.2f ref=$%.2f",
            symbol, notional, ref_price,
        )
        return None

    # Compose the live-collection row. Same shape as the paper row
    # the caller built so closer / dashboards can read symmetrically,
    # plus the discriminator + broker IDs.
    live_row = dict(trade)
    live_row["is_live"] = True
    live_row["kraken_order_id"] = placed["kraken_order_id"]
    live_row["kraken_pair"] = placed["kraken_pair"]
    live_row["live_notional_usd"] = placed["notional_usd"]
    live_row["size_usd"] = placed["notional_usd"]
    live_row["size"] = placed["qty"]
    # Hard stop-loss + take-profit legs (operator-mandated 3%/4% caps).
    # The closer reads these fields to know which legs are broker-side.
    live_row["stop_loss_pct"] = HARD_STOP_LOSS_PCT
    live_row["stop_loss_price"] = placed.get("stop_loss_price", 0.0)
    live_row["stop_loss_order_id"] = placed.get("stop_loss_order_id", "")
    live_row["stop_loss_placed"] = placed.get("stop_loss_placed", False)
    live_row["take_profit_pct"] = HARD_TAKE_PROFIT_PCT
    live_row["take_profit_price"] = placed.get("take_profit_price", 0.0)
    live_row["take_profit_order_id"] = placed.get("take_profit_order_id", "")
    live_row["take_profit_placed"] = placed.get("take_profit_placed", False)
    live_row["live_safety_caps"] = {
        "notional_cap_usd": notional,
        "stop_loss_pct": HARD_STOP_LOSS_PCT,
        "take_profit_pct": HARD_TAKE_PROFIT_PCT,
        "open_position_cap": MAX_OPEN_LIVE_POSITIONS,
        "daily_trade_cap": MAX_DAILY_LIVE_TRADES,
        "symbol_allowlist": sorted(LIVE_SYMBOL_ALLOWLIST),
    }

    try:
        await db.crypto_live_trades.insert_one(live_row)
        live_row.pop("_id", None)
    except Exception as exc:  # noqa: BLE001
        # If the broker fill succeeded but Mongo insert failed, we
        # have an orphan Kraken position. Log loudly and return None
        # so the paper path also fires (creating a paper twin that
        # the operator can use to reconcile manually).
        logger.error(
            "[crypto-live] CRITICAL — Kraken fill SUCCEEDED but Mongo "
            "insert FAILED. orphan_order_id=%s symbol=%s: %s",
            placed["kraken_order_id"], symbol, exc,
        )
        return None

    sl_state = "SL @ $%.2f" % live_row["stop_loss_price"] if live_row["stop_loss_placed"] else "NO SL (MANUAL!)"
    tp_state = "TP @ $%.2f" % live_row["take_profit_price"] if live_row["take_profit_placed"] else "no TP"
    logger.info(
        "[crypto-live] LIVE FILL — %s LONG qty=%.8f notional=$%.2f "
        "kraken_order_id=%s %s %s",
        symbol, placed["qty"], placed["notional_usd"],
        placed["kraken_order_id"], sl_state, tp_state,
    )
    return live_row


__all__ = [
    "LIVE_SYMBOL_ALLOWLIST",
    "MAX_OPEN_LIVE_POSITIONS",
    "MAX_DAILY_LIVE_TRADES",
    "kraken_pair",
    "is_eligible_for_live",
    "place_live_market_buy",
    "maybe_route_live",
]
