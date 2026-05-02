"""Tiered top-universe pre-warm service.

Design (see user spec):
    Tier A (rank   1–100): daily history + overview + technicals + quote refresh
    Tier B (rank 101–200): daily history + technicals only
    Tier C (rank 201–300): metadata only, fetched on-demand

Scheduling
----------
Three cron jobs register against APScheduler (wired in server.py):

    - ``rebuild_universe``  — weekly, Sunday 00:00 UTC. Runs ``OVERVIEW``
      on the full seed list (``data/sp500_constituents.json``), ranks by
      market cap, assigns tiers, upserts ``top_universe``.
    - ``warm_post_close``   — daily, 21:05 UTC. Full warm: Tier A (daily
      + overview + technicals + quote), Tier B (daily + technicals).
    - ``warm_pre_open``     — daily, 13:00 UTC. Light warm: Tier A only,
      quote + technicals refresh (overview / daily barely move overnight).

Every run writes a summary row to ``universe_warm_stats`` — consumed by
``/api/admin/top-universe/status`` so ops can see last-warm + coverage
without querying logs.

Operational contract
--------------------
* This service only *seeds* existing caches (``price_cache`` Mongo
  collection + ``sliding_cache.price_cache`` in-memory). It does not
  introduce a new cache tier — downstream services already hit those
  caches transparently, so they pick up the pre-warmed data with zero
  code changes.
* Failures on individual tickers are swallowed and counted — one bad
  symbol never aborts a run.
* Alpha Vantage Premium (150 req/min) budget: full post-close warm on
  200 symbols with 2–3 endpoints each = 400–600 calls ≈ 4 minutes.
  Weekly rebuild = ~500 OVERVIEW calls ≈ 3.5 minutes. Well under budget.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

UNIVERSE_COLLECTION = "top_universe"
WARM_STATS_COLLECTION = "universe_warm_stats"
SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.json"

# Tier sizes — keep these as module constants so the scheduler, admin API,
# and warm routines all agree on the layout.
TIER_A_SIZE = 100
TIER_B_SIZE = 100
TIER_C_SIZE = 100
UNIVERSE_TOTAL = TIER_A_SIZE + TIER_B_SIZE + TIER_C_SIZE

# Concurrency caps keep us under AV's per-minute limit while still finishing
# the warm in a reasonable wall-clock. AV Premium is 150 req/min → 8 parallel
# workers with small per-request sleeps gives ~100 req/min sustained, leaving
# headroom for any other AV caller.
REBUILD_CONCURRENCY = 6
WARM_CONCURRENCY = 8


# ───────────────────────── seed loading ─────────────────────────


def load_seed_tickers() -> list[str]:
    """Load the candidate pool for market-cap ranking.

    Reads ``data/sp500_constituents.json``. Returns a deduplicated,
    uppercase-normalised list. Empty list signals a missing/malformed
    seed — callers treat that as a hard error (never try to guess).
    """
    try:
        raw = json.loads(SEED_PATH.read_text())
        tickers = raw.get("tickers", []) if isinstance(raw, dict) else raw
        out: list[str] = []
        seen: set[str] = set()
        for t in tickers:
            if not isinstance(t, str):
                continue
            u = t.strip().upper()
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out
    except FileNotFoundError:
        logger.error("[top_universe] seed file missing: %s", SEED_PATH)
        return []
    except Exception as exc:
        logger.exception("[top_universe] seed load failed: %s", exc)
        return []


# ───────────────────────── ranking ─────────────────────────


def _parse_market_cap(overview: dict) -> float:
    """Extract market cap from an OVERVIEW dict. AV returns the cap as a
    string of integer USD (e.g. ``"3050000000000"``). Missing / unparseable
    rows return 0 so they sort to the bottom instead of crashing the run.
    """
    raw = overview.get("MarketCapitalization") if overview else None
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_optional_float(raw: Any) -> Optional[float]:
    """AV overview fields are strings; coerce to float or None."""
    if raw in (None, "None", "-", ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _assign_tier(rank: int) -> str:
    if rank <= TIER_A_SIZE:
        return "A"
    if rank <= TIER_A_SIZE + TIER_B_SIZE:
        return "B"
    return "C"


async def _fetch_overview_safe(symbol: str, sem: asyncio.Semaphore) -> tuple[str, Optional[dict]]:
    from services.price_provider import get_overview_sync

    async with sem:
        try:
            data = await asyncio.to_thread(get_overview_sync, symbol)
            return symbol, data
        except Exception as exc:
            logger.warning("[top_universe] overview failed for %s: %s", symbol, exc)
            return symbol, None


async def _fetch_daily_safe(symbol: str, sem: asyncio.Semaphore) -> tuple[str, Optional[list[dict]]]:
    """Pull daily bars for dollar-volume computation during rebuild. Uses
    the existing cache-first pipeline so repeated rebuilds are cheap."""
    from services.price_provider import get_daily_history

    async with sem:
        try:
            bars = await get_daily_history(symbol, outputsize="compact")
            return symbol, bars
        except Exception as exc:
            logger.warning("[top_universe] daily fetch failed for %s: %s", symbol, exc)
            return symbol, None


def _composite_score(market_cap: float, dollar_volume_20d: Optional[float]) -> float:
    """Composite ranking: `log(market_cap) + 0.6 * log(dollar_volume_20d)`.

    Market cap rewards stability, dollar volume rewards tradability — the
    0.6 weight prevents a high-volume penny stock from ranking above a
    mega-cap with moderate turnover. When ``dollar_volume_20d`` is None
    (insufficient bar history), fall back to market cap alone so the
    ticker still sorts sensibly rather than dropping to the bottom.
    """
    import math

    if market_cap <= 0:
        return 0.0
    mc_term = math.log(market_cap)
    if dollar_volume_20d and dollar_volume_20d > 0:
        return round(mc_term + 0.6 * math.log(dollar_volume_20d), 4)
    return round(mc_term, 4)


async def rebuild_universe(db: Any) -> dict:
    """Re-rank the seed list by composite score (market cap + 20d dollar
    volume), assign tiers, upsert collection. Persists rank delta vs
    previous run so moves in/out of the universe become a momentum signal.

    Returns a telemetry dict the admin endpoint surfaces. Also writes a row
    to ``universe_warm_stats`` with ``run_type="rebuild"`` so rebuilds and
    warms share a single timeline.
    """
    from services.universe_technicals import compute_dollar_volume_20d

    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    seed = load_seed_tickers()

    if not seed:
        stats = {
            "run_type": "rebuild",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "symbols_attempted": 0,
            "symbols_succeeded": 0,
            "failures": 0,
            "status": "error",
            "error": "empty_seed",
            "wall_seconds": round(time.perf_counter() - t0, 2),
        }
        await db[WARM_STATS_COLLECTION].insert_one(stats.copy())
        return stats

    sem = asyncio.Semaphore(REBUILD_CONCURRENCY)
    # Fire overview + daily fetches concurrently. Daily fetch is cache-first
    # so after the first rebuild these are nearly free (hit Mongo price_cache).
    overview_task = asyncio.gather(
        *[_fetch_overview_safe(t, sem) for t in seed],
        return_exceptions=False,
    )
    daily_task = asyncio.gather(
        *[_fetch_daily_safe(t, sem) for t in seed],
        return_exceptions=False,
    )
    overviews, dailies = await asyncio.gather(overview_task, daily_task)

    dollar_vol_by_sym = {
        sym: compute_dollar_volume_20d(bars) if bars else None
        for sym, bars in dailies
    }

    # Read existing rank map so we can stamp rank_delta on the new rows.
    # Single projection query is cheaper than one find_one per upsert.
    prev_cursor = db[UNIVERSE_COLLECTION].find(
        {}, {"_id": 0, "symbol": 1, "rank": 1, "active": 1},
    )
    prev_ranks: dict[str, int] = {}
    async for row in prev_cursor:
        if row.get("active") and isinstance(row.get("rank"), int):
            prev_ranks[row["symbol"]] = row["rank"]

    ranked: list[dict] = []
    failures = 0
    for symbol, overview in overviews:
        if not overview:
            failures += 1
            continue
        mc = _parse_market_cap(overview)
        if mc <= 0:
            failures += 1
            continue
        dv = dollar_vol_by_sym.get(symbol)
        ranked.append({
            "symbol": symbol,
            "market_cap": mc,
            "dollar_volume_20d": dv,
            "composite_score": _composite_score(mc, dv),
            "sector": overview.get("Sector") or None,
            "industry": overview.get("Industry") or None,
            "beta": _parse_optional_float(overview.get("Beta")),
            "pe_ratio": _parse_optional_float(overview.get("PERatio")),
            "name": overview.get("Name") or None,
        })

    # Rank descending by composite score, truncate to the supported universe size.
    ranked.sort(key=lambda r: r["composite_score"], reverse=True)
    ranked = ranked[:UNIVERSE_TOTAL]
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
        r["tier"] = _assign_tier(i)
        r["asset_type"] = "equity"
        r["active"] = True
        r["last_ranked_at"] = datetime.now(timezone.utc).isoformat()
        # Drift tracking: positive delta = moved up in ranking this cycle.
        # New entrants (no prior rank) get delta=None so UI can badge them
        # distinctly from "held steady at rank N".
        prev = prev_ranks.get(r["symbol"])
        r["prev_rank"] = prev
        r["rank_delta"] = (prev - i) if prev is not None else None

    # Upsert: mark previously-active rows that didn't make the cut as inactive
    # instead of deleting them, so the history of who-was-in-the-universe is
    # preserved for post-mortems and drift analysis.
    current_symbols = {r["symbol"] for r in ranked}
    await db[UNIVERSE_COLLECTION].update_many(
        {"symbol": {"$nin": list(current_symbols)}, "active": True},
        {"$set": {"active": False, "demoted_at": datetime.now(timezone.utc).isoformat()}},
    )
    for row in ranked:
        await db[UNIVERSE_COLLECTION].update_one(
            {"symbol": row["symbol"]},
            {"$set": row},
            upsert=True,
        )

    stats = {
        "run_type": "rebuild",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "symbols_attempted": len(seed),
        "symbols_succeeded": len(ranked),
        "failures": failures,
        "tier_a_size": TIER_A_SIZE,
        "tier_b_size": TIER_B_SIZE,
        "tier_c_size": TIER_C_SIZE,
        # Drift summary — how many rows actually moved rank this cycle.
        # Promoted/demoted = in the universe both runs but changed rank.
        # New = not present last run. Exited = present last run but not this.
        "drift": {
            "new_entrants": sum(1 for r in ranked if r["prev_rank"] is None),
            "held": sum(
                1 for r in ranked
                if r["prev_rank"] is not None and r["rank_delta"] == 0
            ),
            "promoted": sum(
                1 for r in ranked
                if r["rank_delta"] is not None and r["rank_delta"] > 0
            ),
            "demoted": sum(
                1 for r in ranked
                if r["rank_delta"] is not None and r["rank_delta"] < 0
            ),
            "exited": max(
                0,
                len(prev_ranks) - sum(
                    1 for r in ranked if r["prev_rank"] is not None
                ),
            ),
        },
        "status": "success",
        "wall_seconds": round(time.perf_counter() - t0, 2),
    }
    await db[WARM_STATS_COLLECTION].insert_one(stats.copy())
    logger.info(
        "[top_universe] rebuild complete — %d ranked, %d failed, %.1fs",
        len(ranked), failures, stats["wall_seconds"],
    )
    return stats


# ───────────────────────── warming ─────────────────────────


async def _get_active_tiered_symbols(db: Any, tiers: list[str]) -> list[dict]:
    """Return active universe rows filtered by tier, rank-ordered."""
    cursor = (
        db[UNIVERSE_COLLECTION]
        .find(
            {"active": True, "tier": {"$in": tiers}},
            {"_id": 0, "symbol": 1, "tier": 1, "rank": 1},
        )
        .sort("rank", 1)
    )
    return await cursor.to_list(length=UNIVERSE_TOTAL)


async def _warm_one(
    row: dict,
    *,
    fetch_daily: bool,
    fetch_overview: bool,
    fetch_quote: bool,
    compute_tech: bool,
    db: Any,
    sem: asyncio.Semaphore,
) -> dict:
    """Run the requested fetches for a single symbol. Returns per-symbol stats.

    Errors are logged + counted but never raised — a bad symbol doesn't
    tank the whole warm.
    """
    from services.price_provider import (
        get_daily_history,
        get_quote,
        get_overview_sync,
    )
    from services.universe_technicals import compute_technicals

    symbol = row["symbol"]
    result = {"symbol": symbol, "tier": row["tier"], "ok": True, "errors": []}

    async with sem:
        bars: Optional[list[dict]] = None
        if fetch_daily or compute_tech:
            try:
                bars = await get_daily_history(symbol, outputsize="compact")
                if not bars:
                    result["errors"].append("daily_history_empty")
            except Exception as exc:
                result["errors"].append(f"daily_history:{type(exc).__name__}")

        if fetch_overview:
            try:
                overview = await asyncio.to_thread(get_overview_sync, symbol)
                if not overview:
                    result["errors"].append("overview_empty")
            except Exception as exc:
                result["errors"].append(f"overview:{type(exc).__name__}")

        if fetch_quote:
            try:
                quote = await get_quote(symbol)
                if not quote:
                    result["errors"].append("quote_empty")
            except Exception as exc:
                result["errors"].append(f"quote:{type(exc).__name__}")

        if compute_tech and bars:
            try:
                tech = compute_technicals(bars)
                if tech:
                    # Stamp alongside the universe row so the admin panel /
                    # downstream consumers can read indicator state without
                    # recomputing. Only the latest values — history is the
                    # bars themselves.
                    await db[UNIVERSE_COLLECTION].update_one(
                        {"symbol": symbol},
                        {"$set": {
                            "technicals": tech,
                            "technicals_updated_at": datetime.now(timezone.utc).isoformat(),
                        }},
                    )
            except Exception as exc:
                result["errors"].append(f"technicals:{type(exc).__name__}")

    result["ok"] = not result["errors"]
    return result


async def warm_universe(db: Any, run_type: str = "post_close") -> dict:
    """Pre-warm the caches for active Tier A + Tier B symbols.

    run_type:
        - ``"post_close"``: Tier A full (daily + overview + technicals + quote),
                            Tier B daily + technicals only.
        - ``"pre_open"``:   Tier A quote + technicals refresh only.
    """
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    if run_type == "pre_open":
        tier_a_rows = await _get_active_tiered_symbols(db, ["A"])
        tier_b_rows = []
    else:  # post_close (default)
        tier_a_rows = await _get_active_tiered_symbols(db, ["A"])
        tier_b_rows = await _get_active_tiered_symbols(db, ["B"])

    if not tier_a_rows and not tier_b_rows:
        stats = {
            "run_type": run_type,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "symbols_attempted": 0,
            "symbols_succeeded": 0,
            "failures": 0,
            "status": "skipped",
            "reason": "no_active_universe — run rebuild_universe first",
            "wall_seconds": round(time.perf_counter() - t0, 2),
        }
        await db[WARM_STATS_COLLECTION].insert_one(stats.copy())
        return stats

    sem = asyncio.Semaphore(WARM_CONCURRENCY)

    # ── Tier A ──
    if run_type == "pre_open":
        # Pre-open refresh: quote only. Technicals are derived from daily
        # bars — no new bar has formed overnight, so recomputing them
        # produces identical values. Skip the fetch + recompute entirely.
        a_coros = [
            _warm_one(r, fetch_daily=False, fetch_overview=False,
                      fetch_quote=True, compute_tech=False, db=db, sem=sem)
            for r in tier_a_rows
        ]
    else:
        a_coros = [
            _warm_one(r, fetch_daily=True, fetch_overview=True,
                      fetch_quote=True, compute_tech=True, db=db, sem=sem)
            for r in tier_a_rows
        ]

    # ── Tier B (post_close only) ──
    b_coros = [
        _warm_one(r, fetch_daily=True, fetch_overview=False,
                  fetch_quote=False, compute_tech=True, db=db, sem=sem)
        for r in tier_b_rows
    ]

    results = await asyncio.gather(*a_coros, *b_coros, return_exceptions=False)
    succeeded = sum(1 for r in results if r["ok"])
    failures = len(results) - succeeded

    stats = {
        "run_type": run_type,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "symbols_attempted": len(results),
        "symbols_succeeded": succeeded,
        "failures": failures,
        "tier_a_warmed": len(tier_a_rows),
        "tier_b_warmed": len(tier_b_rows),
        "status": "success" if failures == 0 else ("partial" if succeeded else "error"),
        "wall_seconds": round(time.perf_counter() - t0, 2),
    }
    await db[WARM_STATS_COLLECTION].insert_one(stats.copy())
    logger.info(
        "[top_universe] warm %s complete — %d/%d ok, %.1fs",
        run_type, succeeded, len(results), stats["wall_seconds"],
    )
    return stats


# ───────────────────────── admin surface helpers ─────────────────────────


async def get_status(db: Any) -> dict:
    """Snapshot for /api/admin/top-universe/status."""
    total_active = await db[UNIVERSE_COLLECTION].count_documents({"active": True})
    by_tier = {
        t: await db[UNIVERSE_COLLECTION].count_documents({"active": True, "tier": t})
        for t in ("A", "B", "C")
    }
    last_rebuild = await db[WARM_STATS_COLLECTION].find_one(
        {"run_type": "rebuild"},
        {"_id": 0},
        sort=[("started_at", -1)],
    )
    last_warm = await db[WARM_STATS_COLLECTION].find_one(
        {"run_type": {"$in": ["post_close", "pre_open"]}},
        {"_id": 0},
        sort=[("started_at", -1)],
    )

    # Hit-rate estimate: over the last 10 warms, (1 - failures/attempted).
    # Matches what the user asked for — a coverage signal, not a precise
    # cache-hit metric (the sliding_cache tracks in-process stats too).
    recent = await (
        db[WARM_STATS_COLLECTION]
        .find(
            {"run_type": {"$in": ["post_close", "pre_open"]}, "status": {"$ne": "skipped"}},
            {"_id": 0, "symbols_attempted": 1, "symbols_succeeded": 1},
        )
        .sort("started_at", -1)
        .limit(10)
    ).to_list(length=10)
    attempted = sum(r.get("symbols_attempted", 0) for r in recent)
    succeeded = sum(r.get("symbols_succeeded", 0) for r in recent)
    coverage = round(succeeded / attempted, 3) if attempted > 0 else None

    # Sample of the current universe — rank-ordered first 20 rows so the
    # admin panel can render a preview without another round-trip.
    sample = await (
        db[UNIVERSE_COLLECTION]
        .find(
            {"active": True},
            {"_id": 0, "symbol": 1, "rank": 1, "tier": 1, "market_cap": 1,
             "sector": 1, "technicals_updated_at": 1},
        )
        .sort("rank", 1)
        .limit(20)
    ).to_list(length=20)

    return {
        "active_total": total_active,
        "by_tier": by_tier,
        "tier_sizes": {"A": TIER_A_SIZE, "B": TIER_B_SIZE, "C": TIER_C_SIZE},
        "last_rebuild": last_rebuild,
        "last_warm": last_warm,
        "recent_coverage": coverage,
        "recent_runs_window": len(recent),
        "sample": sample,
    }


async def get_recent_warm_stats(db: Any, limit: int = 30) -> list[dict]:
    """Chronological (newest first) warm + rebuild history for admin UI."""
    limit = max(1, min(limit, 200))
    cursor = (
        db[WARM_STATS_COLLECTION]
        .find({}, {"_id": 0})
        .sort("started_at", -1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)
