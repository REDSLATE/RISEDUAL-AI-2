"""Financial Tools — schemas, prompts, and tool implementations.

Separated from the agent state machine so tools can be extended
independently without touching the graph logic.
"""
import json
import logging
import asyncio
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  TOOL SCHEMAS (OpenAI function calling format)
# ─────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "calculate_compound_growth",
            "description": "Calculate future value of an investment using annual compounding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "principal": {"type": "number", "description": "Starting investment amount in dollars"},
                    "annual_rate_pct": {"type": "number", "description": "Annual growth rate as a percent, e.g. 8 for 8%"},
                    "years": {"type": "integer", "description": "Number of years"},
                },
                "required": ["principal", "annual_rate_pct", "years"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_cagr",
            "description": "Calculate compound annual growth rate (CAGR) from start and end values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_value": {"type": "number", "description": "Beginning value"},
                    "end_value": {"type": "number", "description": "Ending value"},
                    "years": {"type": "number", "description": "Elapsed years"},
                },
                "required": ["start_value", "end_value", "years"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "Get current real-time stock price, change, volume for a ticker symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker symbol, e.g. AAPL, MSFT, TSLA"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current financial data, news, growth rates, or market information. Use this to find defensible annualized rates or historical performance data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_history",
            "description": "Get daily OHLCV price history for a stock to derive historical returns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker symbol"},
                    "period": {"type": "string", "description": "compact (3 months) or full (20 years)", "enum": ["compact", "full"]},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sec_fundamentals",
            "description": "Get SEC EDGAR fundamental data for a stock. Types: 'financials' (income statement), 'insiders' (recent insider trades), 'insider_summary' (aggregated buy/sell stats 3/6/12 months), 'earnings' (EPS snapshot), 'scores' (Piotroski F-Score, Altman Z-Score), 'earnings_calendar' (upcoming dates), 'fund_holders' (which ETFs hold this stock).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker symbol"},
                    "data_type": {"type": "string", "description": "Type of SEC data",
                                  "enum": ["financials", "insiders", "insider_summary", "earnings", "scores", "earnings_calendar", "fund_holders"]},
                },
                "required": ["symbol"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a precise financial research assistant inside the RISEDUAL AI trading platform. "
    "Use tools for all math and external data lookups — never guess numbers. "
    "Do not guess annual growth rates. "
    "If a projection requires a growth rate and none is given by the user, you MUST either: "
    "(a) derive one from evidence using tools (historical CAGR via get_daily_history, "
    "analyst estimates via web_search), or "
    "(b) clearly state that the rate is not available and explain what data is missing. "
    "When deriving a rate, show your work: cite the source data, time period, and calculation. "
    "Present results with clear markdown formatting. "
    "Always remind users that past performance does not guarantee future results."
)

MAX_STEPS = 8


# ─────────────────────────────────────────────
#  TOOL IMPLEMENTATIONS
# ─────────────────────────────────────────────

def _exec_compound_growth(principal: float, annual_rate_pct: float, years: int) -> dict:
    fv = principal * ((1 + annual_rate_pct / 100) ** years)
    return {
        "principal": round(principal, 2),
        "annual_rate_pct": round(annual_rate_pct, 4),
        "years": years,
        "future_value": round(fv, 2),
        "gain": round(fv - principal, 2),
    }


def _exec_cagr(start_value: float, end_value: float, years: float) -> dict:
    cagr = ((end_value / start_value) ** (1 / years) - 1) * 100
    return {
        "start_value": round(start_value, 2),
        "end_value": round(end_value, 2),
        "years": years,
        "cagr_pct": round(cagr, 4),
    }


async def _exec_stock_quote(symbol: str) -> dict:
    try:
        from services.price_provider import get_quote
        quote = await get_quote(symbol)
        if quote:
            return {
                "symbol": symbol.upper(),
                "price": quote.get("price"),
                "change": quote.get("change"),
                "change_pct": quote.get("change_pct"),
                "volume": quote.get("volume"),
                "prev_close": quote.get("prev_close"),
                "high": quote.get("high"),
                "low": quote.get("low"),
                "source": quote.get("source", "pool"),
            }
        return {"error": f"No quote data found for {symbol}"}
    except Exception as e:
        return {"error": f"Quote lookup failed: {str(e)[:100]}"}


