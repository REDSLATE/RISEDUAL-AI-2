"""Verifier backlog drain — 2026-05-22 fix.

Pins the verifier-loop changes after the production Tier 3 readiness
score crashed from 70 → 45.5 because the verifier could not drain
predictions fast enough to refill the 30-day high-conf bucket:

* Cadence dropped 3600s → 300s (12× more frequent runs).
* Batch limit bumped 20 → 200 rows/call (10× more per pass).
* Combined drain rate: 120× pre-fix.
* Rows that fail to fetch a price are no longer silent — each miss
  increments ``verify_attempts``; after 10 attempts the row is
  stamped ``VERIFICATION_SKIPPED`` so the queue can move on.
* Sort order is now newest-first so the rolling 30-day window
  refills before we chew through the historical tail.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_SRC = Path("/app/backend/services/prediction_tracker.py").read_text(encoding="utf-8")
_SERVER_SRC = Path("/app/backend/server.py").read_text(encoding="utf-8")


# ── Static authority firewall ──────────────────────────────────────


def test_verifier_cadence_is_5min_not_1h():
    """``_verify_loop`` must sleep 300 seconds between runs."""
    loop_start = _SERVER_SRC.find("async def _verify_loop()")
    assert loop_start > 0
    loop_body = _SERVER_SRC[loop_start: loop_start + 1500]
    assert "asyncio.sleep(300)" in loop_body, "verifier should sleep 5min, not 1h"
    assert "asyncio.sleep(3600)" not in loop_body, (
        "the 1h cadence regressed Tier 3 — must not come back"
    )


def test_verifier_batch_limit_is_200_not_20():
    """Both branches (24h + 1w) must pull 200 rows per call."""
    # 24h branch
    h24_start = _SRC.find('"verified_24h": None,')
    h24_end = _SRC.find("async for pred in pending_24h", h24_start)
    h24_block = _SRC[h24_start:h24_end]
    assert ".limit(200)" in h24_block, "24h verifier must batch 200"
    assert ".limit(20)" not in h24_block, "20-row cap regression must not return"

    # 1w branch
    w_start = _SRC.find('"verified_1w": None,')
    w_end = _SRC.find("async for pred in pending_1w", w_start)
    w_block = _SRC[w_start:w_end]
    assert ".limit(200)" in w_block, "1w verifier must batch 200"


def test_verifier_sorts_newest_first():
    """Tier 3 reads a rolling 30-day window. Newest-first drain
    refills the active window before ancient backlog rows that
    won't help the score anymore."""
    h24_start = _SRC.find('"verified_24h": None,')
    h24_end = _SRC.find("async for pred in pending_24h", h24_start)
    assert '.sort("timestamp", -1)' in _SRC[h24_start:h24_end]


def test_rate_limited_rows_increment_verify_attempts():
    """A ``price_now is None`` path used to ``continue`` silently
    leaving the row in the queue forever. Now it stamps
    ``verify_attempts``. After ``MAX_VERIFY_ATTEMPTS`` the row gets
    a permanent ``VERIFICATION_SKIPPED`` grade so it stops blocking
    the queue."""
    assert "MAX_VERIFY_ATTEMPTS = 10" in _SRC
    # 24h branch increments + caps
    h24_start = _SRC.find("async for pred in pending_24h")
    h24_end = _SRC.find("# Push to SSE stream", h24_start)
    h24_body = _SRC[h24_start:h24_end]
    assert "verify_attempts" in h24_body
    assert "MAX_VERIFY_ATTEMPTS" in h24_body
    assert "VERIFICATION_SKIPPED" in h24_body
    assert "PRICE_FETCH_FAILED" in h24_body
    # 1w branch matches the pattern
    w_start = _SRC.find("async for pred in pending_1w")
    w_body = _SRC[w_start: w_start + 1800]
    assert "verify_1w_attempts" in w_body
    assert "PRICE_FETCH_FAILED" in w_body


def test_query_excludes_rows_that_already_gave_up():
    """The cursor must not re-pull rows that already hit
    MAX_VERIFY_ATTEMPTS. Without this filter the queue would
    re-process the same dead symbols every cycle."""
    h24_start = _SRC.find('"verified_24h": None,')
    h24_end = _SRC.find("async for pred in pending_24h", h24_start)
    h24_block = _SRC[h24_start:h24_end]
    assert '"verify_attempts": {"$exists": False}' in h24_block
    assert '"verify_attempts": {"$lt": MAX_VERIFY_ATTEMPTS}' in h24_block


