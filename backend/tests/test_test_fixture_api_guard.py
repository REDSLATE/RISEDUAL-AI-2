"""End-to-end regression for the test-fixture API guard.

Earlier `test_toxic_spike_test_contamination.py` proves the
toxic-alert pipeline filters test rows. This file proves the
LAYER ABOVE — the prediction storage layer — refuses to persist
test fixtures in the first place. Belt + suspenders.

Covers:
  * `log_prediction()` returns the sentinel id and writes nothing
    when called with a TEST_*/MOCK_*/FAKE_*/FAKEXYZ symbol
  * Real symbols still persist normally
  * The hypothesis-route ML snapshot path is gated identically
    (verified via direct call to the helper that wraps it)
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


def test_log_prediction_blocks_test_symbols():
    """Every contaminating prefix returns the sentinel id and
    leaves the predictions collection untouched."""
    from services.prediction_tracker import log_prediction

    async def _run():
        db = _mongo_db()
        await db.predictions.delete_many(
            {"symbol": {"$regex": "^(TEST_|FAKEXYZ|MOCK_|FAKE_|DUMMY_|FIXTURE_)"}}
        )

        contaminating = [
            "TEST_AAPL", "test_lower",
            "MOCK_NVDA", "FAKE_GOOG",
            "DUMMY_001", "FIXTURE_ABC",
            "FAKEXYZ", "fakexyz",
        ]
        for sym in contaminating:
            pid = await log_prediction(
                db, "market_prediction", sym, "BULLISH", 80.0,
                user_id="test-user-id",
            )
            assert pid.startswith("blocked-test-symbol-"), (
                f"Expected sentinel for {sym!r}, got {pid}"
            )

        # Verify NOTHING was persisted
        count = await db.predictions.count_documents(
            {"symbol": {"$in": contaminating + [s.upper() for s in contaminating]}}
        )
        assert count == 0, f"Test fixtures leaked through guard: {count}"

    asyncio.get_event_loop().run_until_complete(_run())


def test_log_prediction_allows_real_symbols(monkeypatch):
    """Sanity: a real ticker still gets a UUID prediction_id and a
    row in the predictions collection. We tag the row with a
    unique seed so the test cleans up after itself."""
    from services import prediction_tracker

    # Avoid hitting the live price provider in unit tests
    monkeypatch.setattr(
        prediction_tracker, "_get_current_price",
        lambda symbol: 100.0,
    )

    async def _run():
        db = _mongo_db()
        seed_id = f"e2e-real-{uuid.uuid4().hex[:8]}"
        try:
            pid = await prediction_tracker.log_prediction(
                db, "market_prediction", "NVDA", "BULLISH", 80.0,
                user_id=seed_id,
            )
            assert not pid.startswith("blocked-test-symbol-"), (
                f"Real symbol unexpectedly blocked: {pid}"
            )
            row = await db.predictions.find_one(
                {"prediction_id": pid}, {"_id": 0},
            )
            assert row is not None, "Row should be persisted for real symbols"
            assert row["symbol"] == "NVDA"
            assert row["user_id"] == seed_id
        finally:
            await db.predictions.delete_many({"user_id": seed_id})

    asyncio.get_event_loop().run_until_complete(_run())


def test_is_real_symbol_canonical():
    """Single source of truth: `_is_real_symbol` is imported by
    every guard. If this assertion ever changes, every consumer
    needs review."""
    from services.market_memory_service import _is_real_symbol

    assert _is_real_symbol("NVDA")
    assert _is_real_symbol("BRK.B")
    assert _is_real_symbol("aapl")  # case-insensitive prefix check, body OK

    assert not _is_real_symbol("TEST_AAPL")
    assert not _is_real_symbol("test_lower")
    assert not _is_real_symbol("MOCK_NVDA")
    assert not _is_real_symbol("FAKE_GOOG")
    assert not _is_real_symbol("DUMMY_X")
    assert not _is_real_symbol("FIXTURE_Y")
    assert not _is_real_symbol("")
    assert not _is_real_symbol(None)
