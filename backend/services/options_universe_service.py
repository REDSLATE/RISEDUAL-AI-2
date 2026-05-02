"""Options universe orchestrator — Phase 2a.

Pipeline per symbol:

    raw chain (broker)  ──►  filter_contracts  ──►  rank_contracts(top_n)
                                                         │
                                                         ▼
                                                per-symbol aggregates
                                                (PCR, total_volume, mean_iv)

All 11 symbols' results are packed into a single ``option_universe``
Mongo document keyed ``_id="current"``. That doc is the canonical
"what's hot right now" snapshot read by the Strategist / Commander /
conviction hooks — fast single-doc read, no cursor iteration.

Scheduling contract (enforced by the cron gate below):
    * Every 5 minutes, but ONLY during US market hours (13:30–21:00 UTC
      Mon–Fri). Off-hours the chain doesn't move — skipping the fetch
      saves ~120 yfinance calls per overnight.
    * Caller (``server.py`` scheduler) always invokes ``warm_options_universe``
      every 5 min; the gate inside this module short-circuits when
      markets are closed so the scheduler entry stays simple.

Non-goals (Phase 2a):
    * No history / rollover collection — the user's spec is a
      current-snapshot doc. Rollover can be added cheaply later
      by appending ``option_universe_snapshots`` from this same function.
    * No integration with ``failure_mode_classifier`` / ``conviction_service``
      / ``adversarial_core`` / ``ml_paper_trader`` yet — those hooks are
      Phase 2b, shipped separately so each wire-up gets focused review.

Data source priority (unchanged): Tradier → yfinance fallback.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

from services.options_filters import filter_contracts
from services.options_flow_scorer import rank_contracts

logger = logging.getLogger(__name__)

OPTIONS_UNIVERSE_COLLECTION = "option_universe"
WARM_STATS_COLLECTION = "universe_warm_stats"
CURRENT_SNAPSHOT_ID = "current"

# Hand-picked options Tier A underlyings (user spec). SPY/QQQ/NVDA/TSLA
# anchor the set — "these dominate flow, everything else is secondary."
CORE_SYMBOLS: list[str] = [
    "SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL",
    "MSFT", "AMZN", "META", "AMD", "GOOGL",
]

TOP_N_PER_SYMBOL = 10
EXPIRATIONS_PER_SYMBOL = 3
WARM_CONCURRENCY = 4

# Market hours (UTC): 13:30–21:00, Mon–Fri. Outside this window the chain
# is static, so we skip the fetch cycle. Boundaries are inclusive-start,
# exclusive-end to match the US equity options regular session.
MARKET_OPEN_HOUR_UTC = 13
MARKET_OPEN_MIN_UTC = 30
MARKET_CLOSE_HOUR_UTC = 21
MARKET_CLOSE_MIN_UTC = 0

_ENV_OVERRIDE = "OPTIONS_UNIVERSE_UNDERLYINGS"


def get_options_underlyings() -> list[str]:
    """Underlying list — env override first, then the hand-picked core."""
    raw = os.environ.get(_ENV_OVERRIDE, "").strip()
    if raw:
        out = [s.strip().upper() for s in raw.split(",") if s.strip()]
        if out:
            return out
    return list(CORE_SYMBOLS)


def is_market_open(now: datetime | None = None) -> bool:
    """True when ``now`` falls inside the US regular options session
    (Mon–Fri, 13:30–21:00 UTC). Used by the 5-min scheduler to skip
    off-hours warms so we don't burn rate limit on static data.
    """
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Sat / Sun
        return False
    open_mins = MARKET_OPEN_HOUR_UTC * 60 + MARKET_OPEN_MIN_UTC
    close_mins = MARKET_CLOSE_HOUR_UTC * 60 + MARKET_CLOSE_MIN_UTC
    now_mins = now.hour * 60 + now.minute
    return open_mins <= now_mins < close_mins


# ───────────────────── chain fetching (Tradier → yfinance) ─────────────────────


async def _fetch_expirations(underlying: str) -> list[str]:
    try:
        from services.brokers.tradier_options import fetch_tradier_expirations
        dates = await fetch_tradier_expirations(underlying)
        if dates:
            return dates[:EXPIRATIONS_PER_SYMBOL]
    except Exception as exc:
        logger.debug("[options_universe] tradier expirations failed for %s: %s", underlying, exc)

    def _yf_expirations() -> list[str]:
        try:
            import yfinance as yf
            return list(yf.Ticker(underlying).options or [])
        except Exception as exc:
            logger.debug("[options_universe] yfinance expirations failed for %s: %s", underlying, exc)
            return []

    dates = await asyncio.to_thread(_yf_expirations)
    return dates[:EXPIRATIONS_PER_SYMBOL]


async def _fetch_chain(underlying: str, expiration: str) -> list[dict]:
    """Normalised chain fetch. Tradier first; yfinance fallback."""
    try:
        from services.brokers.tradier_options import fetch_tradier_option_chain
        chain = await fetch_tradier_option_chain(underlying, expiration)
        if chain:
            # Normalise Tradier shape to match the filter contract.
            # Tradier already emits contract_type, we pass through as "type".
            out = []
            for c in chain:
                out.append({
                    "expiry": c.get("expiry", expiration),
                    "strike": float(c.get("strike") or 0),
                    "type": "CALL" if c.get("contract_type") == "call" else "PUT",
                    "bid": float(c.get("bid") or 0),
                    "ask": float(c.get("ask") or 0),
                    "volume": int(c.get("volume") or 0),
                    "open_interest": int(c.get("open_interest") or 0),
                    "implied_volatility": c.get("implied_volatility"),
                    "occ_symbol": c.get("occ_symbol", ""),
                    "underlying": c.get("underlying", underlying.upper()),
                })
            return out
    except Exception as exc:
        logger.debug("[options_universe] tradier chain failed for %s %s: %s",
                     underlying, expiration, exc)

    def _yf_chain() -> list[dict]:
        try:
            import yfinance as yf
            chain_obj = yf.Ticker(underlying).option_chain(expiration)
        except Exception as exc:
            logger.debug("[options_universe] yfinance chain failed for %s %s: %s",
                         underlying, expiration, exc)
            return []
        out: list[dict] = []
        for df, opt_type in ((chain_obj.calls, "CALL"), (chain_obj.puts, "PUT")):
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                bid = float(row.get("bid") or 0)
                ask = float(row.get("ask") or 0)
                raw_vol = row.get("volume")
                raw_oi = row.get("openInterest")
                # yfinance leaves NaN for quiet contracts.
                vol = 0 if raw_vol is None or (isinstance(raw_vol, float) and math.isnan(raw_vol)) else int(raw_vol)
                oi = 0 if raw_oi is None or (isinstance(raw_oi, float) and math.isnan(raw_oi)) else int(raw_oi)
                iv = row.get("impliedVolatility")
                try:
                    iv_val = float(iv) if iv is not None else None
                    if iv_val is not None and iv_val != iv_val:
                        iv_val = None
                except (TypeError, ValueError):
                    iv_val = None
                out.append({
                    "expiry": expiration,
                    "strike": float(row.get("strike") or 0),
                    "type": opt_type,
                    "bid": bid,
                    "ask": ask,
                    "volume": vol,
                    "open_interest": oi,
                    "implied_volatility": iv_val,
                    "occ_symbol": str(row.get("contractSymbol") or ""),
                    "underlying": underlying.upper(),
                })
        return out

    return await asyncio.to_thread(_yf_chain)


def get_options_chain_sync(symbol: str) -> list[dict]:
    """Synchronous convenience wrapper matching the user's integration-hook
    signature (``get_options_chain(symbol) -> list[dict]``). Fetches the
    nearest expiration only — sufficient for point-in-time lookups from
    ``failure_mode_classifier`` / ``conviction_service`` (Phase 2b).
    """
    async def _impl() -> list[dict]:
        expirations = await _fetch_expirations(symbol)
        if not expirations:
            return []
        return await _fetch_chain(symbol, expirations[0])
    try:
        return asyncio.run(_impl())
    except RuntimeError:
        # Called from within an existing event loop — fall back to thread
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_impl())
        finally:
            loop.close()


# ───────────────────── per-symbol aggregates ─────────────────────


def _aggregate_contracts(contracts: list[dict]) -> dict:
    """Compute per-symbol aggregates from the PRE-filter chain (so PCR and
    totals reflect real flow, not the post-filter shortlist)."""
    calls = [c for c in contracts if c.get("type") == "CALL"]
    puts = [c for c in contracts if c.get("type") == "PUT"]
    call_vol = sum((c.get("volume", 0) or 0) for c in calls)
    put_vol = sum((c.get("volume", 0) or 0) for c in puts)
    total_oi = sum((c.get("open_interest", 0) or 0) for c in contracts)

    ivs = [c["implied_volatility"] for c in contracts
           if c.get("implied_volatility") is not None and c["implied_volatility"] > 0]
    mean_iv = round(sum(ivs) / len(ivs), 4) if ivs else None

    return {
        "put_call_ratio": round(put_vol / call_vol, 3) if call_vol > 0 else None,
        "total_call_volume": call_vol,
        "total_put_volume": put_vol,
        "total_volume": call_vol + put_vol,
        "total_open_interest": total_oi,
        "mean_iv": mean_iv,
        # IV rank/percentile need ≥252 days of mean_iv history. Surfaced
        # as None until a derivation pass fills them — never fake these.
        "iv_rank": None,
        "iv_percentile": None,
    }


# ───────────────────── build + warm ─────────────────────


async def _build_for_symbol(symbol: str, sem: asyncio.Semaphore) -> dict | None:
    """Fetch chains across ``EXPIRATIONS_PER_SYMBOL`` expirations, filter,
    rank top-N. Returns ``{symbol, contracts, aggregate}`` or None if the
    symbol produced nothing liquid."""
    async with sem:
        expirations = await _fetch_expirations(symbol)
        if not expirations:
            return None

        raw: list[dict] = []
        for exp in expirations:
            chain = await _fetch_chain(symbol, exp)
            raw.extend(chain)

        if not raw:
            return None

        aggregate = _aggregate_contracts(raw)
        filtered = filter_contracts(raw)
        ranked = rank_contracts(filtered, top_n=TOP_N_PER_SYMBOL)

        if not ranked:
            # Symbol's chain is liquid-but-not-unusual — still worth
            # recording aggregates so the UI can show "flat flow" for it.
            return {
                "symbol": symbol.upper(),
                "contracts": [],
                "aggregate": aggregate,
            }

        return {
            "symbol": symbol.upper(),
            "contracts": ranked,
            "aggregate": aggregate,
        }


async def build_options_universe(symbols: list[str]) -> list[dict]:
    """Run the pipeline for every symbol concurrently. Returns the
    ``data`` array that goes into the snapshot doc.
    """
    sem = asyncio.Semaphore(WARM_CONCURRENCY)
    results = await asyncio.gather(
        *[_build_for_symbol(s, sem) for s in symbols],
        return_exceptions=False,
    )
    return [r for r in results if r]


async def warm_options_universe(db: Any, force: bool = False) -> dict:
    """Refresh the ``option_universe`` current-snapshot document.

    Short-circuits when markets are closed unless ``force=True`` (manual
    admin trigger). Records its stats to ``universe_warm_stats``.
    """
    import time
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    if not force and not is_market_open(started_at):
        stats = {
            "run_type": "options_warm",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "symbols_attempted": 0,
            "symbols_succeeded": 0,
            "failures": 0,
            "status": "skipped",
            "reason": "market_closed",
            "wall_seconds": round(time.perf_counter() - t0, 2),
        }
        await db[WARM_STATS_COLLECTION].insert_one(stats.copy())
        return stats

    underlyings = get_options_underlyings()
    universe = await build_options_universe(underlyings)
    finished_at = datetime.now(timezone.utc)

    # Single-doc snapshot (user spec). Replace, not append, so reads are
    # always the latest universe. History can be layered on later via a
    # separate ``option_universe_snapshots`` append collection if needed.
    await db[OPTIONS_UNIVERSE_COLLECTION].replace_one(
        {"_id": CURRENT_SNAPSHOT_ID},
        {
            "_id": CURRENT_SNAPSHOT_ID,
            "data": universe,
            "underlyings_configured": underlyings,
            "top_n_per_symbol": TOP_N_PER_SYMBOL,
            "updated_at": finished_at.isoformat(),
        },
        upsert=True,
    )

    succeeded = len(universe)
    failures = len(underlyings) - succeeded
    stats = {
        "run_type": "options_warm",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "symbols_attempted": len(underlyings),
        "symbols_succeeded": succeeded,
        "failures": failures,
        "status": "success" if failures == 0 else ("partial" if succeeded else "error"),
        "wall_seconds": round(time.perf_counter() - t0, 2),
        "symbols_with_hot_flow": [
            u["symbol"] for u in universe if u.get("contracts")
        ],
    }
    await db[WARM_STATS_COLLECTION].insert_one(stats.copy())
    logger.info(
        "[options_universe] warm complete — %d/%d ok, %d with hot flow, %.1fs",
        succeeded, len(underlyings), len(stats["symbols_with_hot_flow"]),
        stats["wall_seconds"],
    )
    return stats


# ───────────────────── admin helpers ─────────────────────


async def get_options_status(db: Any) -> dict:
    """Snapshot for ``/api/admin/options-universe/status``.

    Returns the live current-snapshot doc (or an empty scaffold when no
    warm has run yet) plus the latest warm stats row so ops can see
    last-run telemetry and coverage in one call.
    """
    snapshot = await db[OPTIONS_UNIVERSE_COLLECTION].find_one(
        {"_id": CURRENT_SNAPSHOT_ID}, {"_id": 0},
    )
    last_warm = await db[WARM_STATS_COLLECTION].find_one(
        {"run_type": "options_warm"},
        {"_id": 0},
        sort=[("started_at", -1)],
    )

    underlyings = get_options_underlyings()
    data = (snapshot or {}).get("data", [])
    by_symbol = {u["symbol"]: len(u.get("contracts", [])) for u in data}

    return {
        "snapshot_updated_at": (snapshot or {}).get("updated_at"),
        "underlyings_configured": underlyings,
        "top_n_per_symbol": TOP_N_PER_SYMBOL,
        "symbols_with_data": len(data),
        "symbols_with_hot_flow": sum(1 for u in data if u.get("contracts")),
        "contracts_by_symbol": by_symbol,
        "data": data,
        "last_warm": last_warm,
        "is_market_open": is_market_open(),
    }