# ── Behavioural — drive the function with an in-memory mock ───────


@pytest.mark.asyncio
async def test_first_miss_increments_attempts_to_1():
    """A row whose price fetch fails ONCE should not get the
    VERIFICATION_SKIPPED stamp — only the counter advances."""
    from unittest.mock import AsyncMock, patch
    from services import prediction_tracker as mod

    pred = {
        "prediction_id": "p-1", "symbol": "DEAD", "direction": "LONG",
        "price_at_prediction": 100.0, "verify_attempts": 0,
        "timestamp": "2026-05-01T00:00:00+00:00",
    }
    sets: list[dict] = []

    class _Cursor:
        def __init__(self, docs):
            self._docs = list(docs)
            self._consumed = False

        def sort(self, *a, **kw): return self

        def limit(self, *a, **kw): return self

        def __aiter__(self): return self

        async def __anext__(self):
            if self._consumed or not self._docs:
                raise StopAsyncIteration
            self._consumed = True
            return self._docs.pop(0)

    class _Coll:
        def __init__(self, rows):
            self.rows = rows
            self._call = 0

        def find(self, *a, **kw):
            self._call += 1
            return _Cursor(self.rows if self._call == 1 else [])

        async def update_one(self, q, upd, *a, **kw):
            sets.append(upd.get("$set", {}))

            class _R:
                modified_count = 1
            return _R()

    class _DB:
        def __init__(self, rows):
            self.predictions = _Coll(rows)

        def __getitem__(self, _name):
            return _Coll([])

    db = _DB([pred])

    with patch("services.prediction_tracker._get_current_price", return_value=None):
        await mod.verify_pending_predictions(db)

    assert any("verify_attempts" in s for s in sets), (
        "first miss must bump verify_attempts"
    )
    bumps = [s for s in sets if s.get("verify_attempts") == 1]
    assert len(bumps) == 1
    # And no VERIFICATION_SKIPPED stamp this early.
    skipped = [s for s in sets if isinstance(s.get("verified_24h"), dict)
               and s["verified_24h"].get("grade") == "VERIFICATION_SKIPPED"]
    assert skipped == []


@pytest.mark.asyncio
async def test_tenth_miss_marks_verification_skipped():
    """After MAX_VERIFY_ATTEMPTS the row gets a permanent skip
    grade and is therefore excluded from future cursors by the
    query filter."""
    from unittest.mock import patch
    from services import prediction_tracker as mod

    pred = {
        "prediction_id": "p-2", "symbol": "DELISTED", "direction": "LONG",
        "price_at_prediction": 50.0, "verify_attempts": 9,
        "timestamp": "2026-05-01T00:00:00+00:00",
    }
    sets: list[dict] = []

    class _Cursor:
        def __init__(self, docs):
            self._docs = list(docs)
            self._consumed = False

        def sort(self, *a, **kw): return self

        def limit(self, *a, **kw): return self

        def __aiter__(self): return self

        async def __anext__(self):
            if self._consumed or not self._docs:
                raise StopAsyncIteration
            self._consumed = True
            return self._docs.pop(0)

    class _Coll:
        def __init__(self, rows):
            self.rows = rows
            self._call = 0

        def find(self, *a, **kw):
            self._call += 1
            return _Cursor(self.rows if self._call == 1 else [])

        async def update_one(self, q, upd, *a, **kw):
            sets.append(upd.get("$set", {}))

            class _R:
                modified_count = 1
            return _R()

    class _DB:
        def __init__(self, rows):
            self.predictions = _Coll(rows)

        def __getitem__(self, _n):
            return _Coll([])

    db = _DB([pred])

    with patch("services.prediction_tracker._get_current_price", return_value=None):
        await mod.verify_pending_predictions(db)

    # On attempt #10 (9+1) the row gets stamped permanently.
    skipped = [s for s in sets if isinstance(s.get("verified_24h"), dict)
               and s["verified_24h"].get("grade") == "VERIFICATION_SKIPPED"]
    assert len(skipped) == 1
    assert skipped[0]["verified_24h"]["failure_code"] == "PRICE_FETCH_FAILED"
    assert skipped[0]["verify_attempts"] == 10
