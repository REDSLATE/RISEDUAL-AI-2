"""War Room Service — unified stock intelligence with new data capabilities."""
import os
import logging
import asyncio
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _av_key():
    return os.environ.get("ALPHA_VANTAGE_API_KEY", "")


def _fh_key():
    return os.environ.get("FINNHUB_API_KEY", "")


def fetch_company_overview(symbol: str) -> Dict:
    """Fetch company fundamentals and sector data from Alpha Vantage."""
    try:
        r = requests.get("https://www.alphavantage.co/query", params={
            "function": "OVERVIEW", "symbol": symbol, "apikey": _av_key()
        }, timeout=10)
        data = r.json()
        if not data or "Symbol" not in data:
            return {}
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
    except Exception as e:
        logger.error(f"Company overview error for {symbol}: {e}")
        return {}


def fetch_earnings_surprises(symbol: str) -> List[Dict]:
    """Fetch quarterly earnings history to detect surprise patterns."""
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
        logger.error(f"Earnings surprise error for {symbol}: {e}")
        return {"quarters": [], "beat_rate": 0, "current_streak": 0, "total_quarters": 0}


def fetch_insider_trades(symbol: str) -> Dict:
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
        logger.error(f"Insider trades error for {symbol}: {e}")
        return {"trades": [], "buy_volume": 0, "sell_volume": 0, "net_sentiment": "neutral", "buy_ratio": 50}


async def generate_war_room(symbol: str, api_key: str) -> Dict:
    """Generate a complete War Room analysis for a symbol."""
    from services.ai_intelligence_service import generate_ai_score, detect_patterns, generate_quick_brief

    # Fire all data fetches in parallel
    overview_task = asyncio.to_thread(fetch_company_overview, symbol)
    earnings_task = asyncio.to_thread(fetch_earnings_surprises, symbol)
    insider_task = asyncio.to_thread(fetch_insider_trades, symbol)
    score_task = generate_ai_score(api_key, symbol)
    patterns_task = detect_patterns(api_key, symbol)
    brief_task = generate_quick_brief(api_key, symbol)

    overview, earnings, insiders, score, patterns, brief = await asyncio.gather(
        overview_task, earnings_task, insider_task,
        score_task, patterns_task, brief_task,
        return_exceptions=True,
    )

    # Handle exceptions gracefully
    if isinstance(overview, Exception):
        logger.error(f"War room overview error: {overview}")
        overview = {}
    if isinstance(earnings, Exception):
        logger.error(f"War room earnings error: {earnings}")
        earnings = {"quarters": [], "beat_rate": 0, "current_streak": 0, "total_quarters": 0}
    if isinstance(insiders, Exception):
        logger.error(f"War room insiders error: {insiders}")
        insiders = {"trades": [], "buy_volume": 0, "sell_volume": 0, "net_sentiment": "neutral", "buy_ratio": 50}
    if isinstance(score, Exception):
        logger.error(f"War room score error: {score}")
        score = {"scores": {}}
    if isinstance(patterns, Exception):
        logger.error(f"War room patterns error: {patterns}")
        patterns = {"patterns": []}
    if isinstance(brief, Exception):
        logger.error(f"War room brief error: {brief}")
        brief = {"brief": {}}

    # Compute composite signal
    scores = score.get("scores", {}) if isinstance(score, dict) else {}
    overall = scores.get("overall_score", 5)
    beat_rate = earnings.get("beat_rate", 50) if isinstance(earnings, dict) else 50

    # Composite signal: weight score (40%), earnings momentum (30%), insider sentiment (30%)
    score_signal = (overall / 10) * 100
    earnings_signal = beat_rate
    insider_signal = insiders.get("buy_ratio", 50) if isinstance(insiders, dict) else 50
    composite = round(score_signal * 0.4 + earnings_signal * 0.3 + insider_signal * 0.3)

    if composite >= 70:
        composite_verdict = "STRONG BUY"
    elif composite >= 55:
        composite_verdict = "BUY"
    elif composite >= 45:
        composite_verdict = "HOLD"
    elif composite >= 30:
        composite_verdict = "SELL"
    else:
        composite_verdict = "STRONG SELL"

    return {
        "symbol": symbol.upper(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": overview if isinstance(overview, dict) else {},
        "ai_score": scores,
        "patterns": patterns.get("patterns", []) if isinstance(patterns, dict) else [],
        "brief": brief.get("brief", {}) if isinstance(brief, dict) else {},
        "earnings": earnings if isinstance(earnings, dict) else {},
        "insiders": insiders if isinstance(insiders, dict) else {},
        "composite": {
            "score": composite,
            "verdict": composite_verdict,
            "breakdown": {
                "ai_score_weight": round(score_signal * 0.4),
                "earnings_weight": round(earnings_signal * 0.3),
                "insider_weight": round(insider_signal * 0.3),
            },
        },
    }
