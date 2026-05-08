"""Tests for `services.snapshot_enricher.stamp_execution_on_snapshot`.

Pins:
  * prediction_id is required
  * only non-None fields are written (no field-wipe on partial data)
  * direction is normalised to LONG/SHORT (or dropped if invalid)
  * bad numeric inputs are silently dropped, not raised
  * Mongo failure → False, never raises
  * matched_count=0 → False; matched_count>0 → True
  * execution_enriched_at + schema_version=4 always set when any
    field is written
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.snapshot_enricher import stamp_execution_on_snapshot


# ── DB stubs ──────────────────────────────────────────────────────


class _UpdateResult:
    def __init__(self, matched: int = 1):
        self.matched_count = matched


class _Coll:
    def __init__(self, matched: int = 1, raise_on_update: bool = False):
        self._matched = matched
        self._raise = raise_on_update
        self.last_filter: dict | None = None
        self.last_update: dict | None = None

    async def update_one(self, filter_: dict, update: dict):
        if self._raise:
            raise RuntimeError("simulated mongo failure")
        self.last_filter = filter_
        self.last_update = update
        return _UpdateResult(self._matched)


class _StubDB:
    def __init__(self, matched: int = 1, raise_on_update: bool = False):
        self._coll = _Coll(matched=matched, raise_on_update=raise_on_update)

    def __getitem__(self, name: str):
        assert name == "features_snapshots"
        return self._coll


# ── Happy path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_four_fields_stamped_normalised_and_versioned():
    """Happy path — all four fields written, direction uppercased,
    schema_version bumped to 4, execution_enriched_at is UTC-aware."""
    db = _StubDB(matched=1)
    ok = await stamp_execution_on_snapshot(
        db=db,
        prediction_id="pred-123",
        entry_price=100.0,
        exit_price=110.5,
        stop_loss=95.0,
        direction="long",
    )
    assert ok is True
    update = db._coll.last_update["$set"]
    assert update["entry_price"] == 100.0
    assert update["exit_price"] == 110.5
    assert update["stop_loss"] == 95.0
    assert update["direction"] == "LONG"
    assert update["schema_version"] == 4
    assert isinstance(update["execution_enriched_at"], datetime)
    assert update["execution_enriched_at"].tzinfo is timezone.utc
    # Filter must target the originating snapshot, not a broad scan.
    assert db._coll.last_filter == {"prediction_id": "pred-123"}


# ── Input validation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_prediction_id_returns_false_no_write():
    """No prediction_id → short-circuit. Guards against an accidental
    broad-match update that would overwrite random snapshots."""
    db = _StubDB()
    ok = await stamp_execution_on_snapshot(
        db=db, prediction_id="", entry_price=100.0, exit_price=110.0,
        stop_loss=95.0, direction="LONG",
    )
    assert ok is False
    assert db._coll.last_update is None


@pytest.mark.asyncio
async def test_partial_data_writes_only_present_fields():
    """stop_loss omitted → only 3 execution fields written, not 4.
    Prevents accidental field-wipe on re-enrichment paths."""
    db = _StubDB(matched=1)
    ok = await stamp_execution_on_snapshot(
        db=db, prediction_id="p1", entry_price=100.0, exit_price=110.0,
        stop_loss=None, direction="SHORT",
    )
    assert ok is True
    update = db._coll.last_update["$set"]
    assert "stop_loss" not in update
    assert update["entry_price"] == 100.0
    assert update["direction"] == "SHORT"


@pytest.mark.asyncio
async def test_no_valid_fields_skips_mongo_roundtrip():
    """All four inputs None → don't even hit Mongo (returns False).
    Keeps the labeling loop cheap when there's nothing to enrich."""
    db = _StubDB(matched=1)
    ok = await stamp_execution_on_snapshot(
        db=db, prediction_id="p1",
        entry_price=None, exit_price=None, stop_loss=None, direction=None,
    )
    assert ok is False
    assert db._coll.last_update is None


@pytest.mark.asyncio
async def test_invalid_direction_dropped_other_fields_kept():
    """Direction 'sideways' → not stamped (only LONG/SHORT accepted).
    Other fields still get written. Guards the
    risk_weighting.compute_r_multiple LONG/SHORT contract."""
    db = _StubDB(matched=1)
    ok = await stamp_execution_on_snapshot(
        db=db, prediction_id="p1", entry_price=100.0, exit_price=110.0,
        stop_loss=95.0, direction="sideways",
    )
    assert ok is True
    update = db._coll.last_update["$set"]
    assert "direction" not in update
    assert update["entry_price"] == 100.0


@pytest.mark.asyncio
async def test_non_numeric_price_silently_dropped():
    """Bad numeric input from a dirty source → dropped, not raised.
    Other good fields still flow through."""
    db = _StubDB(matched=1)
    ok = await stamp_execution_on_snapshot(
        db=db, prediction_id="p1",
        entry_price="not-a-number",  # type: ignore[arg-type]
        exit_price=110.0, stop_loss=95.0, direction="LONG",
    )
    assert ok is True
    update = db._coll.last_update["$set"]
    assert "entry_price" not in update
    assert update["exit_price"] == 110.0


# ── Failure isolation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mongo_failure_returns_false_no_raise():
    """Reporting-sidecar contract: a transient Mongo error must not
    bubble up and break the outcome-labeling loop."""
    db = _StubDB(raise_on_update=True)
    ok = await stamp_execution_on_snapshot(
        db=db, prediction_id="p1", entry_price=100.0, exit_price=110.0,
        stop_loss=95.0, direction="LONG",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_no_snapshot_matched_returns_false():
    """matched_count=0 → False. The prediction exists but no
    features_snapshot was ever written for it (legacy row). Caller
    can log-and-continue."""
    db = _StubDB(matched=0)
    ok = await stamp_execution_on_snapshot(
        db=db, prediction_id="p1", entry_price=100.0, exit_price=110.0,
        stop_loss=95.0, direction="LONG",
    )
    assert ok is False
