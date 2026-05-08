"""One-shot remediation script for the 2026-04-24 test-contamination
cascade. Walks every record in the ChromaDB `market_regimes`
collection, finds rows whose metadata.symbol matches a test prefix,
and deletes them outright (these are not real market data — they
should never have been in the production memory store).

Safe to re-run: idempotent, logs what it touched, exits non-zero on
error so a deploy script can detect failure.

Usage:
    cd /app/backend && python scripts/scrub_test_contamination.py
    cd /app/backend && DRY_RUN=1 python scripts/scrub_test_contamination.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("scrub")


def main() -> int:
    from services.market_memory_service import (
        _TEST_SYMBOL_PREFIXES,
        init_memory,
    )
    import services.market_memory_service as mms
    import asyncio
    import re
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes", "on")

    # ── ChromaDB sweep (vector memory) ──
    init_memory()
    coll = mms._collection
    if coll is None:
        logger.error("ChromaDB collection not initialised — abort")
        return 2

    total_before = coll.count()
    logger.info(f"[chromadb] Scanning {total_before} rows for test contamination…")
    results = coll.get(include=["metadatas"])
    ids = results.get("ids", [])
    metas = results.get("metadatas", [])

    bad_ids: list[str] = []
    bad_symbols: dict[str, int] = {}
    for tid, meta in zip(ids, metas):
        sym = (meta or {}).get("symbol")
        if not sym:
            continue
        if str(sym).upper().startswith(_TEST_SYMBOL_PREFIXES):
            bad_ids.append(tid)
            bad_symbols[str(sym)] = bad_symbols.get(str(sym), 0) + 1

    if bad_ids:
        logger.warning(
            f"[chromadb] Found {len(bad_ids)} contaminated row(s). "
            f"Top: {sorted(bad_symbols.items(), key=lambda kv: -kv[1])[:10]}"
        )
        if not dry_run:
            coll.delete(ids=bad_ids)
            logger.info(f"[chromadb] ✅ Deleted {len(bad_ids)} test row(s). "
                        f"{total_before} → {coll.count()}")
    else:
        logger.info("[chromadb] ✅ Clean")

    # ── MongoDB sweep ──
    # Each (collection, field) pair below was confirmed to leak by the
    # 2026-04-24 contamination audit. Adding a new leaking collection?
    # Add the (collection, field, regex) tuple here and re-run.
    pat = re.compile(
        r"^(TEST_|MOCK_|FAKE_|DUMMY_|FIXTURE_|FAKEXYZ|TEST\d+$)",
        re.IGNORECASE,
    )

    async def _scrub_mongo() -> int:
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        # (collection, field_name, is_array)
        targets = [
            ("predictions", "symbol", False),
            ("trade_ideas", "symbol", False),
            ("trades", "ticker", False),
            ("signals", "ticker", False),
            ("alerts_sent", "tickers", True),
            ("watchlists", "tickers", True),
        ]

        deleted_total = 0
        for cname, field, is_array in targets:
            if is_array:
                # For array fields, $pull the bad entries — keep the
                # parent row intact (a watchlist with mostly real
                # tickers + a few TEST entries should retain the
                # real ones).
                count = await db[cname].count_documents({field: pat})
                if count == 0:
                    continue
                # Sample to log what we're touching
                async for r in db[cname].find(
                    {field: pat}, {"_id": 0, field: 1, "user_id": 1}
                ).limit(3):
                    logger.warning(f"[{cname}] sample: {r}")
                if not dry_run:
                    res = await db[cname].update_many(
                        {field: pat}, {"$pull": {field: {"$regex": pat}}}
                    )
                    logger.info(
                        f"[{cname}] ✅ Pulled test entries from "
                        f"{res.modified_count} doc(s)"
                    )
                else:
                    logger.info(f"[{cname}] DRY_RUN — would scrub {count} doc(s)")
            else:
                count = await db[cname].count_documents({field: pat})
                if count == 0:
                    continue
                # Log a sample
                async for r in db[cname].find(
                    {field: pat}, {"_id": 0, field: 1, "user_id": 1, "created_at": 1}
                ).limit(3):
                    logger.warning(f"[{cname}] sample: {r}")
                if not dry_run:
                    res = await db[cname].delete_many({field: pat})
                    deleted_total += res.deleted_count
                    logger.info(
                        f"[{cname}] ✅ Deleted {res.deleted_count} row(s)"
                    )
                else:
                    logger.info(f"[{cname}] DRY_RUN — would delete {count} row(s)")
        return deleted_total

    asyncio.get_event_loop().run_until_complete(_scrub_mongo())
    return 0


if __name__ == "__main__":
    sys.exit(main())
