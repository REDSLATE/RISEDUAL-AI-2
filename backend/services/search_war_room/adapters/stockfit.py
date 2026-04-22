"""StockFit adapter — SEC EDGAR structured data for the Search War Room.

Uses StockFit API for financials, insider transactions, earnings, ownership,
and fund data. Provides fundamental data that price APIs don't cover.

API: https://api.stockfit.io/v1
Auth: Bearer fl_xxx
"""
import os
import asyncio
import httpx
import logging
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

logger = logging.getLogger(__name__)
TTL = 1800  # 30 min cache
BASE = "https://api.stockfit.io/v1"


def _get_key() -> str:
    return os.environ.get("STOCKFIT_API_KEY", "")


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


async def _safe_get(client: httpx.AsyncClient, url: str, params: dict, headers: dict) -> dict:
    """Safe GET that returns {} on any error."""
    try:
        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


async def run(query: str, symbol: str = None) -> EngineResult:
    """War Room adapter: pull financials + insider summary + earnings for a ticker."""
    key = _get_key()
    if not key:
        return EngineResult(engine="stockfit", status="skipped", source_type="fundamental",
                            query=query, error="missing_stockfit_api_key")
    if not symbol:
        return EngineResult(engine="stockfit", status="skipped", source_type="fundamental",
                            query=query, error="no_symbol_provided")

    ticker = symbol.upper()
    cache_key = f"warroom_{ticker}"
    cached = get_cached("stockfit", cache_key, TTL)
    if cached:
        return EngineResult(**cached)

    try:
        h = _headers(key)
        async with httpx.AsyncClient(timeout=15) as client:
            # Only hit endpoints available on the free plan (insider endpoints return 403)
            financials, earnings, scores, balance = await asyncio.gather(
                _safe_get(client, f"{BASE}/api/financials/income-statement",
                          {"symbol": ticker, "period": "annual", "limit": 2}, h),
                _safe_get(client, f"{BASE}/api/earnings/snapshot",
                          {"symbol": ticker}, h),
                _safe_get(client, f"{BASE}/api/financials/scores",
                          {"symbol": ticker}, h),
                _safe_get(client, f"{BASE}/api/financials/balance-sheet",
                          {"symbol": ticker, "period": "annual", "limit": 1}, h),
            )
        insider_summary = {}  # Not available on free plan

        items = []
        summary_parts = [f"{ticker} SEC Intelligence"]

        # Financials
        if isinstance(financials, list) and financials:
            facts = financials[0].get("facts", {})
            rev = facts.get("revenue", 0)
            net = facts.get("netIncome", 0)
            if rev:
                summary_parts.append(f"Rev: ${rev / 1e9:.1f}B")
            if net:
                summary_parts.append(f"Net: ${net / 1e9:.1f}B")
            if rev and net:
                summary_parts.append(f"Margin: {net / rev * 100:.1f}%")
            items.append({"type": "financials", "period": financials[0].get("period"), "data": {
                "revenue": rev, "netIncome": net,
                "grossProfit": facts.get("grossProfit", 0),
                "operatingIncome": facts.get("operatingIncome", 0),
                "eps": facts.get("eps", 0),
            }})

        # Insider summary (aggregated)
        if insider_summary and not insider_summary.get("error"):
            buys_3m = insider_summary.get("last3Months", {}).get("buyCount", 0)
            sells_3m = insider_summary.get("last3Months", {}).get("sellCount", 0)
            if buys_3m or sells_3m:
                summary_parts.append(f"Insiders(3m): {buys_3m}B/{sells_3m}S")
            items.append({"type": "insider_summary", "data": insider_summary})

        # Earnings snapshot
        if earnings and not earnings.get("error"):
            eps = earnings.get("eps")
            rev_growth = earnings.get("revenueGrowth")
            if eps:
                summary_parts.append(f"EPS: ${eps}")
            if rev_growth:
                summary_parts.append(f"RevGrowth: {rev_growth:.1f}%")
            items.append({"type": "earnings", "data": earnings})

        # Financial health scores
        if scores and not scores.get("error"):
            f_score = scores.get("piotroskiFScore")
            z_score = scores.get("altmanZScore")
            if f_score is not None:
                summary_parts.append(f"F-Score: {f_score}/9")
            if z_score is not None:
                zone = "safe" if z_score > 2.99 else "grey" if z_score > 1.81 else "distress"
                summary_parts.append(f"Z-Score: {z_score:.2f} ({zone})")
            items.append({"type": "scores", "data": scores})

        # Balance sheet highlights
        if isinstance(balance, list) and balance:
            facts = balance[0].get("facts", {})
            cash = facts.get("cash", 0)
            debt = facts.get("totalDebt", 0)
            equity = facts.get("totalEquity", 0)
            if cash:
                summary_parts.append(f"Cash: ${cash / 1e9:.1f}B")
            if debt:
                summary_parts.append(f"Debt: ${debt / 1e9:.1f}B")
            items.append({"type": "balance_sheet", "period": balance[0].get("period"), "data": {
                "cash": cash, "totalDebt": debt, "totalEquity": equity,
                "assets": facts.get("assets", 0),
            }})

        if len(summary_parts) <= 1:
            return EngineResult(engine="stockfit", status="error", source_type="fundamental",
                                query=query, error="no_data_returned")

        result = EngineResult(
            engine="stockfit", status="ok", source_type="fundamental", query=query,
            title=f"StockFit: {ticker} SEC Intelligence",
            summary=" | ".join(summary_parts),
            items=items, confidence=0.85, authoritative=True, cached=False,
        )
        set_cached("stockfit", cache_key, result.model_dump())
        return result

    except Exception as exc:
        logger.warning(f"StockFit adapter error: {exc}")
        return EngineResult(engine="stockfit", status="error", source_type="fundamental",
                            query=query, error=str(exc)[:100])


