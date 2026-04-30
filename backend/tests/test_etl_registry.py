"""Tests for the ETL framework (``services.etl_registry``).

Pinned properties:

1. **Subclass contract** — base class rejects subclasses missing
   required class attributes at instantiation.
2. **Happy path** — fetch → transform → upsert writes rows with
   ``first_seen_at`` (pinned on insert only) + ``last_fetched_at``
   (updated every run).
3. **Retention** — TTL index is created on ``first_seen_at`` with
   ``expireAfterSeconds = retention_days * 86400``.
4. **Error capture** — ``fetch()`` raising propagates into the
   run summary as ``status="failed"`` without re-raising.
5. **Per-row fault tolerance** — a single malformed row doesn't
   abort the whole batch.
6. **Concurrent-run guard** — second ``run()`` while the first is
   in flight returns ``status="already_running"``.
7. **Registry** — ``register_etl_job`` uniqueness + lookup.
8. **Audit log** — each run writes one row to ``etl_run_log``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from services.etl_registry import (
    AUDIT_COLLECTION,
    BaseETLJob,
    _reset_registry_for_tests,
    all_jobs,
    get_job,
    get_last_run,
    get_run_history,
    register_etl_job,
)


# ── Test harness ───────────────────────────────────────────────────


class _FakeCollection:
    """In-memory stand-in for a Motor collection — just enough of
    the surface to exercise the framework without a real Mongo."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.indexes: list[dict] = []
        # Inject a fault on the Nth upsert by setting this.
        self.fail_on_upsert_n: int | None = None
        self._upsert_counter = 0

    async def update_one(self, filt, update, upsert=False):
        self._upsert_counter += 1
        if self.fail_on_upsert_n is not None and \
                self._upsert_counter == self.fail_on_upsert_n:
            raise RuntimeError("simulated upsert failure")

        # Locate existing row matching filter
        for row in self.rows:
            if all(row.get(k) == v for k, v in filt.items()):
                # update: $set + $setOnInsert merge semantics
                set_fields = update.get("$set", {})
                row.update(set_fields)
                return

        # No match — insert with $set + $setOnInsert
        new_row = {**filt}
        new_row.update(update.get("$set", {}))
        new_row.update(update.get("$setOnInsert", {}))
        self.rows.append(new_row)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def create_index(self, *args, **kwargs):
        self.indexes.append({"args": args, "kwargs": kwargs})

    def find(self, filt, proj=None):
        matched = [r for r in self.rows if all(r.get(k) == v for k, v in filt.items())]
        return _FakeCursor(matched)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *args, **kwargs):
        if args:
            field, direction = args[0], args[1] if len(args) > 1 else 1
            # Real Mongo auto-handles mixed types; the fake needs
            # help because get_run_history normalises datetimes to
            # ISO strings before returning, but sort() runs on the
            # raw docs BEFORE that normalisation happens. Use a
            # stable sentinel so None / string comparisons don't
            # blow up in tests.
            def _key(r):
                v = r.get(field)
                if v is None:
                    return datetime.min.replace(tzinfo=timezone.utc)
                return v
            self._rows = sorted(self._rows, key=_key, reverse=(direction == -1))
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._idx]
        self._idx += 1
        return row


class _FakeDB:
    def __init__(self):
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _FakeCollection()
        return self.collections[name]


