"""StockFit adapter — SEC EDGAR structured data for the Search War Room.

Uses StockFit API for ownership intelligence, financials, research summaries,
and insider transactions. Provides fundamental data that price APIs don't cover.

API: https://api.stockfit.io/v1
Auth: Bearer fl_xxx
"""
import os
import httpx
import logging
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached

logger = logging.getLogger(__name__)
TTL = 1800  # 30 min cache — SEC data doesn't change fast
BASE = "https://api.stockfit.io/v1"


def _get_key() -> str:
    return os.environ.get("STOCKFIT_API_KEY", "")


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


async def run(query: str, symbol: str = None):
    """War Room adapter: pull research summary + ownership for a ticker."""
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
        async with httpx.AsyncClient(timeout=15) as client:
            # Parallel: financials + insider transactions (available on free tier)
            import asyncio
            financials_task = client.get(f"{BASE}/api/financials/income-statement",
                                         params={"symbol": ticker, "period": "annual", "limit": 2},
                                         headers=_headers(key))
            insiders_task = client.get(f"{BASE}/api/ownership/insider-transactions",
                                       params={"symbol": ticker, "pageSize": 5},
                                       headers=_headers(key))

            financials_resp, insiders_resp = await asyncio.gather(
                financials_task, insiders_task, return_exceptions=True
            )

        items = []
        summary_parts = [f"{ticker} SEC Data"]

        # Parse financials
        if not isinstance(financials_resp, Exception) and financials_resp.status_code == 200:
            fin_data = financials_resp.json()
            if isinstance(fin_data, list) and fin_data:
                latest = fin_data[0]
                facts = latest.get("facts", {})
                rev = facts.get("revenue", 0)
                net = facts.get("netIncome", 0)
                if rev:
                    rev_b = rev / 1e9
                    summary_parts.append(f"Revenue: ${rev_b:.1f}B")
                if net:
                    net_b = net / 1e9
                    summary_parts.append(f"Net Income: ${net_b:.1f}B")
                if rev and net:
                    margin = (net / rev * 100)
                    summary_parts.append(f"Margin: {margin:.1f}%")
                items.append({"type": "financials", "period": latest.get("period"), "data": {
                    "revenue": rev, "netIncome": net,
                    "grossProfit": facts.get("grossProfit", 0),
                    "operatingIncome": facts.get("operatingIncome", 0),
                    "eps": facts.get("eps", 0),
                }})

        # Parse insider transactions
        if not isinstance(insiders_resp, Exception) and insiders_resp.status_code == 200:
            insider_data = insiders_resp.json()
            txns = insider_data if isinstance(insider_data, list) else insider_data.get("transactions", [])
            if txns:
                buys = sum(1 for t in txns if t.get("transactionType", "").lower() in ("purchase", "buy", "p"))
                sells = sum(1 for t in txns if t.get("transactionType", "").lower() in ("sale", "sell", "s"))
                summary_parts.append(f"Insiders: {buys} buys, {sells} sells (recent)")
                items.append({"type": "insiders", "data": [
                    {"name": t.get("ownerName", ""), "type": t.get("transactionType", ""),
                     "shares": t.get("sharesTraded", 0), "value": t.get("value", 0),
                     "date": t.get("transactionDate", "")}
                    for t in txns[:5]
                ]})

        if not summary_parts:
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


async def get_financials(symbol: str, period: str = "annual", limit: int = 4) -> dict:
    """Standalone: pull income statement for research/prediction."""
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
    """Standalone: pull insider transactions."""
    key = _get_key()
    if not key:
        return {"error": "missing_stockfit_api_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE}/api/ownership/insider-transactions",
                                    params={"symbol": symbol.upper(), "pageSize": limit},
                                    headers=_headers(key))
            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:100]}
