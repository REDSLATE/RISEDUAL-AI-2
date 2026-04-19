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
from datetime import datetime, timezone, timedelta
from typing import Optional

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


def _regime_to_text(regime: dict) -> str:
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
    if regime.get("failure_code"):
        lines.append(f"Failure Mode: {regime['failure_code']}")
    if regime.get("failure_reason"):
        lines.append(f"Failure Reason: {regime['failure_reason']}")

    return " | ".join(lines) if lines else json.dumps(regime)


def _make_id(regime: dict) -> str:
    """Generate a deterministic ID for a regime to avoid duplicates."""
    key_parts = [
        regime.get("symbol", ""),
        regime.get("date", ""),
        str(regime.get("price", "")),
    ]
    raw = "|".join(key_parts)
    return hashlib.sha256(raw.encode()).hexdigest()


async def save_regime(regime: dict) -> str:
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

    # Store failure classification if present
    if regime.get("failure_code"):
        metadata["failure_code"] = regime["failure_code"]

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
    current_state: dict, n_results: int = 3, outcome_filter: Optional[str] = None
) -> list[dict]:
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
    else:
        # By default, exclude toxic_lesson entries from general queries
        where_filter = {"outcome": {"$ne": "toxic_lesson"}}

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


async def get_prediction_context(symbol: str, current_data: dict, n_results: int = 3) -> str:
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



