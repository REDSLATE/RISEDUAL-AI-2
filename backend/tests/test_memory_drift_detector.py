"""Tests for the Mongo→Chroma drift detector + sync metrics.

The drift detector is the operator's early-warning channel — without
it, the next sync regression would silently corrupt ChromaDB until
someone noticed visibly broken output downstream (which is how the
``[:10]`` slice bug went unspotted for months).

Scope:
* ``record_skip`` increments + logs at WARN
* ``mark_rebuild`` stamps a tz-aware UTC timestamp
* ``_classify`` thresholds are bucket-correct
* The ``/api/admin/memory/drift`` route logic — given mocked Mongo
  + Chroma counts, returns the right shape and recommendation

The actual ChromaDB / Mongo connections aren't exercised here; the
underlying coercion is already tested in
``test_mongo_to_chroma_sync.py``. This file pins the observability
layer.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


def setup_function():
    """Reset the in-process counters + cache before each test so
    state doesn't bleed across cases."""
    from services.mongo_chroma_sync_metrics import (
        _reset_cache_for_tests, reset_counters,
    )
    reset_counters()
    _reset_cache_for_tests()


# ── record_skip ─────────────────────────────────────────────────────


def test_record_skip_increments_counter():
    from services.mongo_chroma_sync_metrics import (
        get_skip_counters, record_skip,
    )
    record_skip("rebuild_save_failed")
    record_skip("rebuild_save_failed")
    record_skip("warmup_save_failed")
    counters = get_skip_counters()
    assert counters["rebuild_save_failed"] == 2
    assert counters["warmup_save_failed"] == 1


def test_record_skip_logs_at_warn(caplog):
    from services.mongo_chroma_sync_metrics import record_skip
    import logging

    with caplog.at_level(logging.WARNING, logger="services.mongo_chroma_sync_metrics"):
        record_skip(
            "rebuild_save_failed",
            doc_id="pred-abc",
            exc=ValueError("boom"),
        )
    assert any(
        "[mongo_chroma_sync] skipped" in r.message
        and "rebuild_save_failed" in r.message
        and "ValueError" in r.message
        for r in caplog.records
    )


# ── mark_rebuild / get_last_rebuild ─────────────────────────────────


def test_mark_rebuild_stamps_tz_aware_utc():
    """``mark_rebuild`` is async-now (Mongo-backed). Verify the
    in-process cache is populated with a tz-aware UTC timestamp
    that survives the ISO round-trip in ``get_last_rebuild()``."""
    import asyncio
    from unittest.mock import patch
    from services.mongo_chroma_sync_metrics import (
        get_last_rebuild, mark_rebuild,
    )

    # Force the lazy db lookup to return None — exercises the
    # in-process-only fallback path. (The Mongo persistence path
    # has its own dedicated test below.)
    with patch("services.mongo_chroma_sync_metrics._get_db", return_value=None):
        asyncio.run(mark_rebuild(rebuilt=42, skipped=3, since="2026-04-01"))
        out = asyncio.run(get_last_rebuild())

    assert out["last_rebuild_summary"] == {
        "rebuilt": 42, "skipped": 3, "since": "2026-04-01",
    }
    parsed = datetime.fromisoformat(out["last_rebuild_at"])
    assert parsed.tzinfo is not None


