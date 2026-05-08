"""ETL framework for periodic pulls from external APIs into MongoDB.

Purpose
-------
Some data sources are inherently stale-by-the-time-they-publish
(e.g. US Congress stock disclosures, SEC filings, patent grants).
Hitting them live on every request burns rate-limit budget for no
accuracy gain. The right pattern is:

1. Pull the source on a cron cadence (weekly, biweekly, monthly)
2. Normalize + upsert into a dedicated Mongo collection
3. Serve reads from Mongo (sub-10ms) — no live API hit

This module provides the scaffolding so adding a new source
is roughly ~30 lines of subclass code instead of ~150 lines of
bespoke cron wiring.

Design
------
* **Subclass ``BaseETLJob``**: set four class attributes
  (``source_name``, ``cadence``, ``unique_key_fields``,
  ``description``) and implement ``fetch()``. The base class
  handles upserts, dedup, retention, audit logging, and
  concurrent-run guarding.
* **Retention**: rows auto-expire after ``retention_days`` (default
  180 days) from ``first_seen_at``. Implemented via a MongoDB TTL
  index — the server-side daemon does the cleanup, we do not.
  ``first_seen_at`` is immutable once set (``$setOnInsert``);
  ``last_fetched_at`` updates on every re-fetch for audit clarity.
* **Audit log**: each ``run()`` invocation writes a row to
  ``etl_run_log`` with status, counts, duration, and error (if
  any). Operators query this to answer "when did we last
  refresh X?".
* **Single-pod correctness**: concurrent-run guard is an
  in-process ``asyncio.Lock`` per job. A second invocation while
  the first is in-flight is rejected with ``status="already_running"``.
  If multi-pod deploy ever happens, promote this to a Mongo-backed
  lock with a TTL — contract stays the same.

Non-goals (deliberately out of scope)
-------------------------------------
* Incremental "since-last-run" fetches — most slow-data sources
  don't support it cleanly. We re-pull the full window each tick
  and let the unique-key upsert dedupe.
* Streaming / CDC — these sources all have batch APIs only.
* Cross-source joins — live query code still joins at read time.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

logger = logging.getLogger(__name__)


# Mongo collection holding one row per ETL invocation.
AUDIT_COLLECTION = "etl_run_log"

# Bound on how many audit rows we return from the history endpoint
# and keep around per source. Enough to diagnose a week of runs
# without flooding the admin dashboard payload.
AUDIT_ROWS_PER_SOURCE = 100


# ── Base class ─────────────────────────────────────────────────────


class BaseETLJob(ABC):
    """Subclass this to register a new periodic ETL source.

    Minimal subclass shape::

        @register_etl_job
        class MyJob(BaseETLJob):
            source_name = "my_source_rows"
            description = "Weekly pull of XYZ from the XYZ API"
            cadence = {"day_of_week": "mon", "hour": 4, "minute": 0}
            unique_key_fields = ("foo_id", "bar_date")

            async def fetch(self):
                # Pull from external API; return list of dicts.
                return await my_client.get_batch(...)

    Optional overrides:
    * ``retention_days`` (default 180) — TTL window
    * ``transform(rows)`` — normalize before upsert (default:
      pass-through)
    * ``enabled`` (default True) — flip False to pause scheduling
      without removing the subclass
    """

    # ── subclass MUST set ──
    source_name: ClassVar[str] = ""
    cadence: ClassVar[dict[str, Any]] = {}
    unique_key_fields: ClassVar[tuple[str, ...]] = ()

    # ── subclass MAY override ──
    retention_days: ClassVar[int] = 180
    enabled: ClassVar[bool] = True
    description: ClassVar[str] = ""

    def __init__(self) -> None:
        if not self.source_name:
            raise ValueError(
                f"{type(self).__name__}.source_name must be a non-empty "
                "string — it's used as the Mongo collection name."
            )
        if not self.unique_key_fields:
            raise ValueError(
                f"{type(self).__name__}.unique_key_fields must be a "
                "non-empty tuple. Upsert dedup is keyed on these "
                "fields — define what makes a row unique."
            )
        if not self.cadence:
            raise ValueError(
                f"{type(self).__name__}.cadence must be a non-empty "
                "dict of APScheduler cron trigger kwargs "
                "(e.g. {'day_of_week': 'mon', 'hour': 4})."
            )
        # In-process guard against two runs of the same job
        # stomping each other (manual-trigger during scheduled run,
        # scheduler tick during manual run, etc.).
        self._run_lock: asyncio.Lock = asyncio.Lock()

    # ── Subclass surface ──

    @abstractmethod
    async def fetch(self) -> list[dict[str, Any]]:
        """Pull the current batch from the external source.

        Return a list of row dicts. Network errors, rate-limit
        responses, schema surprises — all raise as normal
        exceptions. The framework catches them and logs the run
        as ``failed`` with the exception type.
        """

    async def transform(
        self, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Optional hook to normalize / enrich before upsert.

        Default: pass-through. Override when the source returns
        e.g. mixed-case tickers or stringified dates that should
        be coerced before hitting Mongo.
        """
        return rows

    # ── Framework: index ensure ──

    async def ensure_indexes(self, db: Any) -> None:
        """Create the unique composite index + TTL index.

        Idempotent — safe to call on every startup. Logs at
        INFO on first creation and at DEBUG on re-runs (Mongo
        treats identical index specs as no-ops).
        """
        coll = db[self.source_name]
        # Unique on the composite key — this is the ``PRIMARY KEY``
        # of the cache. Without it the upsert would create
        # duplicate rows on re-fetches.
        try:
            await coll.create_index(
                [(f, 1) for f in self.unique_key_fields],
                unique=True,
                name=f"{self.source_name}_uniq",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[etl] unique index create failed for %s: %s",
                self.source_name, exc,
            )
        # TTL index on first_seen_at — Mongo daemon drops rows
        # older than ``retention_days``. Indexes on non-date
        # fields are silently ignored by the TTL monitor, so we
        # explicitly index the date field.
        try:
            await coll.create_index(
                "first_seen_at",
                expireAfterSeconds=self.retention_days * 86400,
                name="first_seen_at_ttl",
            )
        except Exception as exc:  # noqa: BLE001
            # If a previous version created the TTL with a
            # different ``expireAfterSeconds``, Mongo rejects
            # the change. Operator can drop the index manually
            # and restart to apply the new retention.
            logger.warning(
                "[etl] TTL index create failed for %s "
                "(drop '%s' index manually to re-apply): %s",
                self.source_name, "first_seen_at_ttl", exc,
            )

    # ── Framework: main run loop ──

    async def run(
        self, db: Any, *, trigger: str = "cron",
    ) -> dict[str, Any]:
        """Full ETL cycle: fetch → transform → upsert → audit.

        Returns a dict with the run summary (``run_id``,
        ``status``, ``fetched``, ``upserted``, ``failed``,
        ``duration_s``). Never raises — errors are captured in
        the return value and written to ``etl_run_log``.

        ``trigger`` documents what initiated this run
        ("cron" / "manual" / "startup") and appears in the
        audit log for diagnosis.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        summary: dict[str, Any] = {
            "run_id": run_id,
            "source_name": self.source_name,
            "trigger": trigger,
            "started_at": started_at,
            "status": "pending",
            "fetched": 0,
            "upserted": 0,
            "failed": 0,
            "error": None,
            "duration_s": 0.0,
        }

        # Reject overlapping runs cleanly. The asyncio.Lock is
        # per-instance, so this holds for the process lifetime.
        if self._run_lock.locked():
            summary["status"] = "already_running"
            summary["error"] = (
                "Another run of this job is in flight; skipping this tick."
            )
            summary["completed_at"] = datetime.now(timezone.utc)
            await _record_audit(db, summary)
            logger.info(
                "[etl:%s] skipped — already_running (trigger=%s)",
                self.source_name, trigger,
            )
            return summary

        t0 = time.perf_counter()
        async with self._run_lock:
            try:
                raw_rows = await self.fetch()
                summary["fetched"] = len(raw_rows)
                rows = await self.transform(raw_rows)
                upserted, failed = await self._bulk_upsert(db, rows)
                summary["upserted"] = upserted
                summary["failed"] = failed
                summary["status"] = "ok" if failed == 0 else "partial"
            except Exception as exc:  # noqa: BLE001
                summary["status"] = "failed"
                summary["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                logger.exception(
                    "[etl:%s] run failed (trigger=%s)",
                    self.source_name, trigger,
                )

        summary["duration_s"] = round(time.perf_counter() - t0, 3)
        summary["completed_at"] = datetime.now(timezone.utc)
        await _record_audit(db, summary)
        logger.info(
            "[etl:%s] %s — fetched=%d upserted=%d failed=%d dur=%.2fs",
            self.source_name, summary["status"],
            summary["fetched"], summary["upserted"],
            summary["failed"], summary["duration_s"],
        )
        return summary

    # ── Framework: bulk upsert ──

    async def _bulk_upsert(
        self, db: Any, rows: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Upsert each row, keyed on ``unique_key_fields``.

        Uses ``$setOnInsert`` to pin ``first_seen_at`` on the
        first write only — the TTL index reads this field and
        drops the row ``retention_days`` later regardless of
        re-fetches. ``last_fetched_at`` updates on every pass so
        operators can see "when did we last confirm this row
        still exists in the source?".

        Returns ``(upserted_count, failed_count)``. Failures are
        per-row — a single malformed row doesn't abort the batch.
        """
        if not rows:
            return (0, 0)

        coll = db[self.source_name]
        now = datetime.now(timezone.utc)
        upserted = 0
        failed = 0

        for row in rows:
            # Build the filter dict from the composite unique key.
            # Missing keys are a fatal schema bug — the row would
            # match every other row via the empty filter.
            try:
                filt = {k: row[k] for k in self.unique_key_fields}
            except KeyError as exc:
                failed += 1
                logger.warning(
                    "[etl:%s] row missing unique key %s: %s",
                    self.source_name, exc, row,
                )
                continue

            try:
                await coll.update_one(
                    filt,
                    {
                        "$set": {
                            **row,
                            "last_fetched_at": now,
                            "source_name": self.source_name,
                        },
                        # Pin first_seen_at on first write only —
                        # this is the TTL anchor. Re-fetches must
                        # NOT reset it or rows would live forever.
                        "$setOnInsert": {"first_seen_at": now},
                    },
                    upsert=True,
                )
                upserted += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning(
                    "[etl:%s] upsert failed (key=%s): %s",
                    self.source_name, filt, exc,
                )

        return (upserted, failed)


