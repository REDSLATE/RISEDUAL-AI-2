"""USASpending.gov government contracts service.

Free public API (no auth) covering federal spending awards. Used as a
fallback for QuiverQuant's gov_contracts endpoint, which has been down
intermittently for weeks.

Scope decisions:
    * We only surface awards going to publicly-traded companies. A
      manually-curated map (`KNOWN_TICKERS`) covers the bulk of defense,
      aerospace, tech, and healthcare primes that RISEDUAL users care
      about. USASpending returns recipients as free-text strings (no
      CUSIP/ticker), so fuzzy name matching is the only path short of
      integrating OpenFIGI per-row. We can add OpenFIGI enrichment later
      if users ask for broader ticker coverage.
    * 6-hour sliding cache (same as Quiver layer) — USASpending data
      updates nightly, so hourly polls are wasted.
    * Public API so no rate limit worries for our traffic volume, but we
      still go through the circuit-breaker pattern for resilience.
"""
import asyncio
import logging
import re
from typing import Optional

import httpx

from services.sliding_cache import SlidingCache

logger = logging.getLogger(__name__)

_USASPENDING_BASE = "https://api.usaspending.gov/api/v2"
_CACHE_TTL_SECONDS = 6 * 60 * 60

_usaspending_cache = SlidingCache(
    default_ttl_seconds=_CACHE_TTL_SECONDS,
    max_entries=50,
    max_resets=3,
)

# Recipient name → ticker map. Hand-curated for the major publicly-traded
# government primes. Match is case-insensitive substring on the recipient
# name as returned by USASpending. Order matters: more-specific patterns
# should come before generic ones (e.g. "LOCKHEED MARTIN CORPORATION"
# must match before "LOCKHEED").
KNOWN_TICKERS: list[tuple[str, str]] = [
    # Defense primes
    ("LOCKHEED MARTIN", "LMT"),
    ("NORTHROP GRUMMAN", "NOC"),
    ("RAYTHEON", "RTX"),
    ("RTX CORPORATION", "RTX"),
    ("BOEING", "BA"),
    ("GENERAL DYNAMICS", "GD"),
    ("L3HARRIS", "LHX"),
    ("HUNTINGTON INGALLS", "HII"),
    ("BAE SYSTEMS", "BAESY"),
    ("LEIDOS", "LDOS"),
    ("BOOZ ALLEN", "BAH"),
    ("SAIC", "SAIC"),
    ("CACI", "CACI"),
    ("PALANTIR", "PLTR"),
    # Aerospace / engines
    ("HONEYWELL", "HON"),
    ("TEXTRON", "TXT"),
    ("GENERAL ELECTRIC", "GE"),
    ("CURTISS-WRIGHT", "CW"),
    ("TRANSDIGM", "TDG"),
    ("HEICO", "HEI"),
    ("AEROVIRONMENT", "AVAV"),
    # Big tech federal
    ("MICROSOFT", "MSFT"),
    ("ORACLE", "ORCL"),
    ("AMAZON WEB SERVICES", "AMZN"),
    ("AMAZON.COM", "AMZN"),
    ("ALPHABET", "GOOGL"),
    ("GOOGLE LLC", "GOOGL"),
    ("INTERNATIONAL BUSINESS MACHINES", "IBM"),
    ("IBM CORPORATION", "IBM"),
    ("CISCO SYSTEMS", "CSCO"),
    ("DELL TECHNOLOGIES", "DELL"),
    ("HEWLETT PACKARD", "HPE"),
    ("ACCENTURE", "ACN"),
    ("SALESFORCE", "CRM"),
    ("SERVICENOW", "NOW"),
    ("INTEL", "INTC"),
    ("NVIDIA", "NVDA"),
    ("APPLE INC", "AAPL"),
    # Healthcare primes (common DoD/VA recipients)
    ("HUMANA", "HUM"),
    ("UNITEDHEALTH", "UNH"),
    ("CVS HEALTH", "CVS"),
    ("MCKESSON", "MCK"),
    ("CARDINAL HEALTH", "CAH"),
    ("PFIZER", "PFE"),
    ("MERCK", "MRK"),
    # Energy / infrastructure
    ("EXXON", "XOM"),
    ("CHEVRON", "CVX"),
    ("GENERAL MOTORS", "GM"),
    ("FORD MOTOR", "F"),
    ("CATERPILLAR", "CAT"),
    ("DEERE", "DE"),
]