async def _exec_daily_history(symbol: str, period: str = "compact") -> dict:
    """Get price history to derive historical CAGR."""
    try:
        from services.price_provider import get_daily_history
        data = await get_daily_history(symbol, period)
        if not data or len(data) < 2:
            return {"error": f"No history for {symbol}"}
        newest = data[0]
        oldest = data[-1]
        days = len(data)
        years = days / 252  # trading days
        start_price = oldest["close"]
        end_price = newest["close"]
        cagr = ((end_price / start_price) ** (1 / years) - 1) * 100 if years > 0 else 0
        return {
            "symbol": symbol.upper(),
            "period_days": days,
            "period_years": round(years, 2),
            "start_date": oldest["date"],
            "end_date": newest["date"],
            "start_price": start_price,
            "end_price": end_price,
            "historical_cagr_pct": round(cagr, 2),
            "total_return_pct": round((end_price / start_price - 1) * 100, 2),
        }
    except Exception as e:
        return {"error": f"History fetch failed: {str(e)[:100]}"}


async def _exec_sec_fundamentals(symbol: str, data_type: str = "financials") -> dict:
    """Get SEC EDGAR data via StockFit API."""
    try:
        from services.search_war_room.adapters.stockfit import (
            get_financials, get_insider_transactions, get_insider_summary,
            get_earnings, get_health_scores, get_earnings_calendar,
            get_fund_reverse_lookup,
        )
        key = os.environ.get("STOCKFIT_API_KEY", "")
        if not key:
            return {"error": "StockFit API key not configured"}

        dispatch = {
            "financials": lambda: get_financials(symbol),
            "insiders": lambda: get_insider_transactions(symbol),
            "insider_summary": lambda: get_insider_summary(symbol),
            "earnings": lambda: get_earnings(symbol),
            "scores": lambda: get_health_scores(symbol),
            "earnings_calendar": lambda: get_earnings_calendar(symbol),
            "fund_holders": lambda: get_fund_reverse_lookup(symbol),
        }
        handler = dispatch.get(data_type)
        if handler:
            return await handler()
        return {"error": f"Unknown data_type: {data_type}"}
    except Exception as e:
        return {"error": f"SEC fundamentals failed: {str(e)[:100]}"}


async def _exec_web_search(query: str) -> dict:
    """Search with Tavily (advanced) -> DDG fallback."""
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    if tavily_key:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": tavily_key,
                        "query": query,
                        "search_depth": "advanced",
                        "max_results": 4,
                        "include_answer": True,
                        "topic": "finance",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "query": query,
                        "answer": data.get("answer", ""),
                        "source": "tavily",
                        "results": [
                            {"title": r.get("title", ""), "body": r.get("content", ""), "url": r.get("url", ""),
                             "score": r.get("score", 0)}
                            for r in data.get("results", [])
                        ],
                    }
        except Exception as e:
            logger.debug(f"Tavily search failed, falling back to DDG: {e}")

    try:
        from ddgs import DDGS
        results = await asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=4))
        )
        return {
            "query": query,
            "source": "duckduckgo",
            "results": [
                {"title": r.get("title", ""), "body": r.get("body", ""), "url": r.get("href", "")}
                for r in results
            ],
        }
    except Exception as e:
        return {"query": query, "error": f"Search failed: {str(e)[:100]}", "results": []}


# ─────────────────────────────────────────────
#  TOOL DISPATCHER
# ─────────────────────────────────────────────

async def run_tool(name: str, args: dict) -> str:
    """Execute a tool by name and return JSON string result."""
    if name == "calculate_compound_growth":
        result = _exec_compound_growth(**args)
    elif name == "calculate_cagr":
        result = _exec_cagr(**args)
    elif name == "get_stock_quote":
        result = await _exec_stock_quote(args["symbol"])
    elif name == "get_daily_history":
        result = await _exec_daily_history(args["symbol"], args.get("period", "compact"))
    elif name == "web_search":
        result = await _exec_web_search(args["query"])
    elif name == "get_sec_fundamentals":
        result = await _exec_sec_fundamentals(args["symbol"], args.get("data_type", "financials"))
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)
