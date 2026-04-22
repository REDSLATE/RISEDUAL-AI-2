"""War Room Service — unified stock intelligence with new data capabilities."""
import os
import logging
import asyncio
import requests
from datetime import datetime, timezone


from services.price_provider import get_overview_sync, get_quote_sync


def _av_key() -> str:
    return os.environ.get("ALPHA_VANTAGE_API_KEY", "")

from services.structured_log import log_error, log_warning

logger = logging.getLogger(__name__)


def _fh_key() -> str:
    return os.environ.get("FINNHUB_API_KEY", "")


def fetch_company_overview(symbol: str) -> dict:
    """Fetch company fundamentals via smart price provider (AV → yfinance)."""
    try:
        data = get_overview_sync(symbol)
        if data and "Symbol" in data:
            return {
                "name": data.get("Name", symbol),
                "sector": data.get("Sector", "N/A"),
                "industry": data.get("Industry", "N/A"),
                "market_cap": data.get("MarketCapitalization", "0"),
                "pe_ratio": data.get("PERatio", "N/A"),
                "forward_pe": data.get("ForwardPE", "N/A"),
                "peg_ratio": data.get("PEGRatio", "N/A"),
                "dividend_yield": data.get("DividendYield", "0"),
                "beta": data.get("Beta", "N/A"),
                "52_week_high": data.get("52WeekHigh", "N/A"),
                "52_week_low": data.get("52WeekLow", "N/A"),
                "50d_avg": data.get("50DayMovingAverage", "N/A"),
                "200d_avg": data.get("200DayMovingAverage", "N/A"),
                "profit_margin": data.get("ProfitMargin", "N/A"),
                "revenue_growth": data.get("QuarterlyRevenueGrowthYOY", "N/A"),
                "earnings_growth": data.get("QuarterlyEarningsGrowthYOY", "N/A"),
                "analyst_target": data.get("AnalystTargetPrice", "N/A"),
                "analyst_rating": data.get("AnalystRatingStrongBuy", "0"),
            }
        # Fallback: fetch quote data for ETFs/indices
        q = get_quote_sync(symbol)
        if q:
            return {
                "name": symbol.upper(),
                "sector": "ETF / Index",
                "industry": "Exchange-Traded Fund",
                "is_etf": True,
                "price": q["price"],
                "change_pct": f"{q['change_pct']}%",
                "day_high": q.get("high", 0),
                "day_low": q.get("low", 0),
                "prev_close": q.get("prev_close", 0),
                "market_cap": "N/A",
                "pe_ratio": "N/A",
                "beta": "N/A",
                "52_week_high": "N/A",
                "52_week_low": "N/A",
                "profit_margin": "N/A",
                "revenue_growth": "N/A",
                "analyst_target": "N/A",
            }
        return {}
    except Exception as e:
        log_error(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "war_room",
            "note": "Company overview error for <symbol>",
            "symbol": symbol,
        })
        return {}


def fetch_earnings_surprises(symbol: str) -> dict:
    """Fetch quarterly earnings history to detect surprise patterns.

    Returns a summary dict
    ``{quarters, beat_rate, current_streak, total_quarters}``.
    """
    try:
        r = requests.get("https://www.alphavantage.co/query", params={
            "function": "EARNINGS", "symbol": symbol, "apikey": _av_key()
        }, timeout=10)
        data = r.json()
        quarters = data.get("quarterlyEarnings", [])[:8]  # Last 8 quarters
        results = []
        beat_count = 0
        for q in quarters:
            reported = float(q.get("reportedEPS", 0) or 0)
            estimated = float(q.get("estimatedEPS", 0) or 0)
            surprise_pct = float(q.get("surprisePercentage", 0) or 0)
            beat = reported > estimated
            if beat:
                beat_count += 1
            results.append({
                "date": q.get("fiscalDateEnding", ""),
                "reported_eps": reported,
                "estimated_eps": estimated,
                "surprise": float(q.get("surprise", 0) or 0),
                "surprise_pct": surprise_pct,
                "beat": beat,
            })
        streak = 0
        for q in results:
            if q["beat"]:
                streak += 1
            else:
                break
        return {
            "quarters": results,
            "beat_rate": round(beat_count / max(len(results), 1) * 100),
            "current_streak": streak,
            "total_quarters": len(results),
        }
    except Exception as e:
        log_error(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "war_room",
            "note": "Earnings surprise error for <symbol>",
            "symbol": symbol,
        })
        return {"quarters": [], "beat_rate": 0, "current_streak": 0, "total_quarters": 0}


def fetch_insider_trades(symbol: str) -> dict:
    """Fetch insider transactions from Finnhub."""
    try:
        r = requests.get("https://finnhub.io/api/v1/stock/insider-transactions", params={
            "symbol": symbol, "token": _fh_key()
        }, timeout=10)
        data = r.json()
        trades = data.get("data", [])[:20]  # Last 20 transactions
        buy_volume = 0
        sell_volume = 0
        notable_trades = []
        for t in trades:
            shares = abs(int(t.get("share", 0) or 0))
            name = t.get("name", "Unknown")
            tx_type = (t.get("transactionType") or t.get("transactionCode", "")).upper()
            is_buy = tx_type in ("P", "A", "BUY", "P-PURCHASE", "PURCHASE")
            is_sell = tx_type in ("S", "S-SALE", "SELL", "SALE")
            # If no clear type, infer from change
            change = float(t.get("change", 0) or 0)
            if not is_buy and not is_sell:
                is_buy = change > 0
                is_sell = change < 0
            if is_buy:
                buy_volume += shares
            elif is_sell:
                sell_volume += shares
            notable_trades.append({
                "name": name,
                "date": t.get("transactionDate", ""),
                "shares": shares,
                "type": "buy" if is_buy else "sell" if is_sell else "other",
                "filing_date": t.get("filingDate", ""),
            })
        total = buy_volume + sell_volume
        return {
            "trades": notable_trades[:10],
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "net_sentiment": "bullish" if buy_volume > sell_volume else "bearish" if sell_volume > buy_volume else "neutral",
            "buy_ratio": round(buy_volume / max(total, 1) * 100),
        }
    except Exception as e:
        log_error(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "war_room",
            "note": "Insider trades error for <symbol>",
            "symbol": symbol,
        })
        return {"trades": [], "buy_volume": 0, "sell_volume": 0, "net_sentiment": "neutral", "buy_ratio": 50}


