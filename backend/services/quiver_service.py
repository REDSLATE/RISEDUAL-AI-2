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

from services.structured_log import log_error, log_warning

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
            "note": "QuiverQuant <endpoint_key> network error",
            "endpoint_key": endpoint_key,
        })
        _record_failure(endpoint_key)
        return None

    if resp.status_code != 200:
        log_warning(logger, {
            "error": str(url),
            "type": type(url).__name__,
            "context": "quiver",
            "note": "QuiverQuant <endpoint_key>: HTTP <expr> —",
            "endpoint_key": endpoint_key,
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
            "note": "QuiverQuant <endpoint_key> JSON parse failed",
            "endpoint_key": endpoint_key,
        })
        _record_failure(endpoint_key)
        return None

    if not isinstance(data, list):
        data = []

    _record_success(endpoint_key)
    _quiver_cache.set(url, data)
    return data


async def get_congressional_trades(ticker: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Fetch recent congressional stock trades from QuiverQuant."""
    if ticker:
        url = f"https://api.quiverquant.com/beta/historical/congresstrading/{ticker}"
        endpoint_key = "congresstrading_historical"
    else:
        url = "https://api.quiverquant.com/beta/live/congresstrading"
        endpoint_key = "congresstrading_live"

    data = await _fetch_quiver(endpoint_key, url)
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

    logger.info(f"QuiverQuant congressional: {len(trades)} trades")
    return trades


async def get_insider_trades(ticker: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Fetch recent insider trades (SEC Form 4) from QuiverQuant."""
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
    """Fetch recent corporate lobbying data from QuiverQuant."""
    if ticker:
        url = f"https://api.quiverquant.com/beta/historical/lobbying/{ticker}"
    else:
        url = "https://api.quiverquant.com/beta/live/lobbying"
    endpoint_key = "lobbying"

    data = await _fetch_quiver(endpoint_key, url)
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

    Note: the correct path is `govcontractsall`, not `govcontracts` —
    verified from the official `quiverquant` SDK. Our prior code used the
    shorter variant which returns 404.
    """
    if ticker:
        url = f"https://api.quiverquant.com/beta/historical/govcontractsall/{ticker}"
    else:
        url = "https://api.quiverquant.com/beta/live/govcontractsall"
    endpoint_key = "govcontractsall"

    data = await _fetch_quiver(endpoint_key, url)
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
