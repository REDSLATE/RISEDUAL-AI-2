"""Operator Watchlist — parse free-form research text into pick candidates
and store them in Mongo. Watchlist entries express **operator interest**;
they never inject synthetic signals. When Alpha runs its normal scan,
watchlist symbols with a live signal_dispatcher prediction surface with
an ``operator_watched=True`` tag; watchlist symbols without a prediction
show up as a **coverage gap** on the admin panel so ops can see which
picks Alpha hasn't scored yet.

Doctrine:
* No synthetic prediction is ever written from here.
* No confidence override.
* Regime + gate stack apply unchanged.
* Watchlist is purely a lens over Alpha's own predictions.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

WATCHLIST_COLLECTION = "operator_watchlist"

# HARD stoplist — tokens that are never tickers, even with $prices nearby.
_TICKER_STOPLIST: set[str] = {
    "TL", "TLDR", "TIL", "USD", "AM", "PM", "ET", "CT", "PT", "UTC",
    "WATCH", "ONLY", "PIPE", "IPO", "NYSE", "NASDAQ", "SPY", "QQQ", "IWM",
    "DJI", "DJIA", "OK", "TBD", "NA", "USA", "US", "UK", "EU",
    "GDP", "CPI", "PPI", "FED", "FOMC", "SEC", "IRS", "CEO", "CFO",
    "COO", "CTO", "CEOS", "IRA", "ETF", "OTC", "PE", "PS", "EPS",
    "AI", "ML", "AR", "VR", "AAA", "BBB",
    "EUR", "GBP", "JPY", "CNY", "BTC", "ETH", "XRP", "SOL", "ADA", "DOGE",
    "HIGH", "LOW", "OPEN", "CLOSE", "BUY", "SELL", "HOLD", "LONG",
    "SHORT", "BID", "ASK", "VOL", "AVG", "MAX", "MIN", "NEW", "OLD",
    "MC", "FYI", "ASAP", "EOD", "SOD", "RANGE", "FLOAT", "VOLUME",
    "MISSING", "QUAL", "QUALS", "TRIGGER", "TRIGGERS", "RECOVERY", "STOP",
    "ENTRY", "EXIT", "PLAY", "BUZZ", "BASE", "REBOUND", "CHASING",
    "SESSION", "PIVOT", "REJECT", "REJECTS", "SIGNAL", "SETUP",
    "UP", "DOWN", "OFF", "ON", "OR", "AND", "THE", "FOR", "TO", "AT",
    "OF", "IS", "IT", "BE", "AS", "IF", "IN", "SO", "NO", "GO", "DO",
}
# Two-letter English words are aggressively excluded above ("BE", "IS",
# "IT", "TO", ...). If a two-letter real ticker like ``BE`` needs to be
# accepted, use the smart-paste with an explicit `$price` on the same
# line — the parser only readmits a stoplisted token if a nearby price
# strongly suggests intent AND the token is on the ``SOFT_ALLOWLIST``.
_SOFT_ALLOWLIST: set[str] = {"BE", "MU", "GE", "GM", "F", "T", "V", "C"}


_PRICE_RX = re.compile(r"\$\d{1,4}(?:\.\d{1,4})?")
_TICKER_RX = re.compile(r"\b[A-Z]{2,5}\b")


def parse_text(text: str) -> list[dict[str, Any]]:
    """Extract ticker candidates from free-form text.

    Each candidate: ``{symbol, prices[], trigger, invalidation, is_penny,
    note_snippet}``. ``trigger`` = highest price near the token,
    ``invalidation`` = lowest. ``is_penny`` true if any nearby price
    below $5. The caller decides which are auto-checked.

    We tokenise the input into clauses (split on ``.;—|`` + newline)
    and only associate prices with tickers found in the SAME clause.
    This prevents "BE > AAOI > NVDA ... $217.50" from giving all three
    the $217.50 trigger — only whichever ticker actually shares its
    clause with the price.
    """
    if not text or not isinstance(text, str):
        return []

    # Split into clauses. Include a note-preview snippet from the
    # clause the ticker first appeared in.
    clauses = _split_clauses(text)

    seen: dict[str, dict[str, Any]] = {}
    for clause in clauses:
        prices = _extract_prices(clause)
        tickers_in_clause: list[str] = []
        for m in _TICKER_RX.finditer(clause):
            tok = m.group(0)
            if tok in _TICKER_STOPLIST:
                # Only re-admit a stoplisted token if it's on the soft
                # allowlist (real ticker AND common English word, e.g.
                # BE, MU) AND the clause has a nearby price. Otherwise
                # keep it filtered — a $price near "USD" or "FED" is
                # never intent to buy USD or FED.
                if tok in _SOFT_ALLOWLIST and prices:
                    tickers_in_clause.append(tok)
                continue
            tickers_in_clause.append(tok)

        for tok in tickers_in_clause:
            entry = seen.get(tok) or {
                "symbol": tok,
                "prices": [],
                "note_snippet": clause.strip()[:240],
            }
            entry["prices"] = sorted(set(entry["prices"] + prices))
            seen[tok] = entry

    # Build final candidates.
    out: list[dict[str, Any]] = []
    for tok, entry in seen.items():
        prices = entry["prices"]
        trigger = max(prices) if prices else None
        invalidation = min(prices) if len(prices) >= 2 else None
        is_penny = bool(prices) and min(prices) < 5.0
        out.append({
            "symbol": tok,
            "trigger": trigger,
            "invalidation": invalidation,
            "is_penny": is_penny,
            "prices": prices,
            "note_snippet": entry["note_snippet"],
            "auto_checked": (not is_penny),
        })
    out.sort(key=lambda x: (x["is_penny"], x["symbol"]))
    return out


# Split on sentence-ish boundaries. Careful NOT to split on the ``.``
# inside a decimal price ($217.50), so we require the ``.`` to be
# followed by whitespace or end-of-string. Also NOT on em-dash — that's
# often the symbol/price separator in research text ("SWVL — $2.07").
_CLAUSE_SPLIT_RX = re.compile(r"(?:[.;!?](?=\s|$))|[;|\n]+")


def _split_clauses(text: str) -> list[str]:
    return [c for c in _CLAUSE_SPLIT_RX.split(text) if c.strip()]


def _extract_prices(snippet: str) -> list[float]:
    prices: list[float] = []
    for pm in _PRICE_RX.finditer(snippet):
        raw = pm.group(0).lstrip("$")
        try:
            v = float(raw)
        except ValueError:
            continue
        # Filter out obvious non-price numbers (years, phone bits, %s).
        if 0.01 <= v <= 10_000:
            prices.append(round(v, 4))
    return prices


# ── Mongo CRUD ────────────────────────────────────────────────────────


def _end_of_session_utc() -> datetime:
    """21:00 UTC of today, or tomorrow if we're already past."""
    now = datetime.now(timezone.utc)
    today_close = datetime.combine(now.date(), time(21, 0), tzinfo=timezone.utc)
    if now >= today_close:
        today_close += timedelta(days=1)
    return today_close


