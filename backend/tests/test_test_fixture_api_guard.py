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


def test_failure_loop_create_blocks_test_symbols():
    """`failure_loop_service.create_trade_idea` was the entry point
    that leaked 12 TEST_AAPL/GOOG/NVDA/META/AMZN rows into the
    prod `trade_ideas` collection. Confirm it now returns a
    structured `blocked` payload (no fake idea_id) and writes
    nothing to the collection."""
    from services import failure_loop_service

    async def _run():
        db = _mongo_db()
        failure_loop_service.set_db(db)
        import re
        pat = re.compile(r"^(TEST_|MOCK_|FAKE_|DUMMY_|FIXTURE_|FAKEXYZ)", re.I)
        await db.trade_ideas.delete_many({"symbol": pat})

        for sym in ("TEST_AAPL", "MOCK_GOOG", "FAKEXYZ", "fixture_x"):
            res = await failure_loop_service.create_trade_idea(
                user_id="test-user",
                symbol=sym,
                direction="long",
                thesis="should be blocked",
                confidence=0.8,
                source="user",
            )
            assert res.get("blocked") is True, (
                f"Expected blocked=True for {sym!r}, got {res}"
            )
            assert res.get("reason") == "test_symbol_rejected", (
                f"Expected reason='test_symbol_rejected' for {sym!r}, got {res}"
            )
            # Symbol echoed back uppercase
            assert res.get("symbol") == sym.upper(), (
                f"Symbol echo mismatch: {res.get('symbol')!r} vs {sym.upper()!r}"
            )
            # Critically: no fake idea_id leaked into the response.
            # Earlier patch returned `idea_id: 'blocked-test-symbol-X'`;
            # the cleaner contract removes it entirely so callers
            # can never accidentally treat a rejection as a success.
            assert "idea_id" not in res, (
                f"Unexpected idea_id in blocked response: {res!r}"
            )

        leaked = await db.trade_ideas.count_documents({"symbol": pat})
        assert leaked == 0, f"{leaked} test fixtures slipped past guard"

    asyncio.get_event_loop().run_until_complete(_run())


def test_failure_loop_blocks_TEST_FAIL_61_at_source():
    """Mirror of the user-supplied regression spec — the exact
    incident symbol that started the 2026-04-24 cascade. Locks the
    fix at the `create_trade_idea` source boundary."""
    from services import failure_loop_service

    async def _run():
        db = _mongo_db()
        failure_loop_service.set_db(db)
        await db.trade_ideas.delete_many({"symbol": "TEST_FAIL_61"})

        result = await failure_loop_service.create_trade_idea(
            user_id="u1",
            symbol="TEST_FAIL_61",
            direction="UP",
            thesis="fixture",
            confidence=90,
        )

        assert result["blocked"] is True
        assert await db.trade_ideas.count_documents(
            {"symbol": "TEST_FAIL_61"}
        ) == 0

    asyncio.get_event_loop().run_until_complete(_run())


def test_failure_loop_route_returns_400_for_test_symbols():
    """The `/api/failure-loop/ideas` route now returns 400 instead
    of a 200 with a `blocked` flag. Honest API contract: clients
    writing test fixtures should see a real error so the bad
    behaviour is impossible to ignore."""
    import requests
    from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD

    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 200, r.text

    r = s.post(
        f"{BASE_URL}/api/failure-loop/ideas",
        json={
            "symbol": "TEST_AAPL",
            "direction": "long",
            "thesis": "smoke",
            "confidence": 0.7,
            "source": "user",
        },
    )
    assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
    detail = r.json().get("detail", {})
    assert detail.get("error") == "test_symbol_rejected"
    assert detail.get("symbol") == "TEST_AAPL"