@pytest.fixture(autouse=True)
def _reset_registry():
    """Every test gets a fresh registry — register_etl_job's
    uniqueness check would otherwise leak across cases."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


# ── Subclass contract ──────────────────────────────────────────────


def test_subclass_must_set_source_name():
    """Empty ``source_name`` → ValueError at instantiation so the
    misconfiguration surfaces at startup, not at first cron tick."""
    class NoName(BaseETLJob):
        cadence = {"hour": 4}
        unique_key_fields = ("id",)
        async def fetch(self): return []
    with pytest.raises(ValueError, match="source_name"):
        NoName()


def test_subclass_must_set_unique_key_fields():
    class NoKey(BaseETLJob):
        source_name = "test_nokey"
        cadence = {"hour": 4}
        async def fetch(self): return []
    with pytest.raises(ValueError, match="unique_key_fields"):
        NoKey()


def test_subclass_must_set_cadence():
    class NoCadence(BaseETLJob):
        source_name = "test_nocadence"
        unique_key_fields = ("id",)
        async def fetch(self): return []
    with pytest.raises(ValueError, match="cadence"):
        NoCadence()


def test_fetch_must_be_implemented():
    """``fetch`` is abstract — subclasses that don't implement
    it fail at instantiation, not at first cron tick."""
    class NoFetch(BaseETLJob):
        source_name = "test_nofetch"
        cadence = {"hour": 4}
        unique_key_fields = ("id",)
    with pytest.raises(TypeError):
        NoFetch()  # abstract method not implemented


# ── Registry ───────────────────────────────────────────────────────


def test_register_etl_job_adds_to_registry():
    @register_etl_job
    class J(BaseETLJob):
        source_name = "test_reg_one"
        cadence = {"hour": 4}
        unique_key_fields = ("id",)
        async def fetch(self): return []

    jobs = all_jobs()
    assert len(jobs) == 1
    assert jobs[0].source_name == "test_reg_one"
    assert get_job("test_reg_one") is jobs[0]
    assert get_job("nope") is None


def test_register_etl_job_rejects_duplicate_source_name():
    @register_etl_job
    class J1(BaseETLJob):
        source_name = "test_dup"
        cadence = {"hour": 4}
        unique_key_fields = ("id",)
        async def fetch(self): return []

    with pytest.raises(ValueError, match="already registered"):
        @register_etl_job
        class J2(BaseETLJob):
            source_name = "test_dup"
            cadence = {"hour": 4}
            unique_key_fields = ("id",)
            async def fetch(self): return []


# ── ensure_indexes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_and_ttl():
    """Both indexes must be created: composite unique on key fields,
    TTL on ``first_seen_at`` with correct retention seconds."""
    class J(BaseETLJob):
        source_name = "test_indexes"
        cadence = {"hour": 4}
        unique_key_fields = ("ticker", "trade_date")
        retention_days = 180
        async def fetch(self): return []

    job = J()
    db = _FakeDB()
    await job.ensure_indexes(db)

    coll = db.collections["test_indexes"]
    assert len(coll.indexes) == 2

    # Unique composite index
    uniq = next(i for i in coll.indexes if i["kwargs"].get("unique"))
    assert uniq["args"][0] == [("ticker", 1), ("trade_date", 1)]

    # TTL index with correct expiry (180 days in seconds)
    ttl = next(i for i in coll.indexes if "expireAfterSeconds" in i["kwargs"])
    assert ttl["args"][0] == "first_seen_at"
    assert ttl["kwargs"]["expireAfterSeconds"] == 180 * 86400


@pytest.mark.asyncio
async def test_ensure_indexes_respects_custom_retention():
    """A subclass with ``retention_days=30`` produces a 30-day TTL."""
    class J(BaseETLJob):
        source_name = "test_short_ttl"
        cadence = {"hour": 4}
        unique_key_fields = ("id",)
        retention_days = 30
        async def fetch(self): return []

    job = J()
    db = _FakeDB()
    await job.ensure_indexes(db)
    ttl = next(
        i for i in db.collections["test_short_ttl"].indexes
        if "expireAfterSeconds" in i["kwargs"]
    )
    assert ttl["kwargs"]["expireAfterSeconds"] == 30 * 86400


# ── Run: happy path ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_writes_first_seen_and_last_fetched():
    """On first run: both fields equal the run timestamp.
    On re-run: ``last_fetched_at`` updates; ``first_seen_at`` does NOT."""
    class J(BaseETLJob):
        source_name = "test_happy"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        async def fetch(self):
            return [{"rid": "r1", "payload": "v1"}]

    job = J()
    db = _FakeDB()

    # First run
    summary = await job.run(db)
    assert summary["status"] == "ok"
    assert summary["fetched"] == 1
    assert summary["upserted"] == 1
    assert summary["failed"] == 0

    row = db.collections["test_happy"].rows[0]
    first_seen_run1 = row["first_seen_at"]
    last_fetched_run1 = row["last_fetched_at"]
    assert isinstance(first_seen_run1, datetime)
    assert first_seen_run1.tzinfo is not None

    # Second run — sleep a tiny bit so timestamps differ
    await asyncio.sleep(0.01)
    await job.run(db)

    row = db.collections["test_happy"].rows[0]
    assert row["first_seen_at"] == first_seen_run1, (
        "first_seen_at must be IMMUTABLE — TTL anchor must not reset on re-fetch."
    )
    assert row["last_fetched_at"] > last_fetched_run1


@pytest.mark.asyncio
async def test_run_dedupes_via_unique_key():
    """Two batches with the same unique key produce ONE row."""
    class J(BaseETLJob):
        source_name = "test_dedupe"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        calls: int = 0
        async def fetch(self):
            J.calls += 1
            return [
                {"rid": "r1", "val": f"v{J.calls}"},
                {"rid": "r2", "val": f"v{J.calls}"},
            ]

    job = J()
    db = _FakeDB()
    await job.run(db)
    await job.run(db)

    rows = db.collections["test_dedupe"].rows
    assert len(rows) == 2  # no duplicates
    # Values reflect the SECOND fetch (last-write-wins on non-key fields)
    assert all(r["val"] == "v2" for r in rows)


@pytest.mark.asyncio
async def test_transform_hook_is_applied():
    """Subclass ``transform()`` is called between fetch and upsert."""
    class J(BaseETLJob):
        source_name = "test_transform"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        async def fetch(self):
            return [{"rid": "r1", "ticker": "aapl"}]
        async def transform(self, rows):
            return [{**r, "ticker": r["ticker"].upper()} for r in rows]

    job = J()
    db = _FakeDB()
    await job.run(db)

    row = db.collections["test_transform"].rows[0]
    assert row["ticker"] == "AAPL", "transform() must run before upsert"


# ── Run: error capture ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_exception_becomes_failed_summary():
    """Network / schema errors from fetch must NOT raise out of
    run() — they must be captured in the summary."""
    class J(BaseETLJob):
        source_name = "test_fetch_fail"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        async def fetch(self):
            raise RuntimeError("API down")

    job = J()
    db = _FakeDB()
    summary = await job.run(db)

    assert summary["status"] == "failed"
    assert "RuntimeError" in summary["error"]
    assert "API down" in summary["error"]
    assert summary["fetched"] == 0
    assert summary["upserted"] == 0


@pytest.mark.asyncio
async def test_malformed_row_does_not_abort_batch():
    """A single row with a missing unique key is counted as
    ``failed`` but the rest of the batch still upserts."""
    class J(BaseETLJob):
        source_name = "test_partial"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        async def fetch(self):
            return [
                {"rid": "good1", "val": 1},
                {"val": 2},  # missing unique key
                {"rid": "good2", "val": 3},
            ]

    job = J()
    db = _FakeDB()
    summary = await job.run(db)

    assert summary["status"] == "partial"
    assert summary["fetched"] == 3
    assert summary["upserted"] == 2
    assert summary["failed"] == 1
    # Only the two good rows landed
    assert len(db.collections["test_partial"].rows) == 2


@pytest.mark.asyncio
async def test_upsert_exception_counted_as_failed():
    """A Mongo-layer failure on one row doesn't crash the batch."""
    class J(BaseETLJob):
        source_name = "test_mongo_fail"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        async def fetch(self):
            return [{"rid": "r1"}, {"rid": "r2"}, {"rid": "r3"}]

    job = J()
    db = _FakeDB()
    db["test_mongo_fail"].fail_on_upsert_n = 2  # fail the second upsert

    summary = await job.run(db)
    assert summary["status"] == "partial"
    assert summary["upserted"] == 2
    assert summary["failed"] == 1


