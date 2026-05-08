"""Alpha Vantage News Sentiment adapter — ticker-specific sentiment-scored news.

Uses the AV NEWS_SENTIMENT endpoint which returns headlines with overall_sentiment_score.
"""
import httpx
from services.search_war_room.schemas import EngineResult
from services.search_war_room.cache import get_cached, set_cached
from services.key_rotator import KeyRotator

TTL = 600
av_rotator = KeyRotator("ALPHA_VANTAGE_API_KEY")


async def run(query: str, symbol: str = None) -> EngineResult:
    import os
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY") or os.environ.get("ALPHAVANTAGEAPIKEY", "")
    if not api_key:
        return EngineResult(engine="av_news", status="skipped", source_type="news", query=query, error="missing_key")

    ticker = (symbol or query.split()[0]).upper()
    cache_key = f"{ticker}_{query[:30]}"
    cached = get_cached("av_news", cache_key, TTL)
    if cached:
        return EngineResult(**cached)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://www.alphavantage.co/query", params={
                "function": "NEWS_SENTIMENT",
                "tickers": ticker,
                "limit": 5,
                "apikey": api_key,
            })
            if resp.status_code != 200:
                return EngineResult(engine="av_news", status="error", source_type="news", query=query, error=f"http_{resp.status_code}")

            data = resp.json()
            if "Note" in data or "Information" in data:
                return EngineResult(engine="av_news", status="error", source_type="news", query=query, error="rate_limited")

            feed = data.get("feed", [])[:5]
            if not feed:
                return EngineResult(engine="av_news", status="ok", source_type="news", query=query,
                                    title=f"AV News: {ticker}", summary="No news found", items=[], confidence=0.3)

            items = []
            for row in feed:
                score = float(row.get("overall_sentiment_score", 0) or 0)
                items.append({
                    "title": row.get("title", ""),
                    "url": row.get("url", ""),
                    "snippet": row.get("summary", "")[:200],
                    "sentiment_score": score,
                    "sentiment_label": row.get("overall_sentiment_label", "Neutral"),
                    "published_at": row.get("time_published"),
                    "source": row.get("source", ""),
                })

            avg_sentiment = sum(i["sentiment_score"] for i in items) / len(items) if items else 0
            sentiment_word = "bullish" if avg_sentiment > 0.15 else "bearish" if avg_sentiment < -0.15 else "neutral"

            result = EngineResult(
                engine="av_news", status="ok", source_type="news", query=query,
                title=f"AV News Sentiment: {ticker}",
                summary=f"{len(items)} articles, avg sentiment {sentiment_word} ({avg_sentiment:.2f})",
                items=items, confidence=0.75, authoritative=False, cached=False,
            )
            set_cached("av_news", cache_key, result.model_dump())
            return result

    except Exception as exc:
        return EngineResult(engine="av_news", status="error", source_type="news", query=query, error=str(exc)[:100])
