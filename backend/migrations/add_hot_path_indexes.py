"""Migration: add indexes to hot-path collections.

Why this exists: predictions (2,260 docs) and shelly_memories (17,126 docs)
were running every query as a full-collection scan. The AI Cache + Hypothesis
hot paths regularly filter by ``symbol`` / ``user_id`` / ``metadata.source``
/ ``created_at`` — those queries should be index-backed.

Idempotent. Safe to re-run. Each ``create_index`` swallows duplicate-name
errors so an upgrade on top of partial state never aborts the rest.

Run automatically at startup via the migration runner (see
``services.startup_migrations``).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("migrations.add_hot_path_indexes")


# Spec: (collection, [(keys, name, opts)], ...)
SPECS: dict[str, list[dict]] = {
    "predictions": [
        # Hottest path — accuracy badge / prediction list filtering by symbol.
        {"keys": [("symbol", 1), ("created_at", -1)], "name": "predictions_symbol_created"},
        {"keys": [("user_id", 1), ("created_at", -1)], "name": "predictions_user_created"},
        {"keys": [("prediction_type", 1)], "name": "predictions_type"},
        {"keys": [("created_at", -1)], "name": "predictions_created_desc"},
    ],
    "shelly_memories": [
        # Memory queries are filtered by source + lane, sorted by time.
        {"keys": [("metadata.source", 1), ("created_at", -1)],
         "name": "memories_source_created"},
        {"keys": [("label", 1)], "name": "memories_label"},
        {"keys": [("id", 1)], "name": "memories_external_id"},
        {"keys": [("created_at", -1)], "name": "memories_created_desc"},
    ],
    "hypothesis_logs": [
        {"keys": [("symbol", 1), ("created_at", -1)],
         "name": "hyplogs_symbol_created"},
    ],
    "market_memories": [
        {"keys": [("symbol", 1), ("created_at", -1)],
         "name": "marketmem_symbol_created"},
    ],
    "shelly_legacy_malformed": [
        # The malformed-quarantine admin UI sorts by quarantined_at.
        {"keys": [("quarantined_at", -1)], "name": "malformed_qa_desc"},
        {"keys": [("doc_number", 1)], "name": "malformed_docnum"},
    ],
}


async def run(db) -> dict:
    """Create the index set. Returns a stats dict.

    Each index is created in its own try/except so a conflict on one
    (e.g. an old auto-named index from before this migration) doesn't
    short-circuit the rest of the batch.
    """
    if db is None:
        return {"ok": False, "reason": "no db"}

    created = 0
    skipped = 0
    failures: list[str] = []

    for coll_name, specs in SPECS.items():
        collection = db[coll_name]
        for spec in specs:
            try:
                await collection.create_index(
                    spec["keys"],
                    name=spec["name"],
                    background=True,
                )
                created += 1
                logger.info(
                    "[indexes] created %s on %s", spec["name"], coll_name,
                )
            except Exception as e:  # noqa: BLE001 — index creation can throw
                # OperationFailure with code 85 = IndexOptionsConflict (already
                # exists with same shape). We treat that as success.
                msg = str(e)
                if "already exists" in msg.lower() or "IndexOptionsConflict" in msg:
                    skipped += 1
                else:
                    failures.append(f"{coll_name}.{spec['name']}: {msg[:140]}")
                    logger.warning(
                        "[indexes] %s.%s failed: %s",
                        coll_name, spec["name"], msg[:200],
                    )

    return {
        "ok": not failures,
        "created": created,
        "skipped_existing": skipped,
        "failures": failures,
    }