async def add_picks(
    db: Any,
    picks: list[dict[str, Any]],
    actor: str,
    ttl: str = "session",
    conviction: str = "watch",
) -> int:
    """Upsert picks; returns count actually written."""
    if db is None or not picks:
        return 0
    if ttl == "session":
        expires_at = _end_of_session_utc()
    elif ttl == "day":
        expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    elif ttl == "week":
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    else:
        expires_at = _end_of_session_utc()
    now = datetime.now(timezone.utc)
    n = 0
    for p in picks:
        sym = str(p.get("symbol") or "").upper().strip()
        if not sym:
            continue
        doc = {
            "symbol": sym,
            "trigger": p.get("trigger"),
            "invalidation": p.get("invalidation"),
            "conviction": conviction,
            "note": (p.get("note_snippet") or "")[:240],
            "added_by": actor,
            "added_at": now,
            "expires_at": expires_at,
        }
        await db[WATCHLIST_COLLECTION].update_one(
            {"symbol": sym}, {"$set": doc}, upsert=True,
        )
        n += 1
    return n


async def list_active(db: Any) -> list[dict[str, Any]]:
    if db is None:
        return []
    now = datetime.now(timezone.utc)
    cursor = db[WATCHLIST_COLLECTION].find(
        {"expires_at": {"$gt": now}}, {"_id": 0},
    ).sort("added_at", -1)
    return await cursor.to_list(200)


async def remove_symbol(db: Any, symbol: str) -> int:
    if db is None or not symbol:
        return 0
    r = await db[WATCHLIST_COLLECTION].delete_one({"symbol": symbol.upper()})
    return int(r.deleted_count or 0)


async def coverage_report(db: Any) -> list[dict[str, Any]]:
    """For each active watchlist entry, join the latest
    ``signal_dispatcher`` prediction so ops see which picks Alpha
    has actually scored. NEVER writes synthetic predictions.

    Note: ``predictions.insert`` in ``prediction_tracker.log_prediction``
    stores the ISO timestamp under ``timestamp`` (not ``created_at``);
    the older docstring name was misleading.
    """
    active = await list_active(db)
    if not active:
        return []
    symbols = [a["symbol"] for a in active]
    since = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    preds_by_symbol: dict[str, dict[str, Any]] = {}
    cursor = db.predictions.find(
        {
            "symbol": {"$in": symbols},
            "feature": {"$in": ["signal_dispatcher", "paper_trading"]},
            "timestamp": {"$gte": since},
        },
        {"_id": 0, "symbol": 1, "direction": 1, "confidence": 1,
         "calibrated_confidence": 1, "timestamp": 1},
    ).sort("timestamp", -1)
    async for p in cursor:
        sym = p.get("symbol")
        if sym and sym not in preds_by_symbol:
            preds_by_symbol[sym] = p
    out = []
    for a in active:
        pred = preds_by_symbol.get(a["symbol"])
        out.append({
            **a,
            "has_prediction": pred is not None,
            "prediction": pred,
        })
    return out


__all__ = [
    "WATCHLIST_COLLECTION",
    "add_picks",
    "coverage_report",
    "list_active",
    "parse_text",
    "remove_symbol",
]
