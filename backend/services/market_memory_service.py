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
from typing import Any, Optional, cast

import chromadb
from chromadb.types import Where, UpdateMetadata

from services.structured_log import log_error, log_warning

logger = logging.getLogger(__name__)

CHROMA_DIR = "/app/backend/data/chromadb"
COLLECTION_NAME = "market_regimes"

_client: Optional[Any] = None
_collection = None
_db = None  # MongoDB reference for stats


def init_memory(mongo_db: Any = None) -> None:
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

    # Canonicalise confidence to 0-100 scale. Two upstream paths write
    # here — memory_training_service already uses 0-100, but
    # prediction_tracker.verify_pending_predictions writes 0-1 raw
    # floats from Mongo. Without this normalisation, the nightly
    # cleanup's `confidence > 80` query only matches one half of the
    # data, which is exactly what let "easy tickers" keep getting
    # flagged toxic every night. See services/prediction_tracker.py
    # ::normalize_confidence for the single source of truth.
    from services.prediction_tracker import normalize_confidence
    metadata = {
        "symbol": regime.get("symbol", "UNKNOWN"),
        "date": regime.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "outcome": regime.get("outcome", "pending"),
        "confidence": normalize_confidence(regime.get("confidence")),
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
            log_warning(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "market_memory",
                "note": "MongoDB memory log failed",
            })

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

    where_filter: dict[str, Any] | None
    if outcome_filter:
        where_filter = {"outcome": outcome_filter}
    else:
        # By default, exclude toxic_lesson entries from general queries
        where_filter = {"outcome": {"$ne": "toxic_lesson"}}

    actual_n = min(n_results, count)

    # ChromaDB's Where TypedDict is tighter than our runtime-valid
    # filter ({"outcome": str} or {"outcome": {"$ne": str}}). Cast
    # instead of a silent `# type: ignore` so mypy still catches
    # drift if we ever construct a filter with a wrong operator key.
    results = await asyncio.to_thread(
        _collection.query,
        query_texts=[text],
        n_results=actual_n,
        where=cast(Where, where_filter) if where_filter else None,
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
            where=cast(Where, where_filter),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "market_memory",
            "note": "Strategist context query failed",
        })
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
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "market_memory",
            "note": "Toxic lessons query failed",
        })
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

    results: dict[str, Any] = {
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
        # Cast preserves structural validation — if a future edit
        # uses an operator outside ChromaDB's `$and`/`$gt`/`$ne`/...
        # literal set, mypy still rejects it at the cast site.
        toxic_where: dict[str, Any] = {
            "$and": [
                {"outcome": "miss"},
                {"confidence": {"$gt": toxic_confidence_threshold}},
            ]
        }
        toxic = await asyncio.to_thread(
            _collection.get,
            where=cast(Where, toxic_where),
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

            # Re-tag as toxic_lesson instead of deleting. `dict()`
            # mirrors runtime `.copy()`; ChromaDB's metadata stub
            # types the source as Mapping. Cast the outgoing list
            # so mypy checks each dict is an UpdateMetadata-shaped
            # payload.
            updated_metas: list[dict[str, Any]] = []
            for i, tid in enumerate(toxic_ids):
                meta = dict(toxic_metas[i]) if i < len(toxic_metas) else {}
                meta["outcome"] = "toxic_lesson"
                updated_metas.append(meta)

            await asyncio.to_thread(
                _collection.update,
                ids=toxic_ids,
                metadatas=cast(list[UpdateMetadata], updated_metas),
            )
            results["toxic_removed"] = len(toxic_ids)
            logger.info(f"Cleanup: Re-tagged {len(toxic_ids)} toxic high-confidence failures as 'toxic_lesson'")
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "market_memory",
            "note": "Toxic outlier cleanup failed",
        })

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
        # `.get("date", "9999")` return type is `str | int | float |
        # SparseVector` per ChromaDB stubs. Runtime always gets
        # back a str (we write dates as ISO strings). str <-> str
        # compare is safe; cast to str to appease the union.
        old_ids = [
            all_ids[i]
            for i in range(len(all_ids))
            if i < len(all_metas) and str(all_metas[i].get("date", "9999")) < cutoff_date
        ]
        if old_ids:
            # Delete in batches of 500 to avoid ChromaDB limits
            for batch_start in range(0, len(old_ids), 500):
                batch = old_ids[batch_start:batch_start + 500]
                await asyncio.to_thread(_collection.delete, ids=batch)
            results["obsolete_removed"] = len(old_ids)
            logger.info(f"Cleanup: Pruned {len(old_ids)} episodes older than {cutoff_date}")
    except Exception as e:
        log_warning(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "market_memory",
            "note": "Obsolete data cleanup failed",
        })

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
            log_warning(logger, {
                "error": str(e),
                "type": type(e).__name__,
                "context": "market_memory",
                "note": "Cleanup log save failed",
            })

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