async def generate_war_room(symbol: str, api_key: str) -> dict:
    """Generate a complete War Room analysis using multi-agent AI crew."""
    from services.crew_definitions import run_war_room_crew

    # Fire all DATA fetches in parallel (no LLM calls yet)
    overview_task = asyncio.to_thread(fetch_company_overview, symbol)
    earnings_task = asyncio.to_thread(fetch_earnings_surprises, symbol)
    insider_task = asyncio.to_thread(fetch_insider_trades, symbol)

    gather_results: list = await asyncio.gather(
        overview_task, earnings_task, insider_task,
        return_exceptions=True,
    )
    raw_overview = gather_results[0]
    raw_earnings = gather_results[1]
    raw_insiders = gather_results[2]

    # Three-tier guard for `asyncio.gather(return_exceptions=True)` —
    # `asyncio.CancelledError` is a `BaseException` (not `Exception`)
    # and WILL land in the results during FastAPI request cancellation.
    # Using `isinstance(x, Exception)` here was a latent bug that would
    # crash downstream `overview.get(...)` calls with `AttributeError`.
    # See `services/fred_service.py` for the canonical comment.
    overview: dict = raw_overview if isinstance(raw_overview, dict) else {}
    if isinstance(raw_overview, BaseException) and not isinstance(
        raw_overview, asyncio.CancelledError
    ):
        log_error(logger, {
            "error": str(raw_overview),
            "type": type(raw_overview).__name__,
            "context": "war_room",
            "note": "War room overview error",
            "symbol": symbol,
        })

    earnings: dict = (
        raw_earnings if isinstance(raw_earnings, dict)
        else {"quarters": [], "beat_rate": 0, "current_streak": 0, "total_quarters": 0}
    )
    if isinstance(raw_earnings, BaseException) and not isinstance(
        raw_earnings, asyncio.CancelledError
    ):
        log_error(logger, {
            "error": str(raw_earnings),
            "type": type(raw_earnings).__name__,
            "context": "war_room",
            "note": "War room earnings error",
            "symbol": symbol,
        })

    insiders: dict = (
        raw_insiders if isinstance(raw_insiders, dict)
        else {"trades": [], "buy_volume": 0, "sell_volume": 0,
              "net_sentiment": "neutral", "buy_ratio": 50}
    )
    if isinstance(raw_insiders, BaseException) and not isinstance(
        raw_insiders, asyncio.CancelledError
    ):
        log_error(logger, {
            "error": str(raw_insiders),
            "type": type(raw_insiders).__name__,
            "context": "war_room",
            "note": "War room insiders error",
            "symbol": symbol,
        })

    # Run multi-agent crew (3 parallel agents + 1 synthesizer = ~20s)
    try:
        crew_result = await run_war_room_crew(
            symbol, overview, earnings, insiders, api_key
        )
        comp_score = max(0, min(100, int(crew_result.get("composite_score", 50))))
        verdict = crew_result.get("verdict", "HOLD")
        composite = {
            "score": comp_score,
            "verdict": verdict,
            "confidence": crew_result.get("confidence", 50),
            "key_thesis": crew_result.get("key_thesis", ""),
            "bull_case": crew_result.get("bull_case", ""),
            "bear_case": crew_result.get("bear_case", ""),
            "catalysts": crew_result.get("catalysts", []),
            "risks": crew_result.get("risks", []),
            "price_target_short": crew_result.get("price_target_short", "N/A"),
            "price_target_medium": crew_result.get("price_target_medium", "N/A"),
            "trade_recommendation": crew_result.get("trade_recommendation", ""),
            "breakdown": {
                "fundamental_score": crew_result.get("fundamental_score", 50),
                "technical_score": crew_result.get("technical_score", 50),
                "sentiment_score": crew_result.get("sentiment_score", 50),
            },
            "multi_agent": True,
            "agents_used": crew_result.get("agents_used", 4),
            "agent_analyses": crew_result.get("agent_analyses", []),
        }
    except Exception as e:
        log_error(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "war_room",
            "note": "War room crew failed, falling back",
        })
        ed = earnings if isinstance(earnings, dict) else {}
        ind = insiders if isinstance(insiders, dict) else {}
        br = ed.get("beat_rate", 50) if ed.get("total_quarters", 0) > 0 else 50
        ir = ind.get("buy_ratio", 50) if len(ind.get("trades", [])) > 0 else 50
        cs = round(br * 0.5 + ir * 0.5)
        cv = "STRONG BUY" if cs >= 70 else "BUY" if cs >= 55 else "HOLD" if cs >= 45 else "SELL" if cs >= 30 else "STRONG SELL"
        composite = {"score": cs, "verdict": cv, "multi_agent": False,
                     "breakdown": {"earnings_weight": round(br * 0.5), "insider_weight": round(ir * 0.5)}}

    return {
        "symbol": symbol.upper(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": overview if isinstance(overview, dict) else {},
        "earnings": earnings if isinstance(earnings, dict) else {},
        "insiders": insiders if isinstance(insiders, dict) else {},
        "composite": composite,
    }
