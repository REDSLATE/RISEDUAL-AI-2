"""Market Memory Service — Vector memory for market regime pattern-matching.

Stores past market 'episodes' (price action, macro data, AI predictions, actual results)
as vector embeddings in ChromaDB. Before each prediction, queries for similar historical
regimes to inject as context, dramatically improving prediction accuracy.

Uses ChromaDB's built-in all-MiniLM-L6-v2 model for local embedding (zero API cost).
"""
import os
import json
import logging
import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional

import chromadb

logger = logging.getLogger(__name__)

CHROMA_DIR = "/app/backend/data/chromadb"
COLLECTION_NAME = "market_regimes"

_client: Optional[chromadb.PersistentClient] = None
_collection = None
_db = None  # MongoDB reference for stats


def init_memory(mongo_db=None):
    """Initialize ChromaDB with built-in embedding model."""
    global _client, _collection, _db
    os.makedirs(CHROMA_DIR, exist_ok=True)
    _client = chromadb.PersistentClient(path=CHROMA_DIR)
    _collection = _client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    _db = mongo_db
    count = _collection.count()
    logger.info(f"Market Memory initialized: {count} episodes stored in ChromaDB (all-MiniLM-L6-v2)")


def _regime_to_text(regime: Dict) -> str:
    """Convert a market regime dict into a natural-language description for embedding.
    
    Supports both flat keys and structured {metrics, sentiment} format.
    """
    lines = []
    # Core identity
    if regime.get("symbol") or regime.get("ticker"):
        lines.append(f"Ticker: {regime.get('symbol') or regime.get('ticker')}")
    if regime.get("price"):
        lines.append(f"Price: ${regime['price']:.2f}")

    # Structured metrics (new format)
    metrics = regime.get("metrics", {})
    sentiment = regime.get("sentiment", {})

    # Price changes — flat or nested
    change_1d = regime.get("change_1d") or metrics.get("change_1d")
    change_1w = regime.get("change_1w") or metrics.get("change_1w")
    if change_1d is not None:
        lines.append(f"1D Change: {change_1d:+.2f}%")
    if change_1w is not None:
        lines.append(f"1W Change: {change_1w:+.2f}%")

    # RSI
    rsi = regime.get("rsi") or metrics.get("rsi")
    if rsi is not None:
        lines.append(f"RSI: {rsi:.1f}")

    # Volume delta
    vol_delta = regime.get("vol_delta") or metrics.get("vol_delta")
    if vol_delta is not None:
        lines.append(f"Vol_Delta: {vol_delta:+.0%}" if isinstance(vol_delta, float) else f"Vol_Delta: {vol_delta}")

    # Trend
    if regime.get("trend") or metrics.get("trend"):
        lines.append(f"Trend: {regime.get('trend') or metrics.get('trend')}")

    # Volume signal
    if regime.get("volume_signal") or metrics.get("volume_signal"):
        lines.append(f"Volume: {regime.get('volume_signal') or metrics.get('volume_signal')}")

    # Sector
    if regime.get("sector"):
        lines.append(f"Sector: {regime['sector']}")

    # Sentiment — Fear & Greed
    fg_index = regime.get("fg_index") or sentiment.get("fg_index")
    fg_label = regime.get("fg_label") or sentiment.get("fg_label")
    if fg_index is not None:
        lines.append(f"Fear_Greed: {fg_index}" + (f" ({fg_label})" if fg_label else ""))

    # VIX
    vix = regime.get("vix") or sentiment.get("vix")
    if vix:
        lines.append(f"VIX: {vix}")

    # Macro / news
    if regime.get("macro_context"):
        lines.append(f"Macro: {regime['macro_context']}")
    if regime.get("news_sentiment") or sentiment.get("news"):
        lines.append(f"News Sentiment: {regime.get('news_sentiment') or sentiment.get('news')}")

    # Prediction + outcome
    if regime.get("prediction"):
        lines.append(f"AI Prediction: {regime['prediction']}")
    if regime.get("confidence"):
        lines.append(f"Confidence: {regime['confidence']}%")
    if regime.get("actual_result"):
        lines.append(f"Actual Result: {regime['actual_result']}")
    if regime.get("outcome"):
        lines.append(f"Outcome: {regime['outcome']}")

    return " | ".join(lines) if lines else json.dumps(regime)


def _make_id(regime: Dict) -> str:
    """Generate a deterministic ID for a regime to avoid duplicates."""
    key_parts = [
        regime.get("symbol", ""),
        regime.get("date", ""),
        str(regime.get("price", "")),
    ]
    raw = "|".join(key_parts)
    return hashlib.md5(raw.encode()).hexdigest()