@pytest.mark.asyncio
async def test_mark_rebuild_persists_to_mongo_and_get_reads_it():
    """End-to-end: ``mark_rebuild`` writes to Mongo;
    ``get_last_rebuild`` reads it back even after the in-process
    cache is cleared (simulating a backend restart)."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from services.mongo_chroma_sync_metrics import (
        _reset_cache_for_tests, get_last_rebuild, mark_rebuild,
    )

    captured: dict[str, object] = {}

    async def fake_update_one(query, update, upsert=False):
        captured["query"] = query
        captured["update"] = update
        captured["upsert"] = upsert
        return MagicMock()

    async def fake_find_one(query, projection=None):
        # Return what update_one wrote, minus the _id, mimicking
        # Mongo round-trip behaviour (tzinfo would be stripped here
        # — the helper uses ensure_utc to re-tag).
        u = captured.get("update", {}).get("$set", {})
        return {
            "last_rebuild_at": u["last_rebuild_at"].replace(tzinfo=None),
            "last_rebuild_summary": u["last_rebuild_summary"],
        }

    fake_db = MagicMock()
    fake_collection = MagicMock()
    fake_collection.update_one = AsyncMock(side_effect=fake_update_one)
    fake_collection.find_one = AsyncMock(side_effect=fake_find_one)
    fake_db.__getitem__ = MagicMock(return_value=fake_collection)

    with patch(
        "services.mongo_chroma_sync_metrics._get_db",
        return_value=fake_db,
    ):
        await mark_rebuild(rebuilt=101, skipped=0, since="2026-03-31")
        # Simulate restart — cache wiped, Mongo unchanged.
        _reset_cache_for_tests()
        out = await get_last_rebuild()

    assert out["last_rebuild_summary"] == {
        "rebuilt": 101, "skipped": 0, "since": "2026-03-31",
    }
    assert out["last_rebuild_at"] is not None
    parsed = datetime.fromisoformat(out["last_rebuild_at"])
    assert parsed.tzinfo is not None  # tz-naive Mongo round-trip re-tagged

    # Upsert was called with the right shape.
    assert captured["query"] == {"_id": "mongo_chroma"}
    assert captured["upsert"] is True


@pytest.mark.asyncio
async def test_mark_rebuild_persistence_failure_does_not_raise():
    """Mongo write failure must NOT raise — the rebuild has
    already succeeded by the time we're called. Worst case the
    timestamp is held in-process only until the next rebuild."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from services.mongo_chroma_sync_metrics import (
        get_last_rebuild, mark_rebuild,
    )

    fake_db = MagicMock()
    fake_collection = MagicMock()
    fake_collection.update_one = AsyncMock(
        side_effect=RuntimeError("boom"),
    )
    fake_db.__getitem__ = MagicMock(return_value=fake_collection)

    with patch(
        "services.mongo_chroma_sync_metrics._get_db",
        return_value=fake_db,
    ):
        # Must not raise.
        await mark_rebuild(rebuilt=10, skipped=0)
        out = await get_last_rebuild()

    # In-process cache still serves the value.
    assert out["last_rebuild_summary"]["rebuilt"] == 10
    assert out["last_rebuild_at"] is not None


