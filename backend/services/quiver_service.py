"""QuiverQuant Service — Alternative data from QuiverQuant API.

Provides congressional trading, insider trading, corporate lobbying, and
government contracts data. Used as PRIMARY source with existing scrapers
as fallbacks.

Resilience layer:
    * Per-endpoint circuit breaker. After `_CIRCUIT_FAIL_THRESHOLD`
      consecutive 5xx responses, the endpoint is skipped for
      `_CIRCUIT_COOLDOWN_SECONDS` — callers get `[]` immediately instead
      of waiting for another 30-second timeout. Quiver's API has had
      persistent 500s on 3 of 4 endpoints for weeks; without this guard
      every page load on the gov-filings feature burned 2+ minutes of
      wall-clock time on dead requests.
    * Sliding-TTL response cache (`_QUIVER_CACHE_TTL_SECONDS` = 6 hours).
      Live Quiver data updates daily at most, so re-pulling on every
      request is pure waste.
    * `get_endpoint_health()` exposes circuit state for the owner-only
      admin endpoint.

Path notes: `govcontractsall` (not `govcontracts`) is the correct URL per
the official `quiverquant` SDK. Our prior code used the wrong path.
"""
import os
import logging
import asyncio
import time
from typing import Optional

from services.sliding_cache import SlidingCache

from services.structured_log import log_warning

logger = logging.getLogger(__name__)

_api_key: Optional[str] = None


def _get_key() -> Optional[str]:
    global _api_key
    if _api_key is None:
        _api_key = os.environ.get("QUIVER_API_KEY", "")
    return _api_key if _api_key else None


# ── Resilience configuration ──────────────────────────────────────────
# 6 hour cache: Quiver "live" endpoints refresh once per business day,
# so holding onto a response for 6h is comfortably fresh.
_QUIVER_CACHE_TTL_SECONDS = 6 * 60 * 60
# Three strikes and the endpoint is out for 15 min. Low threshold because
# a working endpoint essentially never returns 500 — if it does, their
# backend is down and we want to fail fast.
_CIRCUIT_FAIL_THRESHOLD = 3
_CIRCUIT_COOLDOWN_SECONDS = 15 * 60

_quiver_cache = SlidingCache(
    default_ttl_seconds=_QUIVER_CACHE_TTL_SECONDS,
    max_entries=500,
    max_resets=3,  # max ~24h cache lifetime under continuous polling
)

# Per-endpoint-key circuit state: { key: (consecutive_failures, open_until_monotonic) }
_circuit: dict[str, tuple[int, float]] = {}


def _circuit_is_open(key: str) -> bool:
    state = _circuit.get(key)
    if not state:
        return False
    _, open_until = state
    return open_until > time.monotonic()


def _record_success(key: str) -> None:
    _circuit[key] = (0, 0.0)


def _record_failure(key: str) -> None:
    fails, _ = _circuit.get(key, (0, 0.0))
    fails += 1
    if fails >= _CIRCUIT_FAIL_THRESHOLD:
        open_until = time.monotonic() + _CIRCUIT_COOLDOWN_SECONDS
        _circuit[key] = (fails, open_until)
        logger.warning(
            f"QuiverQuant circuit OPENED for '{key}' after {fails} failures — "
            f"cooling down for {_CIRCUIT_COOLDOWN_SECONDS // 60} min"
        )
    else:
        _circuit[key] = (fails, 0.0)


async def _fetch_quiver(endpoint_key: str, url: str) -> Optional[list]:
    """Shared fetch with circuit breaker + cache. Returns parsed JSON list,
    or None on any failure (404, 500, network, JSON parse, circuit open).
    """
    key = _get_key()
    if not key:
        return None

    # Cache hit short-circuits everything
    cached = _quiver_cache.get(url)
    if cached is not None:
        return cached

    # Circuit open → fail fast, don't waste a request
    if _circuit_is_open(endpoint_key):
        logger.debug(f"QuiverQuant circuit open for {endpoint_key}, skipping {url}")
        return None

    try:
        import requests
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        resp = await asyncio.to_thread(
            lambda: requests.get(url, headers=headers, timeout=30)
        )
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "quiver",
            "note": f"QuiverQuant {endpoint_key} network error",
            "endpoint_key": endpoint_key,
        })
        _record_failure(endpoint_key)
        return None

    if resp.status_code != 200:
        log_warning(logger, {
            "error": f"HTTP {resp.status_code}",
            "type": "HTTPError",
            "context": "quiver",
            "note": f"QuiverQuant {endpoint_key}: upstream {resp.status_code}",
            "endpoint_key": endpoint_key,
            "url": url,
        })
        # Only record server errors as circuit failures. 404s are "path
        # wrong / no data" not "server broken" and shouldn't trip the breaker.
        if resp.status_code >= 500:
            _record_failure(endpoint_key)
        return None

    try:
        data = resp.json()
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "quiver",
            "note": f"QuiverQuant {endpoint_key} JSON parse failed",
            "endpoint_key": endpoint_key,
        })
        _record_failure(endpoint_key)
        return None

    if not isinstance(data, list):
        data = []

    _record_success(endpoint_key)
    _quiver_cache.set(url, data)
    return data