# ── Registry ───────────────────────────────────────────────────────


_REGISTRY: dict[str, BaseETLJob] = {}


def register_etl_job(cls: type[BaseETLJob]) -> type[BaseETLJob]:
    """Class decorator — register a subclass with the framework.

    The registered instance is what the scheduler invokes; so the
    subclass MUST be safe to instantiate with no args (default
    ``__init__`` enforces this by validating class attributes).
    """
    instance = cls()
    if instance.source_name in _REGISTRY:
        raise ValueError(
            f"ETL job '{instance.source_name}' is already registered. "
            f"source_name must be globally unique."
        )
    _REGISTRY[instance.source_name] = instance
    logger.debug(
        "[etl] registered %s (retention=%dd, enabled=%s)",
        instance.source_name, instance.retention_days, instance.enabled,
    )
    return cls


def all_jobs() -> list[BaseETLJob]:
    """Return every registered job, in registration order."""
    return list(_REGISTRY.values())


def get_job(source_name: str) -> Optional[BaseETLJob]:
    """Fetch a single registered job by source_name."""
    return _REGISTRY.get(source_name)


def _reset_registry_for_tests() -> None:
    """Test-only — clear the registry between cases so the
    ``register_etl_job`` uniqueness check doesn't leak state."""
    _REGISTRY.clear()