@pytest.mark.asyncio
async def test_get_last_rebuild_handles_empty_mongo_state():
    """Cold start with no prior rebuild on record — endpoint
    returns ``{None, None}`` instead of raising or stalling."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from services.mongo_chroma_sync_metrics import get_last_rebuild

    fake_db = MagicMock()
    fake_collection = MagicMock()
    fake_collection.find_one = AsyncMock(return_value=None)
    fake_db.__getitem__ = MagicMock(return_value=fake_collection)

    with patch(
        "services.mongo_chroma_sync_metrics._get_db",
        return_value=fake_db,
    ):
        out = await get_last_rebuild()

    assert out == {"last_rebuild_at": None, "last_rebuild_summary": None}


# ── classify thresholds ─────────────────────────────────────────────


@pytest.mark.parametrize("pct, expected", [
    (0.0, "ok"),
    (0.5, "ok"),
    (0.99, "ok"),
    (1.0, "investigate"),
    (5.0, "investigate"),
    (9.99, "investigate"),
    (10.0, "rebuild"),
    (50.0, "rebuild"),
    (100.0, "rebuild"),
])
def test_classify_thresholds(pct, expected):
    from routes.admin_memory_drift import _classify
    assert _classify(pct) == expected


# ── End-to-end drift endpoint logic ─────────────────────────────────


@pytest.mark.asyncio
async def test_drift_endpoint_no_drift():
    """When Mongo and Chroma counts match per-date, drift is 0 and
    recommendation is ``ok``."""
    from unittest.mock import patch

    fake_mongo = {"2026-04-28": 10, "2026-04-29": 12, "2026-04-30": 8}
    fake_chroma = {"2026-04-28": 10, "2026-04-29": 12, "2026-04-30": 8}

    from routes import admin_memory_drift as mod
    mod._db = object()  # truthy non-None for the gate

    with patch.object(mod, "_mongo_per_date_counts", return_value=fake_mongo), \
         patch.object(mod, "_chroma_per_date_counts", return_value=fake_chroma):
        # Skip the auth check — directly exercise the function body.
        from fastapi import Request
        request = Request({"type": "http", "headers": []})
        with patch.object(mod, "_require_owner", return_value={}):
            out = await mod.memory_drift(request, days=30, top_skew=10)

    assert out["available"] is True
    assert out["mongo_verified_count"] == 30
    assert out["chroma_episode_count"] == 30
    assert out["drift"] == 0
    assert out["drift_pct"] == 0.0
    assert out["recommendation"] == "ok"
    assert out["by_date_top_skew"] == []


@pytest.mark.asyncio
async def test_drift_endpoint_localizes_per_date_skew():
    """The whole point of the per-date breakdown: when 2026-04-21
    is the regression window, that date should be the top-skew row."""
    from unittest.mock import patch

    fake_mongo = {"2026-04-21": 412, "2026-04-22": 50, "2026-04-23": 50}
    fake_chroma = {"2026-04-21": 12, "2026-04-22": 50, "2026-04-23": 50}
    # Drift = 400 / 512 = 78% → recommendation "rebuild".

    from routes import admin_memory_drift as mod
    mod._db = object()

    with patch.object(mod, "_mongo_per_date_counts", return_value=fake_mongo), \
         patch.object(mod, "_chroma_per_date_counts", return_value=fake_chroma):
        from fastapi import Request
        request = Request({"type": "http", "headers": []})
        with patch.object(mod, "_require_owner", return_value={}):
            out = await mod.memory_drift(request, days=30, top_skew=10)

    assert out["recommendation"] == "rebuild"
    assert out["mongo_verified_count"] == 512
    assert out["chroma_episode_count"] == 112
    assert out["drift"] == 400
    # Top skew row points at the regression window.
    top = out["by_date_top_skew"][0]
    assert top["date"] == "2026-04-21"
    assert top["mongo"] == 412
    assert top["chroma"] == 12
    assert top["skew"] == 400


@pytest.mark.asyncio
async def test_drift_endpoint_unavailable_when_db_none():
    from routes import admin_memory_drift as mod
    from unittest.mock import patch
    mod._db = None
    from fastapi import Request
    request = Request({"type": "http", "headers": []})
    with patch.object(mod, "_require_owner", return_value={}):
        out = await mod.memory_drift(request, days=30, top_skew=10)
    assert out == {"available": False, "reason": "db_unavailable"}


# ── Date-field alignment with rebuild endpoint ─────────────────────


@pytest.mark.asyncio
async def test_mongo_per_date_counts_filters_on_verified_at_and_buckets_on_timestamp():
    """The drift endpoint must:
    1. Filter Mongo on ``verified_24h.verified_at`` (NOT
       ``prediction_date``) so it counts the same rows the
       rebuild endpoint touches.
    2. Bucket on the date portion of ``timestamp`` via
       ``to_iso_date``, NOT ``prediction_date`` — that field is
       ``None`` on older rows and produced one giant null
       bucket that the dashboard then dropped, hiding all data.

    Pre-fix flow: rebuild ran, processed 101 rows, dashboard
    still showed mongo=0 because the aggregation grouped on a
    null key."""
    from unittest.mock import MagicMock, patch
    from routes import admin_memory_drift as mod

    captured: dict[str, object] = {}

    class _Cursor:
        def __init__(self, rows):
            self._rows = list(rows)

        def __aiter__(self):
            self._iter = iter(self._rows)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    def fake_find(query, projection=None):
        captured["query"] = query
        captured["projection"] = projection
        # Simulate the production reality: ``prediction_date`` is
        # None on older rows, but ``timestamp`` is always set. Mix
        # in one row that DOES have prediction_date so the helper
        # exercises both branches.
        return _Cursor([
            {"timestamp": "2026-04-15T09:30:00+00:00", "prediction_date": None},
            {"timestamp": "2026-04-15T15:00:00+00:00", "prediction_date": None},
            {"timestamp": "2026-04-20T10:00:00+00:00", "prediction_date": "2026-04-20"},
        ])

    fake_db = MagicMock()
    fake_db.predictions = MagicMock()
    fake_db.predictions.find = MagicMock(side_effect=fake_find)

    with patch.object(mod, "_db", fake_db):
        out = await mod._mongo_per_date_counts(days=30)

    # The filter must use verified_at (the rebuild's filter), not
    # prediction_date (which would miss rows where the field is
    # null).
    q = captured["query"]
    assert "verified_24h.verified_at" in q
    assert "prediction_date" not in q
    # Buckets reflect the date portion of timestamp regardless of
    # whether prediction_date is None or set.
    assert out == {"2026-04-15": 2, "2026-04-20": 1}



@pytest.mark.asyncio
async def test_drift_endpoint_includes_sync_metrics():
    """The drift response must surface skip counters + last-rebuild
    so the operator can interpret raw drift % in context."""
    from unittest.mock import patch

    from services.mongo_chroma_sync_metrics import (
        mark_rebuild, record_skip, reset_counters,
        _reset_cache_for_tests,
    )
    reset_counters()
    _reset_cache_for_tests()
    record_skip("warmup_save_failed")
    record_skip("rebuild_save_failed")
    record_skip("rebuild_save_failed")
    # Force the in-process-only path so the test doesn't depend on
    # a real Mongo connection.
    with patch("services.mongo_chroma_sync_metrics._get_db", return_value=None):
        await mark_rebuild(rebuilt=100, skipped=2, since="2026-04-15")

    from routes import admin_memory_drift as mod
    mod._db = object()

    with patch.object(mod, "_mongo_per_date_counts", return_value={"2026-04-30": 5}), \
         patch.object(mod, "_chroma_per_date_counts", return_value={"2026-04-30": 5}):
        from fastapi import Request
        request = Request({"type": "http", "headers": []})
        with patch.object(mod, "_require_owner", return_value={}):
            out = await mod.memory_drift(request, days=30, top_skew=10)

    assert out["sync_skipped_total"] == {
        "warmup_save_failed": 1, "rebuild_save_failed": 2,
    }
    assert out["last_rebuild_summary"]["rebuilt"] == 100
    assert out["last_rebuild_summary"]["since"] == "2026-04-15"
    assert out["last_rebuild_at"] is not None
    # The endpoint must surface which Mongo field it filtered on
    # so the operator can reconcile against the rebuild endpoint.
    assert out["window_field"] == "verified_24h.verified_at"


@pytest.mark.asyncio
async def test_drift_endpoint_negative_drift_is_benign():
    """Chroma > Mongo (negative drift) is the bulk-training case —
    yfinance regimes that never had a matching prediction. The
    recommendation classifier MUST treat this as ``ok`` so the
    operator isn't paged by training-volume noise."""
    from unittest.mock import patch

    fake_mongo = {"2026-04-30": 5}
    fake_chroma = {"2026-04-30": 50, "2026-04-15": 100}  # 145 extras

    from routes import admin_memory_drift as mod
    mod._db = object()

    with patch.object(mod, "_mongo_per_date_counts", return_value=fake_mongo), \
         patch.object(mod, "_chroma_per_date_counts", return_value=fake_chroma):
        from fastapi import Request
        request = Request({"type": "http", "headers": []})
        with patch.object(mod, "_require_owner", return_value={}):
            out = await mod.memory_drift(request, days=30, top_skew=10)

    # Drift is negative (Mongo - Chroma = 5 - 150 = -145)
    assert out["drift"] == -145
    # But drift_pct uses max(drift, 0) → 0 → "ok" recommendation.
    assert out["drift_pct"] == 0.0
    assert out["recommendation"] == "ok"
    # Negative-skew rows still appear in the breakdown for visibility.
    assert any(row["skew"] < 0 for row in out["by_date_top_skew"])


