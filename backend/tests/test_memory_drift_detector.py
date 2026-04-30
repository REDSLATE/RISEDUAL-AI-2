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
    """Reset the in-process counters before each test so they
    don't bleed across cases."""
    from services.mongo_chroma_sync_metrics import reset_counters
    reset_counters()


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
    from services.mongo_chroma_sync_metrics import (
        get_last_rebuild, mark_rebuild,
    )
    mark_rebuild(rebuilt=42, skipped=3, since="2026-04-01")
    out = get_last_rebuild()
    assert out["last_rebuild_summary"] == {
        "rebuilt": 42, "skipped": 3, "since": "2026-04-01",
    }
    # ``last_rebuild_at`` must be a tz-aware ISO string the drift
    # endpoint can subtract from ``datetime.now(timezone.utc)``
    # without re-introducing the tz-naive bug.
    parsed = datetime.fromisoformat(out["last_rebuild_at"])
    assert parsed.tzinfo is not None


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


@pytest.mark.asyncio
async def test_drift_endpoint_includes_sync_metrics():
    """The drift response must surface skip counters + last-rebuild
    so the operator can interpret raw drift % in context."""
    from unittest.mock import patch

    from services.mongo_chroma_sync_metrics import (
        mark_rebuild, record_skip, reset_counters,
    )
    reset_counters()
    record_skip("warmup_save_failed")
    record_skip("rebuild_save_failed")
    record_skip("rebuild_save_failed")
    mark_rebuild(rebuilt=100, skipped=2, since="2026-04-15")

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