# ── Run: concurrent-run guard ─────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_run_rejected():
    """Second run while the first is in flight returns
    ``already_running`` without re-entering ``fetch()``."""
    fetch_event = asyncio.Event()
    fetch_hits = 0

    class J(BaseETLJob):
        source_name = "test_concurrent"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        async def fetch(self):
            nonlocal fetch_hits
            fetch_hits += 1
            await fetch_event.wait()  # hold the lock
            return [{"rid": "r1"}]

    job = J()
    db = _FakeDB()

    # Start the first run in the background — it will block at fetch_event.
    task = asyncio.create_task(job.run(db))
    # Give the task a moment to take the lock.
    await asyncio.sleep(0.01)
    # Try a second run — should be rejected cleanly.
    second = await job.run(db, trigger="manual")
    assert second["status"] == "already_running"
    assert fetch_hits == 1  # second run did NOT call fetch

    # Let the first run complete.
    fetch_event.set()
    first = await task
    assert first["status"] == "ok"


# ── Audit log ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_writes_audit_row():
    """Every run (ok / failed / already_running) writes one audit row."""
    class J(BaseETLJob):
        source_name = "test_audit"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        async def fetch(self):
            return [{"rid": "r1"}]

    job = J()
    db = _FakeDB()
    await job.run(db)
    await job.run(db, trigger="manual")

    audit_rows = db.collections[AUDIT_COLLECTION].rows
    assert len(audit_rows) == 2
    assert audit_rows[0]["source_name"] == "test_audit"
    assert audit_rows[0]["trigger"] == "cron"
    assert audit_rows[1]["trigger"] == "manual"
    assert all(r["status"] == "ok" for r in audit_rows)


@pytest.mark.asyncio
async def test_get_last_run_returns_most_recent():
    """History query returns runs newest-first; ``get_last_run``
    returns the head."""
    class J(BaseETLJob):
        source_name = "test_history"
        cadence = {"hour": 4}
        unique_key_fields = ("rid",)
        async def fetch(self):
            return []

    job = J()
    db = _FakeDB()
    await job.run(db, trigger="cron")
    await asyncio.sleep(0.01)
    await job.run(db, trigger="manual")

    last = await get_last_run(db, "test_history")
    assert last is not None
    assert last["trigger"] == "manual"

    history = await get_run_history(db, "test_history", limit=5)
    assert len(history) == 2
    assert history[0]["trigger"] == "manual"  # newest first
    assert history[1]["trigger"] == "cron"


@pytest.mark.asyncio
async def test_get_last_run_none_when_never_ran():
    """A freshly registered job with no runs yet returns None."""
    db = _FakeDB()
    result = await get_last_run(db, "never_ran")
    assert result is None