# ── Audit log ──────────────────────────────────────────────────────


async def _record_audit(db: Any, summary: dict[str, Any]) -> None:
    """Persist a run summary to ``etl_run_log``.

    Failure is non-fatal — audit is for humans, not the bot
    loop. Losing one row because Mongo blipped is fine.
    """
    if db is None:
        return
    try:
        await db[AUDIT_COLLECTION].insert_one(
            {k: v for k, v in summary.items() if k != "_id"}
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[etl] audit record failed: %s", exc)


async def get_run_history(
    db: Any, source_name: str, limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent runs for a single source, newest first.

    Capped at ``AUDIT_ROWS_PER_SOURCE`` to keep the admin payload
    bounded regardless of how long the source has been active.
    """
    if db is None:
        return []
    limit = max(1, min(limit, AUDIT_ROWS_PER_SOURCE))
    try:
        cursor = (
            db[AUDIT_COLLECTION]
            .find({"source_name": source_name}, {"_id": 0})
            .sort("started_at", -1)
            .limit(limit)
        )
        rows: list[dict[str, Any]] = []
        async for doc in cursor:
            # Build a fresh row — DO NOT mutate ``doc`` in place.
            # Motor cursors yield fresh dicts per iteration so the
            # mutation would be harmless in production, but the test
            # harness (and any future in-process Mongo mock) may
            # return references. Defensive copy keeps both paths
            # safe.
            out = {k: v for k, v in doc.items()}
            for k in ("started_at", "completed_at"):
                if isinstance(out.get(k), datetime):
                    out[k] = out[k].isoformat()
            rows.append(out)
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("[etl] audit read failed: %s", exc)
        return []


async def get_last_run(
    db: Any, source_name: str,
) -> Optional[dict[str, Any]]:
    """Most recent run for a single source (or None if never ran)."""
    history = await get_run_history(db, source_name, limit=1)
    return history[0] if history else None


async def ensure_audit_indexes(db: Any) -> None:
    """Create the (source_name, started_at) index + audit TTL.

    Audit rows themselves expire after 90 days — keeping forever
    would eventually dominate storage for no operational value.
    The cache rows they describe are already expiring at 180d;
    losing the audit trail 3 months earlier is fine.
    """
    if db is None:
        return
    try:
        await db[AUDIT_COLLECTION].create_index(
            [("source_name", 1), ("started_at", -1)],
            name="etl_audit_source_started_desc",
        )
        await db[AUDIT_COLLECTION].create_index(
            "started_at",
            expireAfterSeconds=90 * 86400,
            name="etl_audit_started_at_ttl",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[etl] audit index ensure failed: %s", exc)
