"""Regression test for the test-contamination tripwire.

`self_test_service._check_test_contamination` is the every-15-min
canary that catches anything that bypasses the 6-layer write-side
guards. Tests:

  * Clean DB → PASS with row count
  * Seeded TEST_ in any probed collection → FAIL with the offender's
    location in the error message
  * Probe is robust to bad regex / missing collections (no exceptions)
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))


def _mongo_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def test_contamination_check_passes_on_clean_db():
    """All probed collections clean → PASS."""
    from services.self_test_service import _check_test_contamination

    async def _run():
        db = _mongo_db()
        # Belt-and-suspenders: scrub any pre-existing contamination
        # so this test doesn't get a false fail from earlier runs.
        import re
        pat = re.compile(r"^(TEST_|MOCK_|FAKE_|DUMMY_|FIXTURE_|FAKEXYZ)", re.I)
        for coll, field in [
            ("predictions", "symbol"), ("trade_ideas", "symbol"),
            ("trades", "ticker"), ("signals", "ticker"),
        ]:
            await db[coll].delete_many({field: pat})
        for coll, field in [("watchlists", "tickers"), ("alerts_sent", "tickers")]:
            await db[coll].update_many(
                {field: pat}, {"$pull": {field: {"$regex": pat}}}
            )

        result = await _check_test_contamination(db)
        assert result["status"] == "PASS", result
        assert result["name"] == "test_contamination"

    asyncio.get_event_loop().run_until_complete(_run())


def test_contamination_check_fails_when_predictions_contaminated():
    """Seed a TEST_ symbol in predictions → check FAILs and the
    error message names the offending collection.field=value."""
    from services.self_test_service import _check_test_contamination

    async def _run():
        db = _mongo_db()
        seed_id = f"contam-test-{uuid.uuid4().hex[:8]}"
        try:
            await db.predictions.insert_one({
                "prediction_id": seed_id,
                "feature": "test",
                "symbol": "TEST_FAIL_61",
                "direction": "BULLISH",
                "confidence": 90,
                "timestamp": "2026-04-25T00:00:00+00:00",
                "user_id": None,
            })
            result = await _check_test_contamination(db)
            assert result["status"] == "FAIL", result
            assert "predictions.symbol" in result["error"]
            assert "TEST_FAIL_61" in result["error"]
        finally:
            await db.predictions.delete_one({"prediction_id": seed_id})

    asyncio.get_event_loop().run_until_complete(_run())


def test_contamination_check_fails_on_array_field_contamination():
    """Watchlists are array-field probes — confirm `$regex` on an
    array still detects contamination (Mongo matches if ANY element
    matches the regex)."""
    from services.self_test_service import _check_test_contamination

    async def _run():
        db = _mongo_db()
        seed_uid = f"wl-contam-{uuid.uuid4().hex[:8]}"
        try:
            await db.watchlists.insert_one({
                "user_id": seed_uid,
                "tickers": ["AAPL", "NVDA", "TEST_LEAK"],
                "updated_at": "2026-04-25T00:00:00+00:00",
            })
            result = await _check_test_contamination(db)
            assert result["status"] == "FAIL"
            assert "watchlists.tickers" in result["error"]
        finally:
            await db.watchlists.delete_one({"user_id": seed_uid})

    asyncio.get_event_loop().run_until_complete(_run())


def test_contamination_check_handles_db_none():
    """`db is None` shouldn't crash — should FAIL cleanly."""
    from services.self_test_service import _check_test_contamination

    async def _run():
        result = await _check_test_contamination(None)
        assert result["status"] == "FAIL"
        assert "db reference is None" in result["error"]

    asyncio.get_event_loop().run_until_complete(_run())


def test_contamination_check_in_full_self_test_battery():
    """The check is now part of `run_self_test`. Confirm it shows
    up in the report's `checks` array under the expected name."""
    from services.self_test_service import run_self_test

    async def _run():
        db = _mongo_db()
        report = await run_self_test(db, scheduler=None)
        names = [c["name"] for c in report["checks"]]
        assert "test_contamination" in names, (
            f"Expected `test_contamination` in {names}"
        )

    asyncio.get_event_loop().run_until_complete(_run())