# ────────────────────────────────────────────────────────────────────────────
# Live-feed fallback helper
# ────────────────────────────────────────────────────────────────────────────
#
# QuiverQuant's `beta/historical/{endpoint}/{TICKER}` routes have been
# server-error-ing (HTTP 500) for weeks on multiple endpoints, while
# the corresponding `beta/live/{endpoint}` routes (which return the
# full live feed across ALL tickers) work fine. Rather than return
# `[]` when the historical route dies, we fall back to the live feed
# and filter client-side on the `Ticker` key.
#
# This is a strict improvement:
#   * Response-cached — the live feed is fetched once per 6h and
#     serves every per-ticker filter during that window.
#   * Deterministic — filtering on `Ticker` is an O(n) scan over a
#     few-thousand-row list, which in practice is sub-millisecond.
#   * Graceful — if the live feed itself is also 500-ing, we still
#     return `[]` (same failure mode as before).
#
# The trade-off: live feeds only cover recent activity, so per-ticker
# lookups for older data (>~30 days) may return fewer rows than the
# historical route would have. For a "just keep it running" UX that's
# the right trade — partial data beats no data.

def _filter_rows_by_ticker(rows: list[dict], ticker: str) -> list[dict]:
    """Filter a live-feed payload down to a single ticker.

    Quiver payloads use `Ticker` (capital T) as the canonical key,
    but some legacy fields use `ticker`; handle both. Case-insensitive
    so a caller passing `aapl` still matches `AAPL`.
    """
    if not ticker:
        return rows
    tkr = ticker.upper()
    return [
        r for r in rows
        if str(r.get("Ticker") or r.get("ticker") or "").upper() == tkr
    ]


async def _fetch_with_live_fallback(
    primary_url: str,
    primary_key: str,
    live_url: str,
    live_key: str,
    ticker: Optional[str],
) -> list[dict]:
    """Try `primary_url` (usually a per-ticker historical route); on
    failure fall back to `live_url` (full feed) and filter by ticker.
    Returns `[]` on total failure.

    Caller responsibility: pass the same `ticker` used to build
    `primary_url` so the fallback filter matches.
    """
    if primary_url:
        data = await _fetch_quiver(primary_key, primary_url)
        if data:
            return list(data)

    live_data = await _fetch_quiver(live_key, live_url)
    if not live_data:
        return []
    return _filter_rows_by_ticker(list(live_data), ticker or "")


