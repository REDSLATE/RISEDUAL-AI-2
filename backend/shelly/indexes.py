"""Index management for the 5-Shelly federation (Phase 1 polish).

Idempotent ``ensure_indexes()`` — safe to call on every boot. The
RISEDUAL doctrine for index management is:

* Compound (symbol, direction, created_at desc) on every memory
  collection — the exact shape `LocalShelly.reason()` and
  `MCShelly.reason_across_shellys()` query.
* Unique on ``event_hash`` so the idempotent upsert in
  ``LocalShelly.remember()`` actually deduplicates at the engine
  level (not just at the application layer).
* `(created_at desc)` on the receipts collections to make the
  admin "recent-receipts" route O(log N) instead of a full scan.
* `(source_brain)` on the shared MC collection so per-brain
  tallies in cross-Shelly reasoning don't scan everything.

Failure to create any single index is non-fatal: the federation
keeps working, just slower. We log at WARNING so the operator
sees it on first boot but the brain isn't blocked.
"""
from __future__ import annotations

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING

from shelly.config import NODE_NAMES

logger = logging.getLogger(__name__)


async def ensure_indexes(db: Any) -> dict[str, Any]:
    """Create every recommended Shelly index. Idempotent."""
    out: dict[str, list[str]] = {}

    # ── Per-node memory + receipt collections ─────────────────────
    for node in NODE_NAMES:
        mem_coll = f"shelly_{node.lower()}_memories"
        rec_coll = f"shelly_{node.lower()}_reasoning_receipts"
        created_for_node: list[str] = []

        try:
            await db[mem_coll].create_index(
                [("event_hash", ASCENDING)],
                unique=True,
                name="event_hash_unique",
            )
            created_for_node.append("event_hash_unique")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[shelly_indexes] %s event_hash unique failed: %s",
                mem_coll, exc,
            )
        try:
            await db[mem_coll].create_index(
                [
                    ("symbol", ASCENDING),
                    ("direction", ASCENDING),
                    ("created_at", DESCENDING),
                ],
                name="symbol_direction_created",
            )
            created_for_node.append("symbol_direction_created")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[shelly_indexes] %s compound failed: %s", mem_coll, exc,
            )
        try:
            await db[mem_coll].create_index(
                [("rolled_to_mc", ASCENDING), ("created_at", DESCENDING)],
                name="rollup_queue",
            )
            created_for_node.append("rollup_queue")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[shelly_indexes] %s rollup_queue failed: %s",
                mem_coll, exc,
            )
        try:
            await db[rec_coll].create_index(
                [("created_at", DESCENDING)],
                name="created_at_desc",
            )
            created_for_node.append("receipts_created_at_desc")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[shelly_indexes] %s created_at failed: %s",
                rec_coll, exc,
            )
        out[node] = created_for_node

    # ── MC shared collection (cross-Shelly memory pool) ───────────
    shared = "shelly_mc_shared_memory"
    shared_created: list[str] = []
    try:
        await db[shared].create_index(
            [("event_hash", ASCENDING)],
            unique=True,
            name="event_hash_unique",
        )
        shared_created.append("event_hash_unique")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[shelly_indexes] %s event_hash failed: %s", shared, exc,
        )
    try:
        await db[shared].create_index(
            [
                ("symbol", ASCENDING),
                ("direction", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="symbol_direction_created",
        )
        shared_created.append("symbol_direction_created")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[shelly_indexes] %s compound failed: %s", shared, exc,
        )
    try:
        await db[shared].create_index(
            [("source_brain", ASCENDING)],
            name="source_brain",
        )
        shared_created.append("source_brain")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[shelly_indexes] %s source_brain failed: %s", shared, exc,
        )
    out["_mc_shared"] = shared_created

    return {"ok": True, "indexes_created": out}


__all__ = ["ensure_indexes"]
