"""Alpha Vantage TOP_GAINERS_LOSERS → intraday candidate universe.

Why this exists
---------------
Static watchlists (SPY / QQQ / a hand-picked tier of 300 names) tell
Alpha where *we* think the action might be, but they never reflect
what is actually moving RIGHT NOW. If NVDA is up 12% on an unexpected
partnership headline, or a small-cap is halted-up, none of that
reaches the scanner through the top_universe rebuild — the weekly
job doesn't know.

Alpha Vantage's ``TOP_GAINERS_LOSERS`` endpoint publishes three
lists refreshed intraday: top gainers, top losers, most actively
traded. Together they give Alpha a real-time "the market's moving
here" signal for free.

Design
------
* Poll AV at most every ``LIVE_MOVERS_TTL_SECONDS`` (default 120s).
  AV's free tier caps at 25 calls / day; we DO NOT want to spam it.
* Cache the fetched list in Mongo (``alpha_live_movers``) with a
  timestamp so ``_candidate_universe`` can read it non-blocking
  without hitting the network on every tick.
* Fail closed: any HTTP / parse / auth error returns the previous
  cache. The universe pipeline treats the movers list as "best
  effort" — if it's empty, static watchlist still fires.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────
_AV_URL = "https://www.alphavantage.co/query"
_COLLECTION = "alpha_live_movers"
# The AV free tier is 25 calls/day. We poll on-demand from the
# universe path with this TTL so worst-case (~200 ticks/day) still
# fits the quota with headroom.
_DEFAULT_TTL_SECONDS = 120


def _ttl_seconds() -> int:
    """Minimum age of the cached document before we refresh AV.

    Env-tunable so the operator can loosen the poll cadence when
    running against a paid AV tier.
    """
    raw = (os.environ.get("ALPHA_LIVE_MOVERS_TTL_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        return max(30, min(3600, int(raw)))
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def _api_key() -> Optional[str]:
    return (os.environ.get("ALPHAVANTAGEAPIKEY") or "").strip() or None


# ─────────────────────────────────────────────
#  AV shape → normalized rows
# ─────────────────────────────────────────────
def _parse_bucket(bucket: list[dict], side: str) -> list[dict]:
    """Turn AV's per-list dict shape into our normalized row.

    AV returns strings for every numeric field — we coerce here so
    downstream code doesn't have to.
    """
    out: list[dict] = []
    for row in bucket or []:
        try:
            ticker = str(row.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            price = float(row.get("price") or 0.0)
            change_pct_raw = str(row.get("change_percentage") or "0").rstrip("%")
            change_pct = float(change_pct_raw)
            volume = int(row.get("volume") or 0)
            out.append({
                "symbol": ticker,
                "side": side,   # 'gainer' | 'loser' | 'active'
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
            })
        except (TypeError, ValueError):
            continue
    return out


def _fetch_from_av() -> Optional[dict]:
    """One HTTP round-trip to Alpha Vantage. ``None`` on any failure
    so callers can serve the cached copy instead of erroring."""
    key = _api_key()
    if not key:
        logger.info("[live_movers] ALPHAVANTAGEAPIKEY not set — skipping refresh")
        return None
    try:
        r = requests.get(
            _AV_URL,
            params={"function": "TOP_GAINERS_LOSERS", "apikey": key},
            timeout=10,
        )
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("[live_movers] AV fetch failed: %s", exc)
        return None
    # AV's rate-limit response looks like {"Information": "..."} — no
    # HTTP 429 to catch. We treat it as no-data and keep the cache.
    if "Information" in data or "Note" in data:
        logger.info("[live_movers] AV rate limited / info: %s",
                    str(data.get("Information") or data.get("Note"))[:120])
        return None

    gainers = _parse_bucket(data.get("top_gainers", []), "gainer")
    losers = _parse_bucket(data.get("top_losers", []), "loser")
    actives = _parse_bucket(data.get("most_actively_traded", []), "active")
    if not (gainers or losers or actives):
        return None

    return {
        "gainers": gainers,
        "losers": losers,
        "actives": actives,
        "last_updated": data.get("last_updated"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────
#  Cache read / refresh
# ─────────────────────────────────────────────
async def _read_cache(db: Any) -> Optional[dict]:
    if db is None:
        return None
    try:
        return await db[_COLLECTION].find_one(
            {"_id": "singleton"}, {"_id": 0},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[live_movers] cache read failed: %s", exc)
        return None


async def _write_cache(db: Any, payload: dict) -> None:
    if db is None:
        return
    try:
        await db[_COLLECTION].update_one(
            {"_id": "singleton"},
            {"$set": {**payload, "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[live_movers] cache write failed: %s", exc)


async def refresh_if_stale(db: Any) -> Optional[dict]:
    """Return the current movers payload, refreshing from AV when
    the cached row is older than the TTL. Called opportunistically
    from ``_candidate_universe`` so we don't need a separate
    scheduler for it.
    """
    ttl = _ttl_seconds()
    cached = await _read_cache(db)
    if cached and cached.get("fetched_at"):
        try:
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
        except (TypeError, ValueError):
            fetched_at = None
        if fetched_at is not None:
            age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            if age < ttl:
                return cached
    fresh = _fetch_from_av()
    if fresh is None:
        return cached  # fail closed to the old cache
    await _write_cache(db, fresh)
    return fresh


async def get_mover_symbols(
    db: Any,
    *,
    include_sides: tuple[str, ...] = ("gainer", "loser", "active"),
    limit: int = 30,
) -> list[str]:
    """Return de-duplicated mover tickers, at most ``limit`` of them.

    Order preference: gainer → loser → active (a small-cap with
    real momentum ranks above a large-cap that's just liquid).
    The caller merges this list with the rest of the universe.
    """
    payload = await refresh_if_stale(db)
    if not payload:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for side in include_sides:
        key = {"gainer": "gainers", "loser": "losers", "active": "actives"}.get(side)
        if not key:
            continue
        for row in payload.get(key, []) or []:
            sym = (row.get("symbol") or "").upper().strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
            if len(out) >= limit:
                return out
    return out


__all__ = [
    "refresh_if_stale",
    "get_mover_symbols",
]
