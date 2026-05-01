"""AI-Powered Post-Mortem Analysis — Classifies WHY predictions failed using news context + LLM.

When a prediction is verified as wrong, this service:
1. Fetches recent news headlines for the ticker (Finnhub company news)
2. Sends context to the LLM to classify the failure reason
3. Updates both MongoDB and ChromaDB with the AI-classified failure mode

This replaces the heuristic-based _classify_failure() with a much more accurate,
news-aware classification that distinguishes MACRO_SHOCK from TECH_FAKEOUT.
"""

import os
import json
import logging
import asyncio
from typing import Any, cast
from datetime import datetime, timezone

from chromadb.types import UpdateMetadata

from services.structured_log import log_error, log_warning

logger = logging.getLogger(__name__)

FAILURE_MODES = {
    "TECH_FAKEOUT": "Indicators were bullish but price reversed immediately (Stop-loss hunt).",
    "MACRO_SHOCK": "Unexpected news/data (CPI, Fed, etc.) invalidated the setup.",
    "LIQUIDITY_GAP": "Low volume caused slippage or erratic price spikes.",
    "REGIME_SHIFT": "Market shifted from trending to range-bound unexpectedly.",
    "UNKNOWN": "Price moved against prediction without clear technical or news trigger.",
}

_POST_MORTEM_PROMPT = """You are a senior trading analyst conducting a post-mortem on a failed AI prediction.

PREDICTION DETAILS:
- Ticker: {ticker}
- Direction: {direction} (predicted the price would go {direction_meaning})
- Price at prediction: ${price_at:.2f}
- Price at verification: ${price_now:.2f}
- Move: {pct_change:+.2f}%
- Confidence was: {confidence}%
- Heuristic classification: {heuristic_code}

RECENT NEWS HEADLINES (from around the prediction period):
{news_headlines}

YOUR TASK:
Analyze the news headlines and price action to determine the MOST LIKELY reason this prediction failed. Choose exactly ONE code from:

- TECH_FAKEOUT: No significant news caused the reversal. The price simply reversed against technical signals (e.g., stop-loss hunting, false breakout, divergence ignored).
- MACRO_SHOCK: A specific news event, data release, or macro development caused the price to move against the prediction (e.g., CPI surprise, Fed decision, earnings miss, geopolitical event).
- LIQUIDITY_GAP: The price moved erratically due to low liquidity, large spread, or thin order book — not a genuine market move.
- REGIME_SHIFT: The broader market sentiment shifted (bull→bear or trend→range) making the directional prediction obsolete. No single event, but a gradual change in market character.
- UNKNOWN: Insufficient data to determine the cause. Use this only if none of the above clearly apply.

Respond with ONLY valid JSON:
{{
  "failure_code": "TECH_FAKEOUT | MACRO_SHOCK | LIQUIDITY_GAP | REGIME_SHIFT | UNKNOWN",
  "reasoning": "2-3 sentence explanation of why you chose this classification",
  "key_headline": "The most relevant news headline that contributed to the failure (or 'None' if TECH_FAKEOUT)"
}}"""


async def _fetch_ticker_news(ticker: str) -> list:
    """Fetch recent news for a ticker using Finnhub."""
    try:
        from services.finnhub_service import FinnhubService
        service = FinnhubService()
        articles = await service.get_company_news(ticker)
        return articles[:10]
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "post_mortem",
            "note": "Post-mortem news fetch failed for <ticker>",
            "ticker": ticker,
        })
        return []


async def _fetch_market_news() -> list:
    """Fetch general market news as fallback."""
    try:
        from services.financial_scraping_service import FinancialScrapingService
        service = FinancialScrapingService()
        articles = await service.scrape_financial_news()
        return articles[:10]
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "post_mortem",
            "note": "Post-mortem market news fetch failed",
        })
        return []


