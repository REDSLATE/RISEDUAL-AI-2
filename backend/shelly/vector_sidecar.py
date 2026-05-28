"""Phase 2 — Chroma vector sidecar for the 5-Shelly federation.

Mirrors the legacy ``services.shelly_memory`` pattern exactly:

  * Mongo is canonical truth. Chroma is disposable.
  * One Chroma collection PER federation node so the operator can
    do single-node similarity queries without filter conditions:
      shelly_alpha_v1, shelly_camaro_v1, shelly_chevelle_v1,
      shelly_redeye_v1, shelly_mc_v1
  * Embedding version pinned to ``minilm-l6-v2-default`` (same as
    legacy) — operator can upgrade by bumping ``EMBEDDING_VERSION``
    and re-rolling via the rollup job.
  * Best-effort: every Chroma operation is wrapped in try/except so
    a Chroma failure NEVER blocks the durable Mongo write.

What this module provides
-------------------------
* ``embed_memory(node, memory_doc)`` — upserts the memory's
  embedding into the node's Chroma collection. Idempotent on
  ``event_hash``.
* ``find_similar(node, query, *, top_k)`` — top-K nearest
  memories for THIS node only.
* ``find_similar_federation(query, *, top_k_per_node)`` —
  Phase 3-style cross-Shelly retrieval that queries all 5
  nodes' Chroma collections and returns the union.

Default similarity uses Chroma's built-in default embedding
function (sentence-transformers all-MiniLM-L6-v2) so no separate
embedding model load is required.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from shelly.config import NODE_NAMES

logger = logging.getLogger(__name__)

EMBEDDING_VERSION = "minilm-l6-v2-default"


# ── Chroma client (best-effort) ───────────────────────────────────


_chroma_client = None


def _get_chroma():
    """Lazy, in-process Chroma client. ``None`` if Chroma unavailable.
    Caller MUST wrap usage in try/except."""
    global _chroma_client
    if _chroma_client is None:
        try:
            import chromadb
        except ImportError:
            return None
        try:
            _chroma_client = chromadb.PersistentClient(
                path="/tmp/shelly_federation_chroma",
            )
        except Exception:  # noqa: BLE001
            return None
    return _chroma_client


def _collection_for(node: str):
    """Per-node Chroma collection handle, or ``None``."""
    client = _get_chroma()
    if client is None:
        return None
    try:
        return client.get_or_create_collection(f"shelly_{node.lower()}_v1")
    except Exception:  # noqa: BLE001
        return None


# ── Public API ────────────────────────────────────────────────────


def _doc_to_text(memory_doc: dict[str, Any]) -> str:
    """Render a memory doc into a single searchable string. The
    Chroma default embedding function will hash this into a 384-dim
    vector. Format is intentionally compact and stable — features
    are sorted so two identical-content events from different times
    still embed the same."""
    feats = memory_doc.get("features") or {}
    feat_str = " ".join(
        f"{k}={v}" for k, v in sorted(feats.items())
        if isinstance(v, (str, int, float, bool))
    )
    return (
        f"brain={memory_doc.get('brain', '?')} "
        f"sym={memory_doc.get('symbol', '?')} "
        f"dir={memory_doc.get('direction', '?')} "
        f"decision={memory_doc.get('decision', '?')} "
        f"conf={memory_doc.get('confidence', 0.0):.2f} "
        f"{feat_str}"
    )


def embed_memory(node: str, memory_doc: dict[str, Any]) -> bool:
    """Upsert one memory into ``node``'s Chroma collection.

    Returns True iff the upsert succeeded. Idempotent on
    ``event_hash``. Synchronous because the Chroma client is
    synchronous; called from inside ``LocalShelly.remember`` via
    ``asyncio.to_thread``-equivalent pattern (best-effort wrap).
    """
    coll = _collection_for(node)
    if coll is None:
        return False
    event_hash = memory_doc.get("event_hash")
    if not event_hash:
        return False
    text = _doc_to_text(memory_doc)
    try:
        coll.upsert(
            ids=[event_hash],
            documents=[text],
            metadatas=[{
                "node": node,
                "symbol": str(memory_doc.get("symbol") or ""),
                "direction": str(memory_doc.get("direction") or ""),
                "decision": str(memory_doc.get("decision") or ""),
                "embedding_version": EMBEDDING_VERSION,
                "authority": "memory_reasoning_only",
            }],
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[shelly_vector] upsert failed node=%s hash=%s: %s",
            node, event_hash, exc,
        )
        return False


def find_similar(
    node: str, query_doc: dict[str, Any], *, top_k: int = 5,
) -> list[dict[str, Any]]:
    """Return ``top_k`` most similar memories from ``node``'s Chroma."""
    coll = _collection_for(node)
    if coll is None:
        return []
    query = _doc_to_text(query_doc)
    try:
        result = coll.query(
            query_texts=[query],
            n_results=top_k,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[shelly_vector] query failed node=%s: %s", node, exc)
        return []
    ids = (result.get("ids") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0] or [None] * len(ids)
    out = []
    for i, mid in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        out.append({
            "event_hash": mid,
            "metadata": dict(meta),
            "distance": distances[i] if i < len(distances) else None,
        })
    return out


def find_similar_federation(
    query_doc: dict[str, Any], *,
    top_k_per_node: int = 3,
    node_names: Optional[tuple[str, ...]] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Phase 3-style cross-Shelly retrieval — query every node's
    Chroma collection independently and return per-node top-K
    matches. Caller can then merge / rank / detect conflicts.
    """
    names = node_names or NODE_NAMES
    return {
        node: find_similar(node, query_doc, top_k=top_k_per_node)
        for node in names
    }


__all__ = [
    "EMBEDDING_VERSION",
    "embed_memory",
    "find_similar",
    "find_similar_federation",
]