async def _send_toxic_alerts(cleanup_results: dict) -> None:
    """Send email and in-app notifications when toxic spikes are detected.

    Deduplicated: if an alert with the same (type, tickers, date_bucket)
    already fired within 48h, we skip. If the same alert has been
    repeating on consecutive days, the subject escalates to
    "Persisting (N days in a row)" — the real ask from the user who
    was getting the exact same alert 3 nights running.
    """
    # ── Emergency mute switch ──
    # Operator escape hatch for runaway cascades. Set
    # `TOXIC_SPIKE_ALERTS_DISABLED=true` in env + restart backend
    # to silence all toxic-spike emails AND in-app notifications.
    # The cleanup itself still runs (we keep retagging toxic
    # predictions in memory) — only the user-facing fanout is
    # gated. Flip back to false once root cause is diagnosed.
    import os as _os
    if _os.environ.get("TOXIC_SPIKE_ALERTS_DISABLED", "").lower() in (
        "1", "true", "yes", "on",
    ):
        logger.warning(
            "[toxic-alert] MUTED via TOXIC_SPIKE_ALERTS_DISABLED — "
            "cleanup ran, alert fanout skipped"
        )
        return

    toxic_count = cleanup_results.get("toxic_removed", 0)
    toxic_details = cleanup_results.get("toxic_details", [])

    # Reserve-first dedup pattern.
    #
    # We bucket by DAY + ALERT TYPE only, NOT by the exact ticker set.
    # Two cleanup runs 35s apart on the same day could report slightly
    # different toxic sets (e.g. run 1 finds {MSFT, AAPL}, run 2 also
    # finds TEST_FAIL_61), which produced different alert_ids on the
    # old ticker-set-based key and bypassed the 48h suppress window —
    # back-to-back emails fired on 2026-04-10 and 2026-04-19.
    #
    # The ACTUAL product requirement is "max one toxic-spike email per
    # day", so we key off a stable daily bucket and let the DB's unique
    # index on `alert_id` be the atomic gate. If `record_alert` raises
    # `DuplicateKeyError`, another run already reserved this slot and
    # we silently suppress — no race window between read and write.
    from services.alert_dedup import (
        compute_alert_id,
        date_bucket_today,
        persistence_run_count,
        record_alert,
    )
    from pymongo.errors import DuplicateKeyError

    affected = sorted({d.get("symbol", "?") for d in toxic_details})
    dedup_key = ["toxic_spike_daily"]
    date_bucket = date_bucket_today()
    alert_id = compute_alert_id("toxic_spike", dedup_key, date_bucket)
    run_id = datetime.now(timezone.utc).isoformat()

    # Persistence run is informational (used only for subject
    # escalation), so a racy read here is fine — the reserve below is
    # the only decision point for whether we actually send.
    run = await persistence_run_count(_db, "toxic_spike", dedup_key)

    # Atomic reserve: unique index on alert_id rejects duplicates.
    # Store enough context in metadata to replay the email later from
    # the Audit UI (toxic_count, obsolete_count, totals, spike_details)
    # without needing to re-run the expensive cleanup scan.
    replay_payload = {
        "toxic_count": toxic_count,
        "obsolete_count": cleanup_results.get("obsolete_removed", 0),
        "total_before": cleanup_results.get("total_before", 0),
        "total_after": cleanup_results.get("total_after", 0),
        "spike_details": toxic_details,
        "persistence_tag": (
            f" — Persisting ({run + 1} days in a row)" if run >= 1 else ""
        ),
    }
    try:
        await record_alert(
            _db,
            alert_id=alert_id,
            alert_type="toxic_spike",
            tickers=dedup_key,
            date_bucket=date_bucket,
            metadata={
                "toxic_count": toxic_count,
                "affected_tickers": affected,
                "persistence_run": run + 1,
                "run_id": run_id,
                "reserved": True,
                "delivery_attempts": 1,
                "replay_payload": replay_payload,
            },
        )
        logger.info(
            f"[toxic-alert] reserved alert_id={alert_id[:12]} run_id={run_id}"
        )
        # Narrate the reservation into the agent activity feed.
        # Pass the top-5 worst offenders so the feed row can render
        # a "Why did this fire?" drilldown without another API call.
        try:
            from services.agent_activity_service import log_alert_reserved
            # Sort by confidence descending — highest-conf misses are
            # the most teachable (model was MOST sure AND wrong).
            top_offenders = sorted(
                toxic_details,
                key=lambda d: float(d.get("confidence") or 0),
                reverse=True,
            )[:5]
            await log_alert_reserved(
                alert_id=alert_id,
                run_id=run_id,
                alert_type="toxic_spike",
                toxic_count=toxic_count,
                tickers=affected,
                spike_details=[
                    {
                        "symbol": d.get("symbol"),
                        "confidence": d.get("confidence"),
                        "date": d.get("date"),
                        "failure_code": d.get("failure_code", "UNKNOWN"),
                    }
                    for d in top_offenders
                ],
            )
        except Exception:
            pass
    except DuplicateKeyError:
        logger.info(
            f"[toxic-alert] suppressed — already reserved ({alert_id[:12]})"
        )
        try:
            from services.agent_activity_service import log_alert_suppressed
            await log_alert_suppressed(
                alert_id=alert_id, alert_type="toxic_spike",
            )
        except Exception:
            pass
        return
    except Exception as e:
        # DB issue on reserve — fail open so we don't silently swallow
        # alerts because Mongo hiccuped. Duplicate on next run is
        # preferable to missing the alert entirely.
        logger.warning(
            f"[toxic-alert] reserve failed — failing open: {type(e).__name__}: {e}"
        )

    # One (and only one) process reaches here per day.
    persistence_tag = replay_payload["persistence_tag"]

    # ── 1. Email Alert to admins/owner ──
    # `send_toxic_spikes_email` swallows its own exceptions and returns
    # True/False, so we branch on the return value (not try/except) to
    # detect per-recipient failures correctly.
    email_recipients: list[str] = []
    email_failed_recipients: list[dict[str, str]] = []
    try:
        from services.email_service import send_toxic_spikes_email
        import os

        admin_email = os.environ.get("ADMIN_EMAIL", "")
        owner_email = os.environ.get("OWNER_EMAIL", "")
        recipients = list({e for e in [admin_email, owner_email] if e})

        for email in recipients:
            ok = await send_toxic_spikes_email(
                recipient_email=email,
                toxic_count=toxic_count,
                obsolete_count=cleanup_results.get("obsolete_removed", 0),
                total_before=cleanup_results.get("total_before", 0),
                total_after=cleanup_results.get("total_after", 0),
                spike_details=toxic_details,
                persistence_tag=persistence_tag,
            )
            if ok:
                email_recipients.append(email)
            else:
                # Provider returned False — either no provider
                # configured or send failed (details already logged
                # inside email_service.log_error).
                email_failed_recipients.append({
                    "email": email,
                    "error": "send_failed_or_no_provider",
                })
        logger.info(
            f"Toxic spikes email alerts sent to {len(email_recipients)}/"
            f"{len(recipients)} admin(s){persistence_tag}"
        )
    except Exception as e:
        log_error(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "market_memory",
            "note": "Failed to send toxic spikes email",
        })
        email_failed_recipients.append({"email": "*", "error": f"{type(e).__name__}: {e}"})

    # Stamp email outcome onto the reserved alert row so the Audit UI
    # (and any future retry tooling) can see what actually happened.
    if _db is not None:
        try:
            await _db.alerts_sent.update_one(
                {"alert_id": alert_id},
                {"$set": {
                    "metadata.email_recipients": email_recipients,
                    "metadata.email_failed": len(email_failed_recipients) > 0,
                    "metadata.email_failed_recipients": email_failed_recipients,
                }},
            )
        except Exception as e:
            logger.warning(f"[toxic-alert] failed to stamp email outcome: {e}")

    # Narrate the delivery outcome into the agent activity feed.
    try:
        from services.agent_activity_service import log_alert_delivery
        await log_alert_delivery(
            alert_id=alert_id,
            run_id=run_id,
            alert_type="toxic_spike",
            delivered=email_recipients,
            failed=email_failed_recipients,
        )
    except Exception:
        pass

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
        title = (
            f"Toxic Spikes: {toxic_count} Bad Predictions Detected"
            + persistence_tag
        )
        notifications = [
            {
                "user_id": uid,
                "type": "toxic_spike",
                "title": title,
                "message": f"Nightly cleanup found {toxic_count} high-confidence failures ({ticker_summary}). Re-tagged as negative lessons in memory.",
                "read": False,
                "created_at": now,
                "metadata": {
                    "toxic_count": toxic_count,
                    "affected_tickers": affected_tickers,
                    "total_before": cleanup_results.get("total_before", 0),
                    "total_after": cleanup_results.get("total_after", 0),
                    "persistence_run": run + 1,
                    "alert_id": alert_id,
                    "run_id": run_id,
                },
            }
            for uid in pro_users
        ]

        await _db.notifications.insert_many(notifications)
        logger.info(f"Toxic spike in-app notifications sent to {len(pro_users)} Pro user(s)")
    except Exception as e:
        log_error(logger, {
            "error": str(e),
            "type": type(e).__name__,
            "context": "market_memory",
            "note": "Failed to create toxic spike notifications",
        })