async def save_regime(regime: Dict) -> str:
    """Store a market regime episode as a vector embedding.

    regime should contain:
        symbol, price, change_1d, change_1w, rsi, trend, volume_signal,
        sector, macro_context, news_sentiment, prediction, confidence,
        actual_result (added later when verified), outcome ('hit'/'miss')
    """
    if not _collection:
        logger.warning("Market Memory not initialized, skipping save")
        return ""

    text = _regime_to_text(regime)
    doc_id = _make_id(regime)

    metadata = {
        "symbol": regime.get("symbol", "UNKNOWN"),
        "date": regime.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "outcome": regime.get("outcome", "pending"),
        "confidence": float(regime.get("confidence", 0)),
    }

    # ChromaDB auto-embeds using built-in model
    await asyncio.to_thread(
        _collection.upsert,
        ids=[doc_id],
        documents=[text],
        metadatas=[metadata],
    )

    logger.info(f"Saved regime for {metadata['symbol']} on {metadata['date']} (id={doc_id[:8]})")

    if _db is not None:
        try:
            await _db.market_memory_log.update_one(
                {"_id": doc_id},
                {"$set": {
                    "regime": regime,
                    "text": text,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"MongoDB memory log failed: {e}")

    return doc_id


async def query_similar_regimes(
    current_state: Dict, n_results: int = 3, outcome_filter: Optional[str] = None
) -> List[Dict]:
    """Find the top N historical regimes most similar to the current market state."""
    if not _collection:
        logger.warning("Market Memory not initialized, returning empty")
        return []

    count = await asyncio.to_thread(_collection.count)
    if count == 0:
        return []

    text = _regime_to_text(current_state)

    where_filter = None
    if outcome_filter:
        where_filter = {"outcome": outcome_filter}

    actual_n = min(n_results, count)

    results = await asyncio.to_thread(
        _collection.query,
        query_texts=[text],
        n_results=actual_n,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    similar = []
    if results and results.get("documents"):
        for i, doc in enumerate(results["documents"][0]):
            distance = results["distances"][0][i] if results.get("distances") else 0
            similarity = round(1 - distance, 4)  # cosine distance -> similarity
            meta = results["metadatas"][0][i] if results.get("metadatas") else {}
            similar.append({
                "text": doc,
                "similarity": similarity,
                "symbol": meta.get("symbol", ""),
                "date": meta.get("date", ""),
                "outcome": meta.get("outcome", ""),
                "confidence": meta.get("confidence", 0),
            })

    return similar


async def get_prediction_context(symbol: str, current_data: Dict, n_results: int = 3) -> str:
    """Build a historical context string to inject into prediction prompts.

    This is the main integration point — call this before making a prediction.
    """
    current_state = {**current_data, "symbol": symbol}
    similar = await query_similar_regimes(current_state, n_results=n_results)

    if not similar:
        return ""

    lines = ["HISTORICAL PATTERN MEMORY (similar past market regimes):"]
    for i, case in enumerate(similar, 1):
        lines.append(
            f"  Case {i} (similarity={case['similarity']:.0%}, "
            f"{case['symbol']} on {case['date']}, "
            f"outcome={case['outcome']}): {case['text']}"
        )
    lines.append(
        "Use these historical patterns to calibrate your prediction — "
        "similar regimes in the past had the outcomes shown above."
    )
    return "\n".join(lines)


async def get_strategist_context(ticker: str, current_rsi: float = None, n_results: int = 3) -> str:
    """Query ONLY past accurate predictions with similar conditions.

    Filters for outcome='hit' only — surfaces 'lessons learned' from winning calls.
    Returns formatted text ready for injection into agent prompts.
    """
    if not _collection:
        return "No similar successful patterns in memory yet."

    count = await asyncio.to_thread(_collection.count)
    if count == 0:
        return "No similar successful patterns in memory yet."

    # Build a targeted query emphasizing ticker + RSI
    query_parts = [f"Ticker {ticker}"]
    if current_rsi is not None:
        query_parts.append(f"RSI {current_rsi:.1f}")
    query_text = " ".join(query_parts)

    # Filter for successful predictions only
    where_filter = {"outcome": "hit"}

    actual_n = min(n_results, count)

    try:
        results = await asyncio.to_thread(
            _collection.query,
            query_texts=[query_text],
            n_results=actual_n,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.warning(f"Strategist context query failed: {e}")
        return "No similar successful patterns in memory yet."

    if not results or not results.get("documents") or not results["documents"][0]:
        return "No similar successful patterns in memory yet."

    lessons = []
    for i, doc in enumerate(results["documents"][0]):
        distance = results["distances"][0][i] if results.get("distances") else 0
        similarity = round(1 - distance, 4)
        meta = results["metadatas"][0][i] if results.get("metadatas") else {}

        lessons.append(
            f"- Win Pattern (sim={similarity:.0%}, {meta.get('symbol', '?')} on "
            f"{meta.get('date', '?')}): {doc}"
        )

    if not lessons:
        return "No similar successful patterns in memory yet."

    header = f"HISTORICAL WIN PATTERNS for {ticker}"
    if current_rsi is not None:
        header += f" (RSI ~{current_rsi:.0f})"
    header += ":"
    footer = (
        "These are verified successful predictions in similar market conditions. "
        "Use them to calibrate confidence — if current conditions mirror a past win, "
        "explain why the probability is higher."
    )
    return f"{header}\n" + "\n".join(lessons) + f"\n{footer}"


async def get_memory_stats() -> Dict:
    """Return stats about the vector memory store."""
    count = await asyncio.to_thread(_collection.count) if _collection else 0
    mongo_count = 0
    if _db is not None:
        try:
            mongo_count = await _db.market_memory_log.count_documents({})
        except Exception:
            pass

    return {
        "total_episodes": count,
        "mongodb_log_count": mongo_count,
        "collection_name": COLLECTION_NAME,
        "embedding_model": "all-MiniLM-L6-v2 (local)",
        "storage_path": CHROMA_DIR,
        "initialized": _collection is not None,
    }