async def get_congressional_trades(ticker: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Fetch recent congressional stock trades.

    Prefers the Mongo-backed ETL cache
    (``quiver_congress_trades`` collection, refreshed weekly) for
    sub-10ms reads. Falls back to the legacy live-feed path when:

    * The ETL cache has not yet been populated (first week after
      framework deploy)
    * The cache returns zero rows for the requested ticker (the
      ETL might not have seen it yet — the live feed has the
      most recent trades)

    Caller shape unchanged from the pre-ETL implementation, so
    every existing consumer in ``routes/quiver.py`` /
    ``services/gov_filings_service.py`` / etc. keeps working
    without modification.
    """
    # Prefer the ETL cache when a Mongo handle is available.
    try:
        from server import db as _server_db
        if _server_db is not None:
            from services.etl_jobs.quiver_congress_trades import (
                get_congressional_trades_cached,
            )
            cached_rows = await get_congressional_trades_cached(
                _server_db, ticker=ticker, limit=limit,
            )
            if cached_rows:
                return cached_rows
            # Empty result → fall through to live feed for the
            # first week post-deploy or when the ticker hasn't
            # been seen yet.
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"ETL cache lookup failed, falling back to live: {exc}"
        )

    # Legacy live-feed path. Kept identical to the pre-ETL
    # implementation so the fallback behaviour is unchanged.
    if ticker:
        data = await _fetch_with_live_fallback(
            primary_url=f"https://api.quiverquant.com/beta/historical/congresstrading/{ticker}",
            primary_key="congresstrading_historical",
            live_url="https://api.quiverquant.com/beta/live/congresstrading",
            live_key="congresstrading_live",
            ticker=ticker,
        )
    else:
        data = await _fetch_quiver(
            "congresstrading_live",
            "https://api.quiverquant.com/beta/live/congresstrading",
        ) or []
    if not data:
        return []

    trades = []
    for row in data[-limit:] if len(data) > limit else data:
        representative = str(row.get("Representative", row.get("representative", "")))
        tx_type = str(row.get("Transaction", row.get("transaction", "")))
        amount = str(row.get("Amount", row.get("Range", row.get("amount", ""))))
        tkr = str(row.get("Ticker", row.get("ticker", ticker or "")))
        date_str = str(row.get("TransactionDate", row.get("Date", row.get("date", ""))))[:10]
        party = str(row.get("Party", row.get("party", "")))
        chamber = str(row.get("House", row.get("house", "")))

        party_short = (
            party if len(party) <= 2
            else ("D" if "dem" in party.lower()
                  else "R" if "rep" in party.lower() else "")
        )

        trades.append({
            "representative": representative,
            "ticker": tkr,
            "transaction_date": date_str,
            "type": tx_type,
            "amount": amount,
            "party": party_short,
            "chamber": chamber,
            "description": f"{tx_type.upper()} by {representative} ({party_short}-{chamber}) — {tkr} {amount}",
            "source": "quiverquant",
        })

    logger.info(f"QuiverQuant congressional (live fallback): {len(trades)} trades")
    return trades


async def get_insider_trades(ticker: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Fetch recent insider trades (SEC Form 4) from QuiverQuant.

    `beta/live/insiders` has been 500-ing upstream for weeks. We keep
    the call so the circuit breaker + existing Finnhub/OpenInsider
    fallback chain (in `gov_filings_service`) remains in force, but
    don't add a live-feed fallback here — when insiders live 500s,
    there's no per-ticker rescue route on Quiver's side.
    """
    if ticker:
        url = f"https://api.quiverquant.com/beta/live/insiders?ticker={ticker}"
    else:
        url = "https://api.quiverquant.com/beta/live/insiders"
    endpoint_key = "insiders"

    data = await _fetch_quiver(endpoint_key, url)
    if not data:
        return []

    trades = []
    for row in data[-limit:] if len(data) > limit else data:
        name = str(row.get("Name", row.get("name", "")))
        title = str(row.get("Title", row.get("title", "")))
        tx_type = str(row.get("Transaction", row.get("transaction", "")))
        shares = str(row.get("Shares", row.get("shares", "")))
        price = str(row.get("Price", row.get("price", "")))
        tkr = str(row.get("Ticker", row.get("ticker", ticker or "")))
        date_str = str(row.get("Date", row.get("date", "")))[:10]

        trades.append({
            "form_type": "Form 4",
            "filed_date": date_str,
            "ticker": tkr,
            "insider_name": name,
            "insider_title": title,
            "trade_type": tx_type,
            "price": price,
            "qty": shares,
            "description": f"Insider {tx_type} by {name} ({title}) — {tkr} {shares} shares @ ${price}",
            "source": "quiverquant",
        })

    logger.info(f"QuiverQuant insiders: {len(trades)} trades")
    return trades


async def get_lobbying(ticker: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Fetch recent corporate lobbying data from QuiverQuant.

    Ticker-specific calls route through `_fetch_with_live_fallback`
    because Quiver's `beta/historical/lobbying/{ticker}` has been
    500-ing persistently while `beta/live/lobbying` (full feed)
    works. See the docstring on `_fetch_with_live_fallback` for the
    trade-off (partial data, cached, deterministic).
    """
    if ticker:
        data = await _fetch_with_live_fallback(
            primary_url=f"https://api.quiverquant.com/beta/historical/lobbying/{ticker}",
            primary_key="lobbying_historical",
            live_url="https://api.quiverquant.com/beta/live/lobbying",
            live_key="lobbying",
            ticker=ticker,
        )
    else:
        data = await _fetch_quiver(
            "lobbying",
            "https://api.quiverquant.com/beta/live/lobbying",
        ) or []
    if not data:
        return []

    records = []
    for row in data[-limit:] if len(data) > limit else data:
        client_name = str(row.get("Client", row.get("client", "")))
        amount = row.get("Amount", row.get("amount", 0))
        issue = str(row.get("Issue", row.get("issue", "")))
        tkr = str(row.get("Ticker", row.get("ticker", ticker or "")))
        date_str = str(row.get("Date", row.get("date", "")))[:10]

        try:
            amount_float = float(amount)
        except (ValueError, TypeError):
            amount_float = 0

        records.append({
            "ticker": tkr,
            "client": client_name,
            "amount": amount_float,
            "issue": issue,
            "date": date_str,
            "description": f"{client_name} lobbied ${amount_float:,.0f} on {issue}",
            "source": "quiverquant",
        })

    logger.info(f"QuiverQuant lobbying: {len(records)} records")
    return records


async def get_gov_contracts(ticker: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Fetch government contract data from QuiverQuant.

    Two live routes exist: `beta/live/govcontractsall` (detailed:
    includes agency + description) and `beta/live/govcontracts`
    (aggregated: ticker + amount + qtr + year only). The `-all`
    variant has been 500-ing persistently upstream; the aggregated
    route returns 200 with 12k+ rows. We prefer `-all` when it works
    (richer data) and transparently fall back to the aggregated route
    when it doesn't. For per-ticker calls we also filter the live
    feed client-side so historical-route 500s don't starve
    downstream consumers.
    """
    if ticker:
        data = await _fetch_with_live_fallback(
            primary_url=f"https://api.quiverquant.com/beta/historical/govcontractsall/{ticker}",
            primary_key="govcontractsall_historical",
            live_url="https://api.quiverquant.com/beta/live/govcontractsall",
            live_key="govcontractsall",
            ticker=ticker,
        )
        if not data:
            # Last-resort fallback: aggregated `govcontracts`
            # (ticker/amount/qtr/year only — no agency, no description,
            # but better than nothing for a "show something" UX).
            fallback = await _fetch_quiver(
                "govcontracts_aggregated",
                "https://api.quiverquant.com/beta/live/govcontracts",
            ) or []
            data = _filter_rows_by_ticker(list(fallback), ticker)
    else:
        data = await _fetch_quiver(
            "govcontractsall",
            "https://api.quiverquant.com/beta/live/govcontractsall",
        ) or []
        if not data:
            data = await _fetch_quiver(
                "govcontracts_aggregated",
                "https://api.quiverquant.com/beta/live/govcontracts",
            ) or []

    if not data:
        return []

    contracts = []
    for row in data[-limit:] if len(data) > limit else data:
        agency = str(row.get("Agency", row.get("agency", "")))
        amount = row.get("Amount", row.get("amount", 0))
        desc = str(row.get("Description", row.get("description", "")))
        tkr = str(row.get("Ticker", row.get("ticker", ticker or "")))
        date_str = str(row.get("Date", row.get("date", "")))[:10]

        try:
            amount_float = float(amount)
        except (ValueError, TypeError):
            amount_float = 0

        contracts.append({
            "ticker": tkr,
            "agency": agency,
            "amount": amount_float,
            "description": desc[:200] if desc else "",
            "date": date_str,
            "source": "quiverquant",
        })

    logger.info(f"QuiverQuant contracts: {len(contracts)} records")
    return contracts


def is_configured() -> bool:
    """Check if QuiverQuant API key is available."""
    return _get_key() is not None


def get_endpoint_health() -> dict:
    """Report per-endpoint circuit-breaker state + cache stats for ops.
    Owner-only caller expected (`routes/admin.py`)."""
    now = time.monotonic()
    endpoints = {}
    for key, (fails, open_until) in _circuit.items():
        status = "open" if open_until > now else ("warning" if fails > 0 else "healthy")
        endpoints[key] = {
            "status": status,
            "consecutive_failures": fails,
            "seconds_until_retry": max(0, int(open_until - now)) if open_until > now else 0,
        }
    return {
        "configured": is_configured(),
        "endpoints": endpoints,
        "cache": _quiver_cache.stats(),
        "config": {
            "cache_ttl_seconds": _QUIVER_CACHE_TTL_SECONDS,
            "circuit_fail_threshold": _CIRCUIT_FAIL_THRESHOLD,
            "circuit_cooldown_seconds": _CIRCUIT_COOLDOWN_SECONDS,
        },
    }
