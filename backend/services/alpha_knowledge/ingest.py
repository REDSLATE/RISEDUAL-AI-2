"""Ingest pipeline — fetch URL → chunk → upsert to Mongo.

Idempotent on ``chunk_id``. The whole pipeline is fire-and-forget
per URL: a single fetch failure does not abort the run.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .chunker import chunk_id_for, chunk_text, html_to_text
from .schemas import IngestSummary
from .seed_manifest import filter_by_category

logger = logging.getLogger(__name__)


COLLECTION = "alpha_knowledge_python"
USER_AGENT = "RisedualAlphaKnowledgeBot/0.1 (+https://risedual.ai)"
FETCH_TIMEOUT = 12.0
CONCURRENCY = 4


async def ensure_indexes(db) -> None:
    """Idempotent. Creates the text index used by retrieval + the
    upsert key."""
    if db is None:
        return
    coll = db[COLLECTION]
    try:
        await coll.create_index(
            "chunk_id", unique=True, name="alpha_knowledge_chunk_id",
        )
        await coll.create_index(
            [("text", "text"), ("source_title", "text")],
            name="alpha_knowledge_text",
            default_language="english",
        )
        await coll.create_index(
            "source_category", name="alpha_knowledge_category",
        )
        await coll.create_index(
            "source_url", name="alpha_knowledge_url",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_knowledge] ensure_indexes failed: %s", exc)


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[str | None, str | None]:
    """Return ``(html, error)``. Always (str, None) or (None, str)."""
    try:
        r = await client.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.text, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


async def _ingest_one(
    db,
    client: httpx.AsyncClient,
    source: tuple[str, str, str],
    *,
    summary: IngestSummary,
) -> None:
    url, title, category = source
    summary.sources_attempted += 1
    html, err = await _fetch_one(client, url)
    if html is None:
        summary.sources_failed += 1
        summary.failures.append({"url": url, "error": err or "unknown"})
        return
    text = html_to_text(html)
    chunks = chunk_text(text)
    if not chunks:
        summary.sources_failed += 1
        summary.failures.append({"url": url, "error": "no_chunks_extracted"})
        return

    now = datetime.now(timezone.utc)
    docs: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        cid = chunk_id_for(url, idx)
        docs.append({
            "chunk_id": cid,
            "source_url": url,
            "source_title": title,
            "source_category": category,
            "chunk_index": idx,
            "text": chunk,
            "char_count": len(chunk),
            "ingested_at": now,
            "schema_version": 1,
            "excluded_from_code_gate_inputs": True,
        })

    if docs:
        try:
            ops = []
            from pymongo import UpdateOne
            for d in docs:
                ops.append(UpdateOne(
                    {"chunk_id": d["chunk_id"]},
                    {"$set": d},
                    upsert=True,
                ))
            await db[COLLECTION].bulk_write(ops, ordered=False)
            summary.sources_fetched += 1
            summary.chunks_written += len(docs)
        except Exception as exc:  # noqa: BLE001
            summary.sources_failed += 1
            summary.failures.append({"url": url, "error": f"mongo_write: {exc}"})


async def ingest(
    db,
    *,
    categories: list[str] | None = None,
    limit_urls: int | None = None,
) -> IngestSummary:
    """Public entry — never raises."""
    started = datetime.now(timezone.utc)
    summary = IngestSummary(
        started_at=started,
        finished_at=started,
        sources_attempted=0,
        sources_fetched=0,
        sources_failed=0,
        chunks_written=0,
        chunks_total_after=0,
        failures=[],
    )

    await ensure_indexes(db)

    sources = filter_by_category(categories)
    if limit_urls is not None and limit_urls > 0:
        sources = sources[:limit_urls]

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _bounded(client, src):
        async with sem:
            await _ingest_one(db, client, src, summary=summary)

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT, headers=headers,
        ) as client:
            await asyncio.gather(
                *[_bounded(client, src) for src in sources],
                return_exceptions=True,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_knowledge] ingest top-level failure: %s", exc)

    summary.finished_at = datetime.now(timezone.utc)
    try:
        summary.chunks_total_after = await db[COLLECTION].count_documents({})
    except Exception:  # noqa: BLE001
        summary.chunks_total_after = 0
    return summary