async def get_strategist_veto_context(ticker: str, current_rsi: float = None, n_results: int = 2) -> str:
    """Query toxic lessons — high-confidence failures that the AI should avoid repeating.

    Filters for outcome='toxic_lesson' and returns formatted warnings.
    This is the 'Veto' signal: if current conditions look like a past trap, lower confidence.
    """
    if not _collection:
        return ""

    count = await asyncio.to_thread(_collection.count)
    if count == 0:
        return ""

    query_parts = [f"Ticker {ticker}"]
    if current_rsi is not None:
        query_parts.append(f"RSI {current_rsi:.1f}")
    query_text = " ".join(query_parts)
    actual_n = min(n_results, count)

    try:
        results = await asyncio.to_thread(
            _collection.query,
            query_texts=[query_text],
            n_results=actual_n,
            where={"outcome": "toxic_lesson"},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.warning(f"Toxic lessons query failed: {e}")
        return ""

    if not results or not results.get("documents") or not results["documents"][0]:
        return ""

    warnings = []
    for i, doc in enumerate(results["documents"][0]):
        distance = results["distances"][0][i] if results.get("distances") else 0
        similarity = round(1 - distance, 4)
        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
        failure_tag = ""
        if meta.get("failure_code"):
            failure_tag = f" [{meta['failure_code']}]"
        warnings.append(
            f"- FAILED Pattern{failure_tag} (sim={similarity:.0%}, {meta.get('symbol', '?')} on "
            f"{meta.get('date', '?')}, confidence was {meta.get('confidence', '?')}%): {doc}"
        )

    if not warnings:
        return ""

    header = f"WARNING — TOXIC PATTERNS for {ticker} (these were HIGH-CONFIDENCE FAILURES):"
    footer = (
        "These predictions had high confidence but were WRONG. "
        "If current conditions resemble these, LOWER your confidence and explain why this time might be different."
    )
    return f"{header}\n" + "\n".join(warnings) + f"\n{footer}"


async def get_memory_stats() -> dict:
    """Return stats about the vector memory store."""
    count = await asyncio.to_thread(_collection.count) if _collection else 0
    mongo_count = 0
    toxic_count = 0
    if _db is not None:
        try:
            mongo_count = await _db.market_memory_log.count_documents({})
        except Exception:
            pass

    # Count toxic lessons in ChromaDB
    if _collection:
        try:
            toxic_data = await asyncio.to_thread(
                _collection.get, where={"outcome": "toxic_lesson"}
            )
            toxic_count = len(toxic_data.get("ids", []))
        except Exception:
            pass

    # Get last cleanup info
    last_cleanup = None
    if _db is not None:
        try:
            doc = await _db.memory_cleanup_log.find_one(
                {}, {"_id": 0}, sort=[("run_at", -1)]
            )
            if doc:
                last_cleanup = doc
        except Exception:
            pass

    return {
        "total_episodes": count,
        "toxic_lessons": toxic_count,
        "active_episodes": count - toxic_count,
        "mongodb_log_count": mongo_count,
        "collection_name": COLLECTION_NAME,
        "embedding_model": "all-MiniLM-L6-v2 (local)",
        "storage_path": CHROMA_DIR,
        "initialized": _collection is not None,
        "last_cleanup": last_cleanup,
    }


# ──────────────────────────────────────────────
#  NIGHTLY CLEANUP — Prune toxic outliers & obsolete data
# ──────────────────────────────────────────────

async def nightly_cleanup(days_to_keep: int = 90, toxic_confidence_threshold: float = 80.0) -> dict:
    """Retrain memory by re-tagging bad patterns and pruning obsolete data.

    A. Re-tag Toxic Outliers: High-confidence (>80%) predictions that were WRONG.
       Instead of deleting, these are re-tagged as 'toxic_lesson' so the AI can
       retrieve them as negative examples ("what NOT to do").

    B. Prune Obsolete Data: Episodes older than `days_to_keep` days.
       Ensures the AI adapts to current market regimes, not stale patterns.

    C. Alert System: Sends email + in-app notifications when toxic spikes are found.
    """
    if not _collection:
        return {"error": "Market Memory not initialized"}

    results = {
        "toxic_removed": 0,
        "obsolete_removed": 0,
        "total_before": 0,
        "total_after": 0,
        "toxic_details": [],
    }

    results["total_before"] = await asyncio.to_thread(_collection.count)
    if results["total_before"] == 0:
        return {**results, "status": "empty", "message": "No episodes to clean"}

    # ── A. Re-tag Toxic Outliers as "toxic_lesson" ──
    try:
        toxic = await asyncio.to_thread(
            _collection.get,
            where={
                "$and": [
                    {"outcome": "miss"},
                    {"confidence": {"$gt": toxic_confidence_threshold}},
                ]
            },
            include=["metadatas"],
        )
        toxic_ids = toxic.get("ids", [])
        toxic_metas = toxic.get("metadatas", [])

        if toxic_ids:
            # Collect details for alerts before re-tagging
            for i, tid in enumerate(toxic_ids):
                meta = toxic_metas[i] if i < len(toxic_metas) else {}
                results["toxic_details"].append({
                    "id": tid,
                    "symbol": meta.get("symbol", "?"),
                    "confidence": meta.get("confidence", 0),
                    "date": meta.get("date", "?"),
                    "failure_code": meta.get("failure_code", "UNKNOWN"),
                })

            # Re-tag as toxic_lesson instead of deleting
            updated_metas = []
            for i, tid in enumerate(toxic_ids):
                meta = toxic_metas[i].copy() if i < len(toxic_metas) else {}
                meta["outcome"] = "toxic_lesson"
                updated_metas.append(meta)

            await asyncio.to_thread(
                _collection.update,
                ids=toxic_ids,
                metadatas=updated_metas,
            )
            results["toxic_removed"] = len(toxic_ids)
            logger.info(f"Cleanup: Re-tagged {len(toxic_ids)} toxic high-confidence failures as 'toxic_lesson'")
    except Exception as e:
        logger.warning(f"Toxic outlier cleanup failed: {e}")

    # ── B. Prune Obsolete Data ──
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
    try:
        # ChromaDB $lt only works on numeric values, so fetch all and filter by date in Python
        all_data = await asyncio.to_thread(
            _collection.get,
            include=["metadatas"],
        )
        all_ids = all_data.get("ids", [])
        all_metas = all_data.get("metadatas", [])
        old_ids = [
            all_ids[i]
            for i in range(len(all_ids))
            if i < len(all_metas) and all_metas[i].get("date", "9999") < cutoff_date
        ]
        if old_ids:
            # Delete in batches of 500 to avoid ChromaDB limits
            for batch_start in range(0, len(old_ids), 500):
                batch = old_ids[batch_start:batch_start + 500]
                await asyncio.to_thread(_collection.delete, ids=batch)
            results["obsolete_removed"] = len(old_ids)
            logger.info(f"Cleanup: Pruned {len(old_ids)} episodes older than {cutoff_date}")
    except Exception as e:
        logger.warning(f"Obsolete data cleanup failed: {e}")

    results["total_after"] = await asyncio.to_thread(_collection.count)
    results["status"] = "complete"
    results["cutoff_date"] = cutoff_date
    results["confidence_threshold"] = toxic_confidence_threshold
    results["run_at"] = datetime.now(timezone.utc).isoformat()

    # Log cleanup to MongoDB
    if _db is not None:
        try:
            await _db.memory_cleanup_log.insert_one(results.copy())
        except Exception as e:
            logger.warning(f"Cleanup log save failed: {e}")

    logger.info(
        f"Nightly cleanup complete: {results['toxic_removed']} toxic re-tagged + "
        f"{results['obsolete_removed']} obsolete removed "
        f"({results['total_before']} -> {results['total_after']} episodes)"
    )

    # ── C. Alert System — Email + In-App Notifications + SSE Stream ──
    if results["toxic_removed"] > 0:
        await _send_toxic_alerts(results)
        # Push to SSE stream
        try:
            from routes.stream import push_event
            affected = list({d.get("symbol", "?") for d in results.get("toxic_details", [])})
            push_event("toxic_alert", {
                "toxic_count": results["toxic_removed"],
                "obsolete_removed": results["obsolete_removed"],
                "affected_tickers": affected,
                "total_before": results["total_before"],
                "total_after": results["total_after"],
            })
        except Exception:
            pass

    return results


async def _send_toxic_alerts(cleanup_results: dict):
    """Send email and in-app notifications when toxic spikes are detected."""
    toxic_count = cleanup_results.get("toxic_removed", 0)
    toxic_details = cleanup_results.get("toxic_details", [])

    # ── 1. Email Alert to admins/owner ──
    try:
        from services.email_service import send_toxic_spikes_email
        import os

        admin_email = os.environ.get("ADMIN_EMAIL", "")
        owner_email = os.environ.get("OWNER_EMAIL", "")
        recipients = list({e for e in [admin_email, owner_email] if e})

        for email in recipients:
            await send_toxic_spikes_email(
                recipient_email=email,
                toxic_count=toxic_count,
                obsolete_count=cleanup_results.get("obsolete_removed", 0),
                total_before=cleanup_results.get("total_before", 0),
                total_after=cleanup_results.get("total_after", 0),
                spike_details=toxic_details,
            )
        logger.info(f"Toxic spikes email alerts sent to {len(recipients)} admin(s)")
    except Exception as e:
        logger.error(f"Failed to send toxic spikes email: {e}")

    # ── 2. In-App Notifications for all Pro users ──
    if _db is None:
        return

    try:
        # Build summary of affected tickers
        affected_tickers = list({d.get("symbol", "?") for d in toxic_details[:20]})
        ticker_summary = ", ".join(affected_tickers[:5])
        if len(affected_tickers) > 5:
            ticker_summary += f" +{len(affected_tickers) - 5} more"

        # Find all Pro users
        pro_users = []
        cursor = _db.users.find(
            {"subscription_status": {"$in": ["pro", "trial"]}},
            {"_id": 1},
        )
        async for user in cursor:
            pro_users.append(str(user["_id"]))

        if not pro_users:
            logger.info("No Pro users found for toxic spike notifications")
            return

        # Batch insert notifications for all Pro users
        now = datetime.now(timezone.utc).isoformat()
        notifications = [
            {
                "user_id": uid,
                "type": "toxic_spike",
                "title": f"Toxic Spikes: {toxic_count} Bad Predictions Detected",
                "message": f"Nightly cleanup found {toxic_count} high-confidence failures ({ticker_summary}). Re-tagged as negative lessons in memory.",
                "read": False,
                "created_at": now,
                "metadata": {
                    "toxic_count": toxic_count,
                    "affected_tickers": affected_tickers,
                    "total_before": cleanup_results.get("total_before", 0),
                    "total_after": cleanup_results.get("total_after", 0),
                },
            }
            for uid in pro_users
        ]

        await _db.notifications.insert_many(notifications)
        logger.info(f"Toxic spike in-app notifications sent to {len(pro_users)} Pro user(s)")
    except Exception as e:
        logger.error(f"Failed to create toxic spike notifications: {e}")
