"""Mongo $text-index retrieval against the Alpha Knowledge Base.

Pure async function — no globals, no caches. Operator-tunable
``limit`` parameter caps the result set.
"""
from __future__ import annotations

import logging
from typing import Any

from .schemas import RetrievalResponse, RetrievalResult

logger = logging.getLogger(__name__)


COLLECTION = "alpha_knowledge_python"
DEFAULT_LIMIT = 6
MAX_LIMIT = 20


def _clean_query(query: str) -> str:
    return (query or "").strip()[:500]


async def retrieve(
    db,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    category: str | None = None,
) -> RetrievalResponse:
    """Return top-k chunks ranked by Mongo's textScore.

    Never raises. Returns an empty response on any backend error
    so the chat hook can degrade gracefully.
    """
    cleaned = _clean_query(query)
    cap = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    if not cleaned:
        try:
            total = await db[COLLECTION].count_documents({})
        except Exception:  # noqa: BLE001
            total = 0
        return RetrievalResponse(query=cleaned, results=[], total_corpus_size=total)

    q: dict[str, Any] = {"$text": {"$search": cleaned}}
    if category:
        q["source_category"] = category

    proj = {
        "_id": 0,
        "chunk_id": 1,
        "source_url": 1,
        "source_title": 1,
        "source_category": 1,
        "chunk_index": 1,
        "text": 1,
        "score": {"$meta": "textScore"},
    }

    try:
        cursor = (
            db[COLLECTION]
            .find(q, proj)
            .sort([("score", {"$meta": "textScore"})])
            .limit(cap)
        )
        rows = [doc async for doc in cursor]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_knowledge] retrieve failed: %s", exc)
        rows = []

    try:
        total = await db[COLLECTION].count_documents({})
    except Exception:  # noqa: BLE001
        total = 0

    results = [
        RetrievalResult(
            chunk_id=r["chunk_id"],
            source_url=r["source_url"],
            source_title=r["source_title"],
            source_category=r["source_category"],
            chunk_index=int(r.get("chunk_index") or 0),
            text=r["text"],
            score=float(r.get("score") or 0.0),
        )
        for r in rows
    ]
    return RetrievalResponse(
        query=cleaned, results=results, total_corpus_size=total,
    )


async def status(db) -> dict[str, Any]:
    """Return aggregate stats — chunk count + per-category breakdown
    + most-recent ingest timestamp."""
    out: dict[str, Any] = {
        "total_chunks": 0,
        "total_sources": 0,
        "by_category": {},
        "last_ingest_at": None,
        "schema_version": 1,
    }
    try:
        out["total_chunks"] = await db[COLLECTION].count_documents({})
        cats: dict[str, int] = {}
        async for d in db[COLLECTION].aggregate([
            {"$group": {"_id": "$source_category", "n": {"$sum": 1}}},
        ]):
            if d.get("_id"):
                cats[d["_id"]] = int(d.get("n") or 0)
        out["by_category"] = cats

        urls: set[str] = set()
        async for d in db[COLLECTION].aggregate([
            {"$group": {"_id": "$source_url"}},
        ]):
            if d.get("_id"):
                urls.add(d["_id"])
        out["total_sources"] = len(urls)

        latest = await db[COLLECTION].find_one(
            {}, sort=[("ingested_at", -1)],
        )
        if latest and latest.get("ingested_at"):
            out["last_ingest_at"] = latest["ingested_at"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_knowledge] status failed: %s", exc)
    return out