@pytest.mark.asyncio
async def test_drift_endpoint_positive_skew_sorted_first():
    """When both positive and negative skews exist, positive
    (sync-regression) rows must rank above negative (training-extra)
    rows so the operator's eye lands on the actionable items."""
    from unittest.mock import patch

    fake_mongo = {"2026-04-21": 100, "2026-04-22": 5}
    fake_chroma = {"2026-04-21": 5, "2026-04-22": 200}
    # 2026-04-21 has +95 skew (mongo lost rows)
    # 2026-04-22 has -195 skew (chroma over-supply, larger but benign)

    from routes import admin_memory_drift as mod
    mod._db = object()

    with patch.object(mod, "_mongo_per_date_counts", return_value=fake_mongo), \
         patch.object(mod, "_chroma_per_date_counts", return_value=fake_chroma):
        from fastapi import Request
        request = Request({"type": "http", "headers": []})
        with patch.object(mod, "_require_owner", return_value={}):
            out = await mod.memory_drift(request, days=30, top_skew=10)

    top = out["by_date_top_skew"][0]
    assert top["date"] == "2026-04-21"
    assert top["skew"] == 95  # positive — actionable
    second = out["by_date_top_skew"][1]
    assert second["date"] == "2026-04-22"
    assert second["skew"] == -195  # larger magnitude but benign — ranks below
