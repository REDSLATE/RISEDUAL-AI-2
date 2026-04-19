"""CUSIP → ticker resolver with persistent MongoDB caching.

Uses OpenFIGI (free, 25 req/min anonymous, 250/6s with a free API key at
https://www.openfigi.com/api). Results are cached in the ``cusip_ticker_map``
collection so each CUSIP is fetched at most once.

Preference order for the composite picked per CUSIP:
 1. US-listed common stock
 2. Any US-listed equity
 3. First row returned
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from collections.abc import Iterable

import httpx

logger = logging.getLogger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
# OpenFIGI limits (https://www.openfigi.com/api/documentation#rate-limit):
#   Anonymous:   25 req/min, 10 jobs per request    → ~250 CUSIPs/min
#   With API key: 250 req/6 sec, 100 jobs per request → ~250,000 CUSIPs/min
def _batch_size() -> int:
    return 100 if _api_key() else 10


BATCH_SIZE = 100  # retained for backwards compat; use _batch_size() at runtime
RATE_LIMIT_SECS_ANON = 2.5  # ~25 req/min = one call every 2.4s
RATE_LIMIT_SECS_KEYED = 0.03  # ~33 req/s burst-friendly ceiling


def _api_key() -> str:
    return os.environ.get("OPENFIGI_API_KEY", "")


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        h["X-OPENFIGI-APIKEY"] = key
    return h


def _pick_best_row(data: list[dict]) -> dict | None:
    if not data:
        return None
    # Prefer US common-stock composite
    for row in data:
        if row.get("exchCode") == "US" and row.get("securityType") in ("Common Stock",):
            return row
    for row in data:
        if row.get("exchCode") == "US":
            return row
    return data[0]


async def lookup_batch(db, cusips: Iterable[str]) -> dict[str, dict]:
    """Return a map of {cusip: {ticker, name, exchange, figi}} for each input CUSIP.

    Cached results come from the ``cusip_ticker_map`` MongoDB collection. Missing
    CUSIPs are fetched in batches from OpenFIGI and written back to the cache.
    Returns only entries we could resolve (ticker present).
    """
    wanted = {c.strip().upper() for c in cusips if c and len(c.strip()) >= 6}
    if not wanted:
        return {}

    out: dict[str, dict] = {}

    # 1) Pull everything we already have
    async for row in db.cusip_ticker_map.find({"cusip": {"$in": list(wanted)}}, {"_id": 0}):
        if row.get("ticker"):
            out[row["cusip"]] = {
                "ticker": row["ticker"],
                "name": row.get("name"),
                "exchange": row.get("exchange"),
                "figi": row.get("figi"),
            }

    missing = sorted(wanted - set(out.keys()) - {c["cusip"] for c in []})
    # Also skip cusips we've already tried and found nothing for recently
    if missing:
        negative_cursor = db.cusip_ticker_map.find(
            {"cusip": {"$in": missing}, "ticker": None}, {"_id": 0, "cusip": 1}
        )
        negatives = {d["cusip"] async for d in negative_cursor}
        missing = [c for c in missing if c not in negatives]

    if not missing:
        return out

    # 2) Fetch missing via OpenFIGI, respecting rate limit
    delay = RATE_LIMIT_SECS_KEYED if _api_key() else RATE_LIMIT_SECS_ANON
    batch_size = _batch_size()
    async with httpx.AsyncClient(timeout=20) as client:
        for i in range(0, len(missing), batch_size):
            chunk = missing[i:i + batch_size]
            payload = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
            try:
                resp = await client.post(OPENFIGI_URL, json=payload, headers=_headers())
            except Exception as e:
                logger.warning(f"OpenFIGI batch error: {e}")
                await asyncio.sleep(delay)
                continue
            if resp.status_code == 429:
                # Use OpenFIGI's ratelimit-reset header if provided (v3 docs)
                try:
                    reset_s = float(resp.headers.get("ratelimit-reset", "30"))
                except (ValueError, TypeError):
                    reset_s = 30.0
                reset_s = min(max(reset_s, 1.0), 60.0)
                logger.warning(f"OpenFIGI rate-limited, backing off {reset_s:.1f}s")
                await asyncio.sleep(reset_s)
                continue
            if resp.status_code != 200:
                logger.warning(f"OpenFIGI returned {resp.status_code}: {resp.text[:120]}")
                await asyncio.sleep(delay)
                continue
            try:
                data = resp.json()
            except Exception:
                await asyncio.sleep(delay)
                continue

            now = datetime.now(timezone.utc).isoformat()
            for cusip, row_envelope in zip(chunk, data, strict=False):
                rows = row_envelope.get("data") or []
                best = _pick_best_row(rows)
                if best and best.get("ticker"):
                    doc = {
                        "cusip": cusip,
                        "ticker": best.get("ticker"),
                        "name": best.get("name"),
                        "exchange": best.get("exchCode"),
                        "figi": best.get("figi") or best.get("compositeFIGI"),
                        "security_type": best.get("securityType"),
                        "resolved_at": now,
                    }
                    out[cusip] = {
                        "ticker": doc["ticker"],
                        "name": doc["name"],
                        "exchange": doc["exchange"],
                        "figi": doc["figi"],
                    }
                    await db.cusip_ticker_map.update_one(
                        {"cusip": cusip}, {"$set": doc}, upsert=True,
                    )
                else:
                    # Negative-cache so we don't retry on every request
                    await db.cusip_ticker_map.update_one(
                        {"cusip": cusip},
                        {"$set": {"cusip": cusip, "ticker": None, "resolved_at": now,
                                  "error": row_envelope.get("warning") or "no_match"}},
                        upsert=True,
                    )
            await asyncio.sleep(delay)
    return out


async def get_ticker(db, cusip: str) -> str | None:
    """Resolve a single CUSIP to its primary US ticker."""
    mapping = await lookup_batch(db, [cusip])
    entry = mapping.get(cusip.upper())
    return entry["ticker"] if entry else None


# ──────────────────────────────────────────────────────────────────────────
# Company name → ticker resolver
# ──────────────────────────────────────────────────────────────────────────
# USASpending.gov and other free sources return recipient names as free-text
# (e.g. "LOCKHEED MARTIN CORPORATION"). We need a ticker to link the data
# into RISEDUAL's existing by-symbol pipelines. OpenFIGI's /v3/search endpoint
# accepts a freeform company name and returns candidate securities — first
# exact-name US-listed equity wins, same preference rules as CUSIP lookup.
#
# Cached in the same `cusip_ticker_map` collection under a different key
# shape (`query` instead of `cusip`) so one collection covers both lookup
# directions. Negative cache prevents re-hitting OpenFIGI every request
# for a private-company recipient (university, research lab, non-public).

OPENFIGI_SEARCH_URL = "https://api.openfigi.com/v3/search"


async def resolve_name_to_ticker(db, name: str) -> str | None:
    """Return the US ticker for a free-text company name, or None.

    Uses OpenFIGI search with persistent caching. Safe for hot-path callers
    — cache hits short-circuit before any network call.
    """
    if not name or len(name.strip()) < 3:
        return None
    query = name.strip().upper()[:200]  # OpenFIGI rejects overly-long queries

    # 1. Cache hit (positive or negative)
    cached = await db.cusip_ticker_map.find_one(
        {"query": query}, {"_id": 0, "ticker": 1},
    )
    if cached is not None:
        return cached.get("ticker")  # None if negative-cached

    if not _api_key():
        # Anonymous search is heavily rate-limited; skip to keep the hot
        # path fast. Callers (USASpending) have their own hand-curated
        # fallback map, so this degrades gracefully.
        return None

    now = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                OPENFIGI_SEARCH_URL,
                json={"query": query, "exchCode": "US"},
                headers=_headers(),
            )
    except Exception as e:
        logger.warning(f"OpenFIGI search network error for '{query[:40]}': {e}")
        return None

    if resp.status_code == 429:
        logger.warning("OpenFIGI search rate-limited, skipping this lookup")
        # Do NOT negative-cache — rate limit is transient, retry next time.
        return None
    if resp.status_code != 200:
        logger.warning(f"OpenFIGI search {resp.status_code} for '{query[:40]}'")
        # Server errors are also transient, don't poison the cache.
        return None

    try:
        rows = (resp.json() or {}).get("data") or []
    except Exception:
        return None

    best = _pick_best_row(rows)
    if best and best.get("ticker"):
        doc = {
            "query": query,
            "ticker": best.get("ticker"),
            "name": best.get("name"),
            "exchange": best.get("exchCode"),
            "figi": best.get("figi") or best.get("compositeFIGI"),
            "resolved_at": now,
        }
        await db.cusip_ticker_map.update_one(
            {"query": query}, {"$set": doc}, upsert=True,
        )
        return doc["ticker"]

    # Negative cache — private company / lab / university
    await db.cusip_ticker_map.update_one(
        {"query": query},
        {"$set": {"query": query, "ticker": None, "resolved_at": now,
                  "error": "no_match"}},
        upsert=True,
    )
    return None


async def backfill_from_holdings(db, limit: int = 5000, top_only: bool = False) -> dict:
    """Bootstrap: resolve tickers for CUSIPs currently stored in ``sec_13f_holdings``.

    When ``top_only=True``, only resolves the top 50 CUSIPs per institution by
    position value — much faster for visible-UI coverage (~600 CUSIPs vs 7800+).

    Returns a {total, resolved, unresolved} summary.
    """
    if top_only:
        # Get top 50 CUSIPs per institution by value_usd
        pipeline = [
            {"$sort": {"cik": 1, "value_usd": -1}},
            {"$group": {
                "_id": "$cik",
                "cusips": {"$push": "$cusip"},
            }},
        ]
        seen: set[str] = set()
        async for group in db.sec_13f_holdings.aggregate(pipeline):
            for c in group.get("cusips", [])[:50]:
                if c and len(c) >= 6:
                    seen.add(c)
                    if len(seen) >= limit:
                        break
            if len(seen) >= limit:
                break
        cusips = list(seen)[:limit]
    else:
        cusips = await db.sec_13f_holdings.distinct("cusip")
        cusips = [c for c in cusips if c and len(c) >= 6][:limit]

    if not cusips:
        return {"total": 0, "resolved": 0, "unresolved": 0}
    mapping = await lookup_batch(db, cusips)
    return {
        "total": len(cusips),
        "resolved": len(mapping),
        "unresolved": len(cusips) - len(mapping),
    }