async def _llm_classify(prompt: str) -> dict:
    """Send the post-mortem prompt to the LLM and parse the response."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        logger.warning("No EMERGENT_LLM_KEY — skipping AI post-mortem")
        return {}

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=api_key,
            session_id=f"postmortem_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            system_message="You are a trading post-mortem analyst. Respond only with valid JSON.",
        ).with_model("openai", "gpt-4o-mini")

        response = await chat.send_message(UserMessage(text=prompt))

        text = str(response).strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        result = json.loads(text)
        code = result.get("failure_code", "UNKNOWN").upper()
        if code not in FAILURE_MODES:
            code = "UNKNOWN"
        result["failure_code"] = code
        return result
    except json.JSONDecodeError as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "post_mortem",
            "note": "AI post-mortem JSON parse failed",
        })
        return {}
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "post_mortem",
            "note": "AI post-mortem LLM call failed",
        })
        return {}


async def run_post_mortem(
    prediction: dict,
    price_now: float,
    heuristic_code: str = "UNKNOWN",
) -> dict:
    """Run an AI-powered post-mortem on a failed prediction.

    Args:
        prediction: The prediction doc from MongoDB (symbol, direction, confidence, price_at_prediction, etc.)
        price_now: Current price at verification time
        heuristic_code: The initial heuristic classification from _classify_failure()

    Returns:
        Dict with failure_code, reasoning, key_headline, and source='ai_post_mortem'
    """
    ticker = prediction.get("symbol", "UNKNOWN")
    direction = prediction.get("direction", "UNKNOWN")
    price_at = prediction.get("price_at_prediction", 0)
    confidence = prediction.get("confidence", 0)

    if price_at <= 0:
        return {"failure_code": heuristic_code, "source": "heuristic", "reasoning": "No price data"}

    pct_change = (price_now - price_at) / price_at * 100

    # Direction meaning for prompt clarity. Routes through the
    # central canonicaliser so STRONG_*/WEAK_* tokens don't fall to
    # the "stay flat" branch (same bug class as the 2026-05-01
    # direction-token cleanup — see prediction_tracker.canonical_ai_dir).
    from services.prediction_tracker import canonical_ai_dir
    canon = canonical_ai_dir(direction)
    if canon == "LONG":
        direction_meaning = "up"
    elif canon == "SHORT":
        direction_meaning = "down"
    else:
        direction_meaning = "stay flat"

    # Fetch news — ticker-specific first, fallback to market news
    articles = await _fetch_ticker_news(ticker)
    if len(articles) < 3:
        market_news = await _fetch_market_news()
        articles.extend(market_news)

    # Format headlines for the prompt
    if articles:
        headlines = []
        for i, a in enumerate(articles[:12], 1):
            headline = a.get("headline") or a.get("title", "N/A")
            source = a.get("source", "Unknown")
            headlines.append(f"{i}. [{source}] {headline}")
        news_text = "\n".join(headlines)
    else:
        news_text = "(No recent news available — classify based on price action alone)"

    prompt = _POST_MORTEM_PROMPT.format(
        ticker=ticker,
        direction=direction,
        direction_meaning=direction_meaning,
        price_at=price_at,
        price_now=price_now,
        pct_change=pct_change,
        confidence=confidence,
        heuristic_code=heuristic_code,
        news_headlines=news_text,
    )

    result = await _llm_classify(prompt)

    if not result or "failure_code" not in result:
        # LLM failed — fall back to heuristic
        return {
            "failure_code": heuristic_code,
            "reasoning": FAILURE_MODES.get(heuristic_code, ""),
            "key_headline": None,
            "source": "heuristic",
        }

    result["source"] = "ai_post_mortem"
    result["heuristic_code"] = heuristic_code
    logger.info(
        f"AI Post-Mortem for {ticker}: {result['failure_code']} "
        f"(heuristic was {heuristic_code}) — {result.get('reasoning', '')[:100]}"
    )
    return result


async def run_and_update_post_mortem(
    db: Any, prediction: dict, price_now: float, heuristic_code: str = "UNKNOWN"
) -> dict:
    """Run AI post-mortem AND update MongoDB + ChromaDB with the result.

    This is the main entry point — call this from verify_pending_predictions().
    """
    result = await run_post_mortem(prediction, price_now, heuristic_code)
    code = result.get("failure_code", heuristic_code)
    reason = result.get("reasoning") or FAILURE_MODES.get(code, "")

    # Update MongoDB prediction
    pred_id = prediction.get("prediction_id")
    if pred_id and db is not None:
        update_fields = {}
        if prediction.get("verified_24h") is not None:
            update_fields["verified_24h.failure_code"] = code
            update_fields["verified_24h.failure_reason"] = reason
            update_fields["verified_24h.post_mortem"] = result
        await db.predictions.update_one(
            {"prediction_id": pred_id},
            {"$set": update_fields},
        )

    # Update ChromaDB metadata
    try:
        from services.market_memory_service import _collection, _make_id
        if _collection:
            # Mirror the v1/v2 id contract from market_memory_service —
            # don't hand-roll the hash here. Previously this site
            # built the id from ``prediction.get('timestamp', '')[:10]``
            # which raised TypeError when ``timestamp`` was a Mongo
            # BSON ``datetime``, then quietly fell into the
            # except-block and never updated Chroma. ``_make_id``
            # takes the same regime dict shape and routes through
            # ``to_iso_date`` so the id is deterministic regardless
            # of the timestamp's storage type.
            doc_id = _make_id({
                "symbol": prediction.get("symbol"),
                "date": prediction.get("timestamp"),
                "price": prediction.get("price_at_prediction"),
                "prediction_id": prediction.get("prediction_id"),
            })
            existing = await asyncio.to_thread(_collection.get, ids=[doc_id])
            if existing and existing.get("ids"):
                # ChromaDB stubs type metadatas entries as Mapping
                # (no .copy); runtime returns plain dicts. dict()
                # mirrors runtime `.copy()`, and cast() preserves
                # mypy's structural check against UpdateMetadata.
                meta: dict[str, Any] = dict(existing["metadatas"][0])
                meta["failure_code"] = code
                await asyncio.to_thread(
                    _collection.update,
                    ids=[doc_id],
                    metadatas=cast(list[UpdateMetadata], [meta]),
                )
    except Exception as e:
        from services.mongo_chroma_sync_metrics import record_skip
        record_skip(
            "post_mortem_chroma_update_failed",
            doc_id=str(prediction.get("prediction_id") or ""),
            exc=e,
        )
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "post_mortem",
            "note": "ChromaDB post-mortem update failed",
        })

    # Log the post-mortem
    if db is not None:
        try:
            await db.post_mortem_log.insert_one({
                "prediction_id": pred_id,
                "ticker": prediction.get("symbol"),
                "failure_code": code,
                "heuristic_code": heuristic_code,
                "reasoning": reason,
                "key_headline": result.get("key_headline"),
                "source": result.get("source", "heuristic"),
                "price_at": prediction.get("price_at_prediction"),
                "price_now": price_now,
                "run_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            log_warning(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "post_mortem",
                "note": "Post-mortem log save failed",
            })

    # Push to SSE stream
    try:
        from routes.stream import push_event
        push_event("post_mortem", {
            "ticker": prediction.get("symbol"),
            "failure_code": code,
            "heuristic_code": heuristic_code,
            "reasoning": reason,
            "key_headline": result.get("key_headline"),
            "source": result.get("source", "heuristic"),
        })
    except Exception:
        pass

    return result
