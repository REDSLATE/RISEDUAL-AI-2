"""Mongo persistence for the RISEDUAL Learning Core resolved-memory
bank.

Patent M Phase 2 — persistence layer.

Phase 1 was strictly in-memory (operator directive, option 5C).
Phase 2 adds *write-then-rehydrate* persistence so the regime
clusters survive process restarts, without changing the live
runtime contract:

  * Writes are gated on env flag ``LEARNING_CORE_PERSISTENCE_ENABLED``
    (default off) — same kill-switch pattern the canonical
    regime-memory engine uses for ``REGIME_MEMORY_ENABLED``.
  * Writes are best-effort. Any Mongo failure is logged and
    swallowed; the in-memory cluster state is never affected.
  * Rehydrate is a one-shot startup helper. It does NOT poll, does
    NOT subscribe to changes, and does NOT short-circuit the
    in-memory engine's normal ingest path.
  * The collection schema mirrors the in-memory ``RegimeMemory``
    dataclass exactly — no transformations, no nested
    enrichment. A future migration can rebuild the in-memory
    state from a Mongo dump in O(n).

Collection: ``learning_core_resolved_memories``
Indices (caller is responsible for creating these — we don't
auto-create indices in this module so the operator can review
the migration before it lands on a populated DB):
  * ``memory_id`` (unique) — idempotency
  * ``ticker`` ascending — most-common query path
  * ``timestamp`` descending — newest-first scans

This module never reads from or writes to the canonical
``regime_memory_retrieval`` engine — it persists, that's all.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from services.regime_clustering_layer import (
    RegimeFingerprint,
    RegimeLabeledMemory,
)


logger = logging.getLogger(__name__)


COLLECTION_NAME = "learning_core_resolved_memories"


def _persistence_enabled() -> bool:
    """Read the env flag fresh each call so operator toggles take
    effect without a process restart."""
    return os.getenv("LEARNING_CORE_PERSISTENCE_ENABLED", "false").lower() == "true"


def _fingerprint_to_doc(fp: RegimeFingerprint | None) -> dict[str, str] | None:
    if fp is None:
        return None
    return {
        "vix_level": fp.vix_level,
        "yield_curve": fp.yield_curve,
        "dxy_trend": fp.dxy_trend,
        "credit_spreads": fp.credit_spreads,
        "liquidity": fp.liquidity,
        "macro_phase": fp.macro_phase,
    }


def _doc_to_fingerprint(doc: dict[str, str] | None) -> RegimeFingerprint | None:
    if not doc:
        return None
    return RegimeFingerprint(
        vix_level=doc["vix_level"],
        yield_curve=doc["yield_curve"],
        dxy_trend=doc["dxy_trend"],
        credit_spreads=doc["credit_spreads"],
        liquidity=doc["liquidity"],
        macro_phase=doc["macro_phase"],
    )


def memory_to_doc(memory: RegimeLabeledMemory) -> dict[str, Any]:
    """Pure transform — no IO. Tested independently so the schema
    stays auditable without spinning up Mongo."""
    raw = asdict(memory)
    # asdict turns nested fingerprint dataclasses into nested
    # dicts, which is exactly what we want; no extra work needed.
    raw["persisted_at"] = datetime.now(timezone.utc).isoformat()
    return raw


def doc_to_memory(doc: dict[str, Any]) -> RegimeLabeledMemory:
    """Pure inverse transform. Strips the ``persisted_at`` and
    Mongo ``_id`` fields the engine never sees in-memory."""
    payload = dict(doc)
    payload.pop("_id", None)
    payload.pop("persisted_at", None)

    payload["regime_at_entry"] = _doc_to_fingerprint(
        payload.get("regime_at_entry"),
    )
    payload["regime_at_exit"] = _doc_to_fingerprint(
        payload.get("regime_at_exit"),
    )
    for k in ("pretell_30d", "pretell_60d", "pretell_90d"):
        payload[k] = _doc_to_fingerprint(payload.get(k))

    return RegimeLabeledMemory(**payload)


async def persist_resolved_memory(
    db: Any,
    memory: RegimeLabeledMemory,
) -> dict[str, Any]:
    """Best-effort upsert of a single resolved memory.

    Returns an envelope with ``persisted`` bool and a ``reason``
    when the write was skipped or failed. The envelope is the
    contract — never raises.
    """
    if not _persistence_enabled():
        return {"persisted": False, "reason": "env_flag_off"}
    if db is None:
        return {"persisted": False, "reason": "db_handle_none"}

    try:
        doc = memory_to_doc(memory)
        # Upsert by ``memory_id`` — idempotent under retries.
        await db[COLLECTION_NAME].update_one(
            {"memory_id": memory.memory_id},
            {"$set": doc},
            upsert=True,
        )
        return {"persisted": True, "memory_id": memory.memory_id}
    except Exception as exc:
        # Mongo failures must NEVER bubble — the in-memory engine
        # is the source of truth; persistence is opportunistic.
        logger.warning(
            "learning_core_persistence: write failed memory_id=%s err=%s",
            memory.memory_id,
            exc,
        )
        return {"persisted": False, "reason": str(exc)}


async def rehydrate_resolved_memories(
    db: Any,
    limit: int = 5000,
) -> list[RegimeLabeledMemory]:
    """One-shot startup helper.

    Returns the most-recent ``limit`` resolved memories, oldest-
    first so the caller can replay them through ``add_resolved_memory``
    in chronological order (preserving cluster centroid drift).

    Returns ``[]`` on any failure — the caller decides whether to
    crash or run cold. Default behaviour: run cold; the operator
    sees the warning in logs.
    """
    if db is None:
        return []
    try:
        cursor = db[COLLECTION_NAME].find(
            {},
            {"_id": 0, "persisted_at": 0},
        ).sort("timestamp", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        rows.reverse()  # oldest-first for replay
        return [doc_to_memory(r) for r in rows]
    except Exception as exc:
        logger.warning(
            "learning_core_persistence: rehydrate failed: %s", exc,
        )
        return []
