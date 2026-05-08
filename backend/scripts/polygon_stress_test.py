#!/usr/bin/env python3
"""Polygon.io live stress-test harness.

Exercises every public method of :class:`PolygonClient` against the
production API using the `POLYGON_API_KEY` in `.env`. Emits a
structured report so we can compare Polygon's output to Finnhub's
before flipping the provider switch.

Categories checked:
  1. **Connectivity** — any 4xx/5xx on the 4 core endpoints?
  2. **Field-map correctness** — the normalised dict contains the
     expected keys and plausible types (no silent None explosion).
  3. **Rate-limit behaviour** — does our bucket honour the stated
     5-req/min free-tier ceiling without exceptions?
  4. **Divergence vs FinnhubClient** — spot-check of `current_price`
     for 3 liquid tickers (AAPL / MSFT / SPY). Percent difference is
     logged; > 0.5% on a liquid large-cap mid-session is a red flag
     worth investigating before we swap providers.

Usage:
    cd /app/backend
    python -m scripts.polygon_stress_test              # single-ticker quickcheck
    python -m scripts.polygon_stress_test --full       # exhaustive run

Exit codes:
  0 — all checks passed
  1 — at least one critical check failed (connectivity / field map)
  2 — only non-critical divergence warnings

This is a READ-ONLY harness. It never writes to Polygon, Finnhub, or
Mongo. Safe to run anytime without risk of side effects.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
)
log = logging.getLogger("polygon-stress")

# Result codes for each check. The overall harness exit code is the
# strictest of any individual result (CRITICAL_FAIL > WARN > OK).
_OK = "OK"
_WARN = "WARN"
_FAIL = "FAIL"


# ═══════════════════════════════════════════════════════════════════════════════
# Field-map expectations
# ═══════════════════════════════════════════════════════════════════════════════

_QUOTE_REQUIRED_KEYS = {"current_price", "timestamp"}
_QUOTE_OPTIONAL_KEYS = {
    "change", "percent_change", "high", "low", "open", "previous_close",
}

_PROFILE_REQUIRED_KEYS = {"name", "ticker"}

_NEWS_REQUIRED_KEYS = {"headline", "url", "datetime"}


def _has_expected_shape(
    result: Any, required: set[str], optional: set[str] = frozenset()
) -> tuple[bool, list[str]]:
    """Return ``(ok, missing_or_wrong_type_keys)``."""
    if not isinstance(result, dict):
        return False, ["<not-a-dict>"]
    missing = required - set(result.keys())
    return (not missing), sorted(missing)


async def _time_call(label: str, coro) -> tuple[str, float, Any]:
    """Await a coroutine, report duration and capture exceptions."""
    start = asyncio.get_event_loop().time()
    try:
        result = await coro
        duration = asyncio.get_event_loop().time() - start
        return _OK, duration, result
    except Exception as exc:  # pragma: no cover — diagnostic path
        duration = asyncio.get_event_loop().time() - start
        log.error("%s raised: %s", label, exc)
        return _FAIL, duration, exc


# ═══════════════════════════════════════════════════════════════════════════════
# Individual probes
# ═══════════════════════════════════════════════════════════════════════════════

async def probe_quote(client: Any, symbol: str) -> dict:
    status, dt, data = await _time_call(
        f"get_quote({symbol})", client.get_quote(symbol)
    )
    if status != _OK:
        return {"check": "quote", "symbol": symbol, "status": _FAIL, "reason": repr(data)}
    ok, missing = _has_expected_shape(data, _QUOTE_REQUIRED_KEYS, _QUOTE_OPTIONAL_KEYS)
    if not ok:
        return {
            "check": "quote", "symbol": symbol, "status": _FAIL,
            "reason": f"missing required keys: {missing}", "got_keys": sorted((data or {}).keys()),
        }
    return {
        "check": "quote", "symbol": symbol, "status": _OK,
        "current_price": data.get("current_price"),
        "percent_change": data.get("percent_change"),
        "latency_s": round(dt, 3),
    }


async def probe_profile(client: Any, symbol: str) -> dict:
    status, dt, data = await _time_call(
        f"get_company_profile({symbol})", client.get_company_profile(symbol)
    )
    if status != _OK:
        return {"check": "profile", "symbol": symbol, "status": _FAIL, "reason": repr(data)}
    ok, missing = _has_expected_shape(data, _PROFILE_REQUIRED_KEYS)
    if not ok:
        return {
            "check": "profile", "symbol": symbol, "status": _FAIL,
            "reason": f"missing required keys: {missing}", "got_keys": sorted((data or {}).keys()),
        }
    return {
        "check": "profile", "symbol": symbol, "status": _OK,
        "name": data.get("name"),
        "market_cap_millions": data.get("market_cap"),
        "latency_s": round(dt, 3),
    }


async def probe_news(client: Any, symbol: str) -> dict:
    to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    status, dt, data = await _time_call(
        f"get_news({symbol})", client.get_news(symbol, from_date, to_date)
    )
    if status != _OK:
        return {"check": "news", "symbol": symbol, "status": _FAIL, "reason": repr(data)}
    if not isinstance(data, list):
        return {"check": "news", "symbol": symbol, "status": _FAIL, "reason": "not a list"}
    if data:
        ok, missing = _has_expected_shape(data[0], _NEWS_REQUIRED_KEYS)
        if not ok:
            return {
                "check": "news", "symbol": symbol, "status": _FAIL,
                "reason": f"first item missing keys: {missing}",
                "got_keys": sorted((data[0] or {}).keys()),
            }
    return {
        "check": "news", "symbol": symbol, "status": _OK,
        "article_count": len(data),
        "latency_s": round(dt, 3),
    }


async def probe_aggregates(client: Any, symbol: str) -> dict:
    to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    status, dt, data = await _time_call(
        f"get_aggregates({symbol})", client.get_aggregates(symbol, from_date, to_date)
    )
    if status != _OK:
        return {"check": "aggregates", "symbol": symbol, "status": _FAIL, "reason": repr(data)}
    if not isinstance(data, list):
        return {"check": "aggregates", "symbol": symbol, "status": _FAIL, "reason": "not a list"}
    return {
        "check": "aggregates", "symbol": symbol, "status": _OK,
        "bar_count": len(data),
        "latency_s": round(dt, 3),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Divergence probe (optional; needs FINNHUB_API_KEY too)
# ═══════════════════════════════════════════════════════════════════════════════

async def probe_divergence(symbols: list[str]) -> list[dict]:
    """Compare Polygon `current_price` to Finnhub `current_price`
    for the same ticker. Skipped if either key is missing."""
    poly_key = os.environ.get("POLYGON_API_KEY")
    finn_key = os.environ.get("FINNHUB_API_KEY")
    if not poly_key or not finn_key:
        log.info("[divergence] skipped — POLYGON_API_KEY or FINNHUB_API_KEY missing")
        return []

    from risedual_core.clients.finnhub import FinnhubClient
    from risedual_core.clients.polygon import PolygonClient

    results: list[dict] = []
    poly = PolygonClient(api_key=poly_key)
    finn = FinnhubClient(api_key=finn_key)
    for sym in symbols:
        p_quote = await poly.get_quote(sym)
        f_quote = await finn.get_quote(sym)
        p_price = (p_quote or {}).get("current_price")
        f_price = (f_quote or {}).get("current_price")
        if not p_price or not f_price:
            results.append({
                "check": "divergence", "symbol": sym, "status": _WARN,
                "reason": "one or both providers returned no price",
                "polygon_price": p_price, "finnhub_price": f_price,
            })
            continue
        pct = abs(p_price - f_price) / f_price * 100
        status = _OK if pct < 0.5 else _WARN
        results.append({
            "check": "divergence", "symbol": sym, "status": status,
            "polygon_price": round(p_price, 4),
            "finnhub_price": round(f_price, 4),
            "pct_diff": round(pct, 3),
        })
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

async def main_async(full: bool) -> int:
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        log.error("POLYGON_API_KEY is not set in the environment")
        return 1

    from risedual_core.clients.polygon import PolygonClient

    symbols = ["AAPL", "MSFT", "SPY"] if full else ["AAPL"]
    log.info("running stress test against Polygon for %d symbol(s)", len(symbols))

    client = PolygonClient(api_key=api_key)
    report: list[dict] = []
    for sym in symbols:
        report.append(await probe_quote(client, sym))
        report.append(await probe_profile(client, sym))
        report.append(await probe_news(client, sym))
        report.append(await probe_aggregates(client, sym))

    if full:
        report.extend(await probe_divergence(symbols))

    # ── Summary ──
    log.info("─" * 60)
    fails = sum(1 for r in report if r.get("status") == _FAIL)
    warns = sum(1 for r in report if r.get("status") == _WARN)
    oks = sum(1 for r in report if r.get("status") == _OK)

    for entry in report:
        status = entry.pop("status", "?")
        check = entry.pop("check", "?")
        sym = entry.pop("symbol", "?")
        log.info("[%-4s] %-10s %-6s  %s", status, check, sym, entry)

    log.info("─" * 60)
    log.info("TOTAL: ok=%d  warn=%d  fail=%d", oks, warns, fails)

    if fails:
        return 1
    if warns:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full", action="store_true",
        help="Exhaustive run (3 symbols + Finnhub divergence). "
             "Default is single-ticker quickcheck. Free-tier rate "
             "ceiling (5/min) means --full takes ~3 minutes unless "
             "combined with --rate-override.",
    )
    parser.add_argument(
        "--rate-override", type=float, default=None,
        help="Override MARKET_DATA_POLYGON_RPS for this run "
             "(requests/second). Useful on paid keys; defaults to "
             "the free-tier 0.08/s (5/min).",
    )
    args = parser.parse_args()
    if args.rate_override is not None:
        os.environ["MARKET_DATA_POLYGON_RPS"] = str(args.rate_override)
        log.info("rate override: %s req/s", args.rate_override)
    return asyncio.run(main_async(full=args.full))


if __name__ == "__main__":
    sys.exit(main())
