"""Migration runner — runs registered migrations exactly once per
deployment.

Each migration is identified by a unique ``id`` string. We record
successful runs in the ``migrations`` collection so a migration
that's already been applied is a no-op on subsequent boots. New
migrations added in code automatically run on the next boot.

Design:
  * Idempotent: failures don't write the success row, so the next
    boot re-attempts.
  * Fail-open: a migration error logs and returns — never crashes
    startup. The product is more useful with a warning than dead.
  * Auditable: every successful run records timestamp, duration,
    and a small summary dict supplied by the migration body.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

_COLLECTION = "migrations"

# Symbol prefixes that mark a row as test-fixture pollution. Imported
# from the canonical source-of-truth in `market_memory_service` so
# we never have two diverging lists.
from services.market_memory_service import _TEST_SYMBOL_PREFIXES  # noqa: E402

# Compiled once. Matches at SYMBOL START so we don't false-positive
# on legitimate tickers that happen to contain "TEST" mid-string.
_TEST_SYMBOL_REGEX = re.compile(
    r"^(?:" + "|".join(re.escape(p) for p in _TEST_SYMBOL_PREFIXES)
    + r"|FAKEXYZ|TEST\d+$)",
    re.IGNORECASE,
)


async def _migration_already_applied(db: Any, mid: str) -> bool:
    if db is None:
        return True  # treat missing DB as "already applied" → skip
    try:
        doc = await db[_COLLECTION].find_one({"_id": mid})
        return doc is not None
    except Exception as exc:
        logger.warning(f"[migrations] applied-lookup failed for {mid}: {exc}")
        return True  # fail-safe — don't re-run if we can't tell


async def _record_success(db: Any, mid: str, duration_s: float, summary: dict) -> None:
    if db is None:
        return
    try:
        await db[_COLLECTION].insert_one({
            "_id": mid,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(duration_s, 3),
            "summary": summary,
        })
    except Exception as exc:
        logger.warning(f"[migrations] success record failed for {mid}: {exc}")


async def _scrub_test_contamination(db: Any) -> dict:
    """Migration body — purges test-fixture rows from every collection
    that's known to have leaked. Runs once per deploy via the
    migration runner; safe to no-op on a clean DB."""
    summary: dict[str, int] = {}

    # Document collections — full row delete by symbol prefix.
    targets = [
        ("predictions", "symbol", False),
        ("trade_ideas", "symbol", False),
        ("trades", "ticker", False),
        ("signals", "ticker", False),
    ]
    for cname, field, _ in targets:
        try:
            res = await db[cname].delete_many({field: _TEST_SYMBOL_REGEX})
            if res.deleted_count:
                summary[cname] = res.deleted_count
                logger.warning(
                    f"[migration:scrub] {cname}.{field}: deleted "
                    f"{res.deleted_count} test-fixture row(s)"
                )
        except Exception as exc:
            logger.warning(f"[migration:scrub] {cname} delete failed: {exc}")

    # Array-field collections — $pull individual entries; keep parent.
    arr_targets = [
        ("watchlists", "tickers"),
        ("alerts_sent", "tickers"),
    ]
    for cname, field in arr_targets:
        try:
            count = await db[cname].count_documents({field: _TEST_SYMBOL_REGEX})
            if count:
                res = await db[cname].update_many(
                    {field: _TEST_SYMBOL_REGEX},
                    {"$pull": {field: {"$regex": _TEST_SYMBOL_REGEX}}},
                )
                summary[f"{cname}_pulled"] = res.modified_count
                logger.warning(
                    f"[migration:scrub] {cname}.{field}: pulled test "
                    f"entries from {res.modified_count} doc(s)"
                )
        except Exception as exc:
            logger.warning(f"[migration:scrub] {cname} pull failed: {exc}")

    return summary


# ── Registry ──
# (id, description, body). Add new migrations to the bottom; ids
# must be unique forever (we record them in `migrations` so a
# rename = re-run).
_MIGRATIONS: list[tuple[str, str, Callable[[Any], Awaitable[dict]]]] = [
    (
        "2026-04-24-scrub-test-contamination",
        "One-shot scrub of TEST_*/FAKEXYZ/MOCK_*/etc fixtures that "
        "leaked from integration tests into prod collections "
        "(predictions, trade_ideas, trades, signals, watchlists, "
        "alerts_sent). Counterpart to scripts/scrub_test_contamination.py.",
        _scrub_test_contamination,
    ),
]


async def run_pending_migrations(db: Any) -> dict:
    """Run every registered migration that hasn't already been
    applied. Called from the startup event. Never raises."""
    if db is None:
        return {"ran": 0, "skipped": 0, "errors": 0}

    ran = 0
    skipped = 0
    errors = 0
    results: list[dict] = []

    for mid, desc, body in _MIGRATIONS:
        try:
            if await _migration_already_applied(db, mid):
                skipped += 1
                continue
            logger.info(f"[migrations] applying {mid}: {desc}")
            t0 = time.monotonic()
            summary = await body(db)
            duration = time.monotonic() - t0
            await _record_success(db, mid, duration, summary)
            ran += 1
            results.append({"id": mid, "duration_s": round(duration, 3),
                            "summary": summary})
            logger.info(f"[migrations] ✅ {mid} applied in {duration:.2f}s — {summary}")
        except Exception as exc:
            errors += 1
            logger.exception(f"[migrations] ❌ {mid} failed: {exc}")
            results.append({"id": mid, "error": str(exc)})

    return {"ran": ran, "skipped": skipped, "errors": errors, "results": results}
