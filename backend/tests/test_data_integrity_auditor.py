"""
Tests for the data-integrity nightly auditor.

Pins the invariants established by the 2026-05-01 direction-token
cleanup so any regression (data or code) flips an audit check red.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_db_stub(*, predictions=None, metrics=None):
    """Build a minimal Motor-like async db stub."""
    predictions = predictions or []
    metrics = metrics or []
    db = MagicMock()

    # data_integrity_metrics collection
    async def metrics_count(q):
        since = q.get("fired_at", {}).get("$gte")
        return sum(
            1 for m in metrics
            if m.get("metric") == q.get("metric")
            and (since is None or m.get("fired_at", "") >= since)
        )

    db.data_integrity_metrics.count_documents = metrics_count

    class _EmptyAsyncCursor:
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration

    def metrics_aggregate(_pipeline):
        return _EmptyAsyncCursor()
    db.data_integrity_metrics.aggregate = metrics_aggregate

    # predictions collection with filter + projection
    class _Cursor:
        def __init__(self, rows): self.rows = rows
        def __aiter__(self): return self
        async def __anext__(self):
            if not self.rows:
                raise StopAsyncIteration
            return self.rows.pop(0)

    def _matches(pred, q):
        for k, v in q.items():
            if k == "$or":
                if not any(_matches(pred, sub) for sub in v):
                    return False
                continue
            node = pred
            for part in k.split("."):
                if not isinstance(node, dict):
                    return False
                node = node.get(part)
            if isinstance(v, dict):
                if "$gte" in v and not (node is not None and node >= v["$gte"]):
                    return False
                if "$gt" in v and not (node is not None and node > v["$gt"]):
                    return False
                if "$in" in v and node not in v["$in"]:
                    return False
                if "$ne" in v and node == v["$ne"]:
                    return False
            else:
                if node != v:
                    return False
        return True

    def find(q, projection=None):
        return _Cursor([p for p in predictions if _matches(p, q)])
    db.predictions.find = find

    db.data_integrity_audits.insert_one = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_audit_passes_when_everything_clean():
    from services.data_integrity_auditor import run_nightly_integrity_audit
    db = _make_db_stub(predictions=[], metrics=[])
    summary = await run_nightly_integrity_audit(db, window_hours=24)
    assert summary["overall_passed"] is True
    # All four invariants present and passing.
    names = {c["name"] for c in summary["checks"]}
    assert names == {
        "no_unknown_direction_tokens",
        "strong_buy_grading_consistent",
        "strong_sell_grading_consistent",
        "toxic_lessons_match_real_misses",
    }
    db.data_integrity_audits.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_flips_red_on_unknown_direction_metric():
    from services.data_integrity_auditor import run_nightly_integrity_audit
    now = datetime.now(timezone.utc).isoformat()
    db = _make_db_stub(
        metrics=[
            {"metric": "unknown_direction_token", "context": "canary",
             "fired_at": now, "token": "GARBAGE"},
        ]
    )
    summary = await run_nightly_integrity_audit(db, window_hours=24)
    assert summary["overall_passed"] is False
    udt = next(c for c in summary["checks"] if c["name"] == "no_unknown_direction_tokens")
    assert udt["passed"] is False
    assert udt["count"] == 1


@pytest.mark.asyncio
async def test_audit_catches_strong_buy_wrongly_graded_as_miss():
    """The exact post-mortem scenario: STRONG_BUY with rising price
    graded as STRONG_MISS must show up as a violation."""
    from services.data_integrity_auditor import run_nightly_integrity_audit
    now = datetime.now(timezone.utc)
    db = _make_db_stub(
        predictions=[
            {
                "prediction_id": "canary-1",
                "symbol": "SPY",
                "direction": "STRONG_BUY",
                "price_at_prediction": 713.94,
                "verified_24h": {
                    "grade": "STRONG_MISS",
                    "verified_at": now.isoformat(),
                    "price": 718.66,  # rose — shouldn't be a miss
                },
                "timestamp": now.isoformat(),
            },
        ]
    )
    summary = await run_nightly_integrity_audit(db, window_hours=24)
    check = next(
        c for c in summary["checks"] if c["name"] == "strong_buy_grading_consistent"
    )
    assert check["passed"] is False
    assert check["violation_count"] == 1
    assert check["violations"][0]["symbol"] == "SPY"
    assert summary["overall_passed"] is False


@pytest.mark.asyncio
async def test_audit_catches_strong_sell_wrongly_graded_as_miss():
    from services.data_integrity_auditor import run_nightly_integrity_audit
    now = datetime.now(timezone.utc)
    db = _make_db_stub(
        predictions=[
            {
                "prediction_id": "canary-2",
                "symbol": "QQQ",
                "direction": "STRONG_SELL",
                "price_at_prediction": 500.0,
                "verified_24h": {
                    "grade": "STRONG_MISS",
                    "verified_at": now.isoformat(),
                    "price": 495.0,  # fell — shouldn't be a miss for SHORT
                },
                "timestamp": now.isoformat(),
            },
        ]
    )
    summary = await run_nightly_integrity_audit(db, window_hours=24)
    check = next(
        c for c in summary["checks"] if c["name"] == "strong_sell_grading_consistent"
    )
    assert check["passed"] is False
    assert check["violation_count"] == 1


@pytest.mark.asyncio
async def test_audit_accepts_strong_buy_real_miss():
    """Invariant must NOT flag legitimate misses — STRONG_BUY with
    FALLING price is a real miss."""
    from services.data_integrity_auditor import run_nightly_integrity_audit
    now = datetime.now(timezone.utc)
    db = _make_db_stub(
        predictions=[
            {
                "prediction_id": "real-miss",
                "symbol": "NVDA",
                "direction": "STRONG_BUY",
                "price_at_prediction": 208.27,
                "verified_24h": {
                    "grade": "STRONG_MISS",
                    "verified_at": now.isoformat(),
                    "price": 199.57,  # fell — real miss for a BUY call
                },
                "timestamp": now.isoformat(),
            },
        ]
    )
    summary = await run_nightly_integrity_audit(db, window_hours=24)
    check = next(
        c for c in summary["checks"] if c["name"] == "strong_buy_grading_consistent"
    )
    assert check["passed"] is True
    assert check["violation_count"] == 0