# ── Standalone endpoints for tools agent and research ──

async def get_financials(symbol: str, period: str = "annual", limit: int = 4) -> dict:
    key = _get_key()
    if not key:
        return {"error": "missing_stockfit_api_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE}/api/financials/income-statement",
                                    params={"symbol": symbol.upper(), "period": period, "limit": limit},
                                    headers=_headers(key))
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:100]}


async def get_insider_transactions(symbol: str, limit: int = 10) -> dict:
    key = _get_key()
    if not key:
        return {"error": "missing_stockfit_api_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE}/api/insider-transactions",
                                    params={"symbol": symbol.upper(), "pageSize": limit},
                                    headers=_headers(key))
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:100]}


async def get_insider_summary(symbol: str) -> dict:
    key = _get_key()
    if not key:
        return {"error": "missing_stockfit_api_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE}/api/insider-transactions/summary",
                                    params={"symbol": symbol.upper()},
                                    headers=_headers(key))
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:100]}


async def get_earnings(symbol: str) -> dict:
    key = _get_key()
    if not key:
        return {"error": "missing_stockfit_api_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE}/api/earnings/snapshot",
                                    params={"symbol": symbol.upper()},
                                    headers=_headers(key))
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:100]}


async def get_health_scores(symbol: str) -> dict:
    key = _get_key()
    if not key:
        return {"error": "missing_stockfit_api_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE}/api/financials/scores",
                                    params={"symbol": symbol.upper()},
                                    headers=_headers(key))
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:100]}


async def get_earnings_calendar(symbols: str) -> dict:
    key = _get_key()
    if not key:
        return {"error": "missing_stockfit_api_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE}/api/earnings/calendar",
                                    params={"symbols": symbols},
                                    headers=_headers(key))
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:100]}


async def get_fund_reverse_lookup(symbol: str) -> dict:
    key = _get_key()
    if not key:
        return {"error": "missing_stockfit_api_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE}/api/fund/reverse-lookup",
                                    params={"symbol": symbol.upper()},
                                    headers=_headers(key))
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:100]}
