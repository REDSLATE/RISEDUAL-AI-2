"""
Tests for the 14-day data-integrity timeseries + the 90-day TTL
index on ``data_integrity_metrics``.

These pin the observability contract so the dashboard sparkline
keeps working and the metrics collection can't grow unbounded.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import pytest
from pymongo import MongoClient


@pytest.fixture(scope="module")
def db():
    url = os.environ.get("MONGO_URL")
    name = os.environ.get("DB_NAME")
    if not url or not name:
        pytest.skip("MONGO_URL / DB_NAME not set")
    client = MongoClient(url, serverSelectionTimeoutMS=2000)
    yield client[name]
    client.close()


def test_ttl_index_on_data_integrity_metrics_is_90_days(db):
    """The TTL index MUST exist and MUST be scoped to 90 days so the
    metrics collection reaps itself in steady state. 90 days is the
    sweet spot: wide enough to trend-analyse a slow drift, narrow
    enough to keep the hot path cheap."""
    idx = list(db.data_integrity_metrics.list_indexes())
    ttl = next(
        (i for i in idx if i.get("expireAfterSeconds") is not None),
        None,
    )
    assert ttl is not None, (
        "data_integrity_metrics has no TTL index — the collection will "
        "grow unbounded. See routes/auth.create_indexes."
    )
    assert ttl["expireAfterSeconds"] == 7776000, (
        f"Expected 90d TTL (7776000s), got {ttl['expireAfterSeconds']}s"
    )
    assert "fired_at_dt" in dict(ttl["key"]), (
        "TTL must index the BSON Date field fired_at_dt, not the ISO "
        "string fired_at (MongoDB TTL ignores string fields)."
    )


def test_query_index_on_fired_at_exists(db):
    """Dashboard queries range-compare on fired_at (ISO string). Make
    sure the descending index is there so count_documents + aggregate
    pipelines don't table-scan under load."""
    idx = list(db.data_integrity_metrics.list_indexes())
    names = {i["name"] for i in idx}
    assert any(
        n for n in names if "fired_at" in n and n != "ttl_fired_at_dt"
    ), f"Missing query-side fired_at index. Got: {names}"


def test_compound_metric_fired_at_index_exists(db):
    """Per-metric timeseries queries filter by ``metric`` and sort by
    ``fired_at``. A compound index is the right shape."""
    idx = {i["name"]: dict(i["key"]) for i in db.data_integrity_metrics.list_indexes()}
    assert "metric_fired_at_desc" in idx, (
        f"Missing compound (metric, fired_at) index. Got: {list(idx.keys())}"
    )
    keys = idx["metric_fired_at_desc"]
    assert "metric" in keys and "fired_at" in keys


@pytest.mark.asyncio
async def test_record_unknown_writes_both_time_fields():
    """Every write to data_integrity_metrics MUST populate both the
    ISO string (for dashboard queries) and the BSON Date (for TTL)."""
    from unittest.mock import AsyncMock, MagicMock
    from services.prediction_tracker import (
        record_unknown_direction_token,
        reset_unknown_direction_counter,
    )
    reset_unknown_direction_counter()
    coll = AsyncMock()
    mock_db = MagicMock()
    mock_db.data_integrity_metrics = coll
    await record_unknown_direction_token("XYZ", context="ttl_test", db=mock_db)
    coll.insert_one.assert_awaited_once()
    doc = coll.insert_one.await_args.args[0]
    assert "fired_at" in doc, "missing ISO string field"
    assert "fired_at_dt" in doc, "missing BSON Date field for TTL"
    assert isinstance(doc["fired_at"], str)
    assert isinstance(doc["fired_at_dt"], datetime)
    # And they must represent the same instant (within rounding).
    parsed = datetime.fromisoformat(doc["fired_at"])
    assert abs((parsed - doc["fired_at_dt"]).total_seconds()) < 1.0
