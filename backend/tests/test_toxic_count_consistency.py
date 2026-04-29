"""Toxic-count consistency test.

Pins the bug-fix invariant: the operator-facing toxic count emitted
by ``market_memory_service.nightly_cleanup`` MUST match the count
reported by ``services.toxic_autopsy_service.build_autopsy`` over
the same window. Both query MongoDB; they should agree exactly.

Before this fix the email reported the ChromaDB-deduplicated count
which was ~10× lower (76 verified misses collapsed to 8 cached rows).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import market_memory_service
from services.toxic_autopsy_service import build_autopsy


def _now_iso(offset_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()


class _FakePredCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakePredictionsCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query, projection=None):  # noqa: ARG002
        # Apply the same filter the autopsy uses
        since = query.get("verified_24h.verified_at", {}).get("$gte", "")
        out = []
        for d in self._docs:
            v24 = d.get("verified_24h")
            if not v24:
                continue
            if v24.get("correct") is not False:
                continue
            if (v24.get("verified_at") or "") < since:
                continue
            out.append(d)
        return _FakePredCursor(out)

    async def count_documents(self, query):
        # Apply the nightly_cleanup mongo predicate. We support the
        # mixed-scale $or shape it uses.
        threshold_pct = 80.0
        since = ""
        for k, v in query.items():
            if k == "verified_24h.verified_at" and isinstance(v, dict):
                since = v.get("$gte", "")
            if k == "$or":
                # Extract the threshold from the $gt clauses
                for clause in v:
                    conf = clause.get("confidence", {})
                    if "$gt" in conf and conf["$gt"] > 1.0:
                        threshold_pct = conf["$gt"]
                        break
        threshold_unit = threshold_pct / 100.0
        n = 0
        for d in self._docs:
            v24 = d.get("verified_24h") or {}
            if v24.get("correct") is not False:
                continue
            if (v24.get("verified_at") or "") < since:
                continue
            conf = d.get("confidence")
            if conf is None:
                continue
            try:
                conf_f = float(conf)
            except (TypeError, ValueError):
                continue
            # Match either the 0-100 or 0-1 stored shape, just like
            # the real predicate.
            if conf_f > threshold_pct:
                n += 1
            elif 0 < conf_f <= 1.0 and conf_f > threshold_unit:
                n += 1
        return n


class _FakeDb:
    def __init__(self, docs):
        self.predictions = _FakePredictionsCollection(docs)

    def __getitem__(self, name):
        if name == "predictions":
            return self.predictions
        return MagicMock()


@pytest.mark.asyncio
async def test_toxic_count_matches_autopsy(monkeypatch):
    """The single most important regression test for this bug fix."""
    docs = [
        # 5 high-conf misses on 0-100 scale within window
        *[
            {
                "prediction_id": f"p-100-{i}", "symbol": "NVDA", "feature": "war_room",
                "direction": "BUY", "confidence": 88,
                "timestamp": _now_iso(-10), "price_at_prediction": 900.0,
                "verified_24h": {"correct": False, "grade": "STRONG_MISS",
                                  "verified_at": _now_iso(-2),
                                  "failure_code": "TECH_FAKEOUT", "price": 880.0},
            }
            for i in range(5)
        ],
        # 7 high-conf misses on 0-1 scale within window
        *[
            {
                "prediction_id": f"p-01-{i}", "symbol": "META", "feature": "hypothesis",
                "direction": "SELL", "confidence": 0.91,
                "timestamp": _now_iso(-10), "price_at_prediction": 500.0,
                "verified_24h": {"correct": False, "grade": "WEAK_MISS",
                                  "verified_at": _now_iso(-3),
                                  "failure_code": "REGIME_SHIFT", "price": 510.0},
            }
            for i in range(7)
        ],
        # Excluded: low conf
        {
            "prediction_id": "lo-1", "symbol": "AAPL", "feature": "war_room",
            "direction": "BUY", "confidence": 0.65,
            "timestamp": _now_iso(-10),
            "verified_24h": {"correct": False, "grade": "STRONG_MISS", "verified_at": _now_iso(-2)},
        },
        # Excluded: outside window (older than 7 days)
        {
            "prediction_id": "old-1", "symbol": "TSLA", "feature": "war_room",
            "direction": "BUY", "confidence": 0.92,
            "timestamp": _now_iso(-300),
            "verified_24h": {"correct": False, "grade": "STRONG_MISS", "verified_at": _now_iso(-240)},
        },
        # Excluded: NEUTRAL — correct is None
        {
            "prediction_id": "neu-1", "symbol": "AMZN", "feature": "war_room",
            "direction": "HOLD", "confidence": 0.95,
            "timestamp": _now_iso(-10),
            "verified_24h": {"correct": None, "grade": "NEUTRAL", "verified_at": _now_iso(-2)},
        },
    ]
    db = _FakeDb(docs)

    # Patch market_memory_service's _db handle so _count_toxic_from_mongo
    # uses our fake.
    monkeypatch.setattr(market_memory_service, "_db", db)

    mongo_count = await market_memory_service._count_toxic_from_mongo(
        confidence_threshold_pct=80.0, days=7,
    )

    # Autopsy uses the same MongoDB collection
    autopsy = await build_autopsy(db, days=7, min_confidence_pct=80.0)

    # Both must agree to the row — this is the bug-fix invariant.
    assert mongo_count["total"] == autopsy["total_failures"], (
        f"toxic count drift detected: nightly_cleanup={mongo_count['total']} "
        f"!= autopsy={autopsy['total_failures']} — the email is lying again"
    )
    # And both should be 12 (5 + 7)
    assert mongo_count["total"] == 12


@pytest.mark.asyncio
async def test_toxic_count_handles_missing_db_gracefully(monkeypatch):
    monkeypatch.setattr(market_memory_service, "_db", None)
    out = await market_memory_service._count_toxic_from_mongo()
    assert out["total"] == 0


@pytest.mark.asyncio
async def test_toxic_count_returns_zero_on_empty_collection(monkeypatch):
    monkeypatch.setattr(market_memory_service, "_db", _FakeDb([]))
    out = await market_memory_service._count_toxic_from_mongo()
    assert out["total"] == 0