def _name_to_ticker(recipient_name: str) -> Optional[str]:
    """Fuzzy match a USASpending recipient name to a ticker.

    Returns None if the recipient is a private company, university, or
    research lab (which USASpending also lists). First-match-wins, so
    KNOWN_TICKERS is ordered for correctness.
    """
    if not recipient_name:
        return None
    upper = recipient_name.upper()
    for pattern, ticker in KNOWN_TICKERS:
        if pattern in upper:
            return ticker
    return None


async def get_gov_contracts(
    ticker: Optional[str] = None, limit: int = 20
) -> list[dict]:
    """Fetch recent federal government contract awards from USASpending.gov.

    Args:
        ticker: If provided, filter results to awards where the recipient
            resolves to this ticker via `_name_to_ticker`. Otherwise
            return the largest recent awards across all tracked primes.
        limit: Max rows returned.

    Returns same shape as `services.quiver_service.get_gov_contracts` so
    `gov_filings_service` can swap in without changing downstream code.
    """
    cache_key = f"usaspending_contracts_{ticker or 'all'}_{limit}"
    cached = _usaspending_cache.get(cache_key)
    if cached is not None:
        return cached

    # Pull the largest recent awards; we filter/map to tickers client-side.
    # Going back 180 days gives enough data that we can find awards across
    # most tracked primes in a single page; USASpending's time_period is
    # "awarded during" not "active during".
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    payload = {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],  # contracts, BPAs, DOs, DCAs
            "time_period": [{
                "start_date": (now - timedelta(days=180)).strftime("%Y-%m-%d"),
                "end_date": now.strftime("%Y-%m-%d"),
            }],
        },
        "fields": [
            "Award ID", "Recipient Name", "Start Date", "End Date",
            "Award Amount", "Awarding Agency", "Description",
        ],
        "page": 1,
        # Pull a wide batch so ticker filtering has something to work with.
        # 100 is USASpending's per-page max.
        "limit": 100,
        "sort": "Award Amount",
        "order": "desc",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{_USASPENDING_BASE}/search/spending_by_award/",
                json=payload,
            )
    except Exception as e:
        logger.warning(f"USASpending network error: {e}")
        return []

    if resp.status_code != 200:
        logger.warning(f"USASpending HTTP {resp.status_code}")
        return []

    try:
        rows = resp.json().get("results", [])
    except Exception as e:
        logger.warning(f"USASpending JSON parse failed: {e}")
        return []

    contracts: list[dict] = []
    for row in rows:
        recipient = str(row.get("Recipient Name", "") or "")
        tkr = _name_to_ticker(recipient)
        if not tkr:
            # Skip private-company / university / lab awards — users only
            # care about publicly-traded names they can trade on.
            continue
        if ticker and tkr != ticker.upper():
            continue

        amount_raw = row.get("Award Amount", 0) or 0
        try:
            amount_float = float(amount_raw)
        except (ValueError, TypeError):
            amount_float = 0.0

        agency = str(row.get("Awarding Agency", "") or "")
        desc = str(row.get("Description", "") or "")
        # USASpending descriptions can be wildly long and ALL CAPS; trim.
        desc = re.sub(r"\s+", " ", desc).strip()[:200]
        date_str = str(row.get("Start Date", "") or "")[:10]

        contracts.append({
            "ticker": tkr,
            "recipient": recipient,
            "agency": agency,
            "amount": amount_float,
            "description": desc,
            "date": date_str,
            "source": "usaspending",
        })
        if len(contracts) >= limit:
            break

    logger.info(f"USASpending gov contracts: {len(contracts)} records for {ticker or 'all'}")
    _usaspending_cache.set(cache_key, contracts)
    return contracts


def is_configured() -> bool:
    """USASpending.gov is public — always available."""
    return True


def get_health() -> dict:
    """Owner-only status report."""
    return {
        "configured": True,
        "provider": "USASpending.gov (public API, no auth)",
        "cache": _usaspending_cache.stats(),
        "tracked_tickers": len({t for _, t in KNOWN_TICKERS}),
    }
