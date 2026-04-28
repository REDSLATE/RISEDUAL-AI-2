"""Unit tests for toxic spike autopsy service.

Verifies:
  * Only predictions with confidence > min and verified_24h.correct == False
    within the lookback window are counted.
  * Confidence scale mixing (0-1 and 0-100) is normalised.
  * All six group dimensions are populated.
  * Top offenders are sorted by count then avg confidence.
  * NEUTRAL grades are excluded (they never have correct=False).
  * Empty DB returns a valid payload (no crashes).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.toxic_autopsy_service import build_autopsy, _bucket_for, _direction_family


class _FakeCursor:
    """Minimal async-iterable that yields the provided docs."""
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for d in self._docs:
            yield d


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query, projection=None):  # noqa: ARG002
        # Apply the same filter the service uses — our fake only
        # needs to filter on verified_24h.verified_at ≥ since and
        # correct == False (the service already handles the
        # confidence threshold in Python).
        since = query["verified_24h.verified_at"]["$gte"]
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
        return _FakeCursor(out)


def _mk_db(docs):
    return SimpleNamespace(predictions=_FakeCollection(docs))


def _now_iso(offset_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()


@pytest.mark.asyncio
async def test_build_autopsy_empty_db():
    db = _mk_db([])
    with patch("services.toxic_autopsy_service.resolve_sectors_bulk", new=AsyncMock(return_value={})):
        out = await build_autopsy(db, days=2, min_confidence_pct=80.0)
    assert out["total_failures"] == 0
    assert out["total_misses_in_window"] == 0
    assert out["avg_confidence_pct"] == 0.0
    assert out["unique_symbols"] == 0
    assert out["by_failure_code"] == []
    assert out["top_offenders"] == []
    assert "generated_at" in out


@pytest.mark.asyncio
async def test_build_autopsy_filters_and_groups():
    docs = [
        # Included: high-conf BUY miss on NVDA (conf 0-1 scale = 0.92)
        {
            "prediction_id": "p1", "symbol": "NVDA", "feature": "war_room",
            "direction": "BUY", "confidence": 0.92, "model_version": "v1",
            "timestamp": _now_iso(-10), "price_at_prediction": 900.0,
            "verified_24h": {
                "correct": False, "grade": "STRONG_MISS",
                "verified_at": _now_iso(-2), "failure_code": "TECH_FAKEOUT",
                "failure_reason": "reversal", "price": 880.0,
            },
        },
        # Included: high-conf SELL miss on META (conf 0-100 scale = 87)
        {
            "prediction_id": "p2", "symbol": "META", "feature": "hypothesis",
            "direction": "SELL", "confidence": 87, "model_version": "v1",
            "timestamp": _now_iso(-10), "price_at_prediction": 500.0,
            "verified_24h": {
                "correct": False, "grade": "WEAK_MISS",
                "verified_at": _now_iso(-3), "failure_code": "REGIME_SHIFT",
                "price": 510.0,
            },
        },
        # Excluded: too low confidence (75%)
        {
            "prediction_id": "p3", "symbol": "AAPL", "feature": "war_room",
            "direction": "BUY", "confidence": 0.75,
            "timestamp": _now_iso(-10),
            "verified_24h": {"correct": False, "grade": "STRONG_MISS", "verified_at": _now_iso(-2)},
        },
        # Excluded: NEUTRAL — correct is None, not False
        {
            "prediction_id": "p4", "symbol": "TSLA", "feature": "war_room",
            "direction": "HOLD", "confidence": 0.95,
            "timestamp": _now_iso(-10),
            "verified_24h": {"correct": None, "grade": "NEUTRAL", "verified_at": _now_iso(-2)},
        },
        # Excluded: outside window (verified 10 days ago, days=2)
        {
            "prediction_id": "p5", "symbol": "AMZN", "feature": "war_room",
            "direction": "BUY", "confidence": 0.9,
            "timestamp": _now_iso(-300),
            "verified_24h": {"correct": False, "grade": "STRONG_MISS", "verified_at": _now_iso(-240)},
        },
        # Included: another NVDA miss for offender ranking
        {
            "prediction_id": "p6", "symbol": "NVDA", "feature": "signal_dispatcher",
            "direction": "STRONG_BUY", "confidence": 0.95, "model_version": "v2",
            "timestamp": _now_iso(-10), "price_at_prediction": 920.0,
            "verified_24h": {
                "correct": False, "grade": "STRONG_MISS",
                "verified_at": _now_iso(-4), "failure_code": "TECH_FAKEOUT",
                "price": 890.0,
            },
        },
    ]
    db = _mk_db(docs)
    sectors = {"NVDA": "Technology", "META": "Communication Services"}
    with patch("services.toxic_autopsy_service.resolve_sectors_bulk",
               new=AsyncMock(return_value=sectors)):
        out = await build_autopsy(db, days=2, min_confidence_pct=80.0)

    assert out["total_failures"] == 3, "expected 3 included rows (p1, p2, p6)"
    assert out["total_misses_in_window"] == 4  # p1, p2, p3, p6 — all false-correct in window
    assert out["unique_symbols"] == 2
    # Avg conf on 0-100 scale: (92 + 87 + 95) / 3 = 91.33
    assert abs(out["avg_confidence_pct"] - 91.33) < 0.1

    # Failure-code grouping
    codes = {g["key"]: g["count"] for g in out["by_failure_code"]}
    assert codes.get("TECH_FAKEOUT") == 2
    assert codes.get("REGIME_SHIFT") == 1

    # Feature grouping
    features = {g["key"]: g["count"] for g in out["by_feature"]}
    assert features["war_room"] == 1
    assert features["hypothesis"] == 1
    assert features["signal_dispatcher"] == 1

    # Direction grouping — STRONG_BUY and BUY both collapse to BULLISH
    directions = {g["key"]: g["count"] for g in out["by_direction"]}
    assert directions.get("BULLISH") == 2
    assert directions.get("BEARISH") == 1

    # Confidence buckets
    buckets = {g["key"]: g["count"] for g in out["by_confidence_bucket"]}
    assert buckets.get("90-95") == 1   # NVDA p1 at 92
    assert buckets.get("85-90") == 1   # META p2 at 87
    assert buckets.get("95-100") == 1  # NVDA p6 at 95

    # Sector grouping
    sector_counts = {g["key"]: g["count"] for g in out["by_sector"]}
    assert sector_counts.get("Technology") == 2
    assert sector_counts.get("Communication Services") == 1

    # Top offender: NVDA wins on count
    top = out["top_offenders"]
    assert top[0]["symbol"] == "NVDA"
    assert top[0]["count"] == 2
    assert top[0]["sector"] == "Technology"
    assert top[0]["failure_codes"].get("TECH_FAKEOUT") == 2

    # Samples respect the cap & contain sector + prediction ids.
    sample_ids = {s["prediction_id"] for s in out["samples"]}
    assert sample_ids == {"p1", "p2", "p6"}
    assert all(s.get("sector") for s in out["samples"])


@pytest.mark.asyncio
async def test_build_autopsy_handles_none_db():
    out = await build_autopsy(None, days=2)
    assert out["total_failures"] == 0
    assert out.get("error") == "database_unavailable"


def test_bucket_for_boundaries():
    assert _bucket_for(80.0) == "80-85"
    assert _bucket_for(84.99) == "80-85"
    assert _bucket_for(85.0) == "85-90"
    assert _bucket_for(95.0) == "95-100"
    assert _bucket_for(100.0) == "95-100"
    assert _bucket_for(50.0) == "other"


def test_direction_family_mapping():
    assert _direction_family("BUY") == "BULLISH"
    assert _direction_family("STRONG_BUY") == "BULLISH"
    assert _direction_family("WEAK_SELL") == "BEARISH"
    assert _direction_family("HOLD") == "NEUTRAL"
    assert _direction_family("FOO") == "FOO"
    assert _direction_family("") == "UNKNOWN"


if __name__ == "__main__":
    asyncio.run(test_build_autopsy_empty_db())
    print("OK")
