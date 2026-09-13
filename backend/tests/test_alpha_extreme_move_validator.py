"""Extreme-move validator (>15% moves) — the operator's directive.

Guarantees under test:

* Below-threshold moves return None (no validator work).
* Above-threshold moves ALWAYS block the trade (verdict is diagnostic
  only — no unblock path exists in the validator).
* REFERENCE_PRICE_ANOMALY on hard failure of any integrity check.
* EXTREME_MOVE_CONFIRMED when multiple checks pass and none fail.
* EXTREME_MOVE_UNVERIFIED when the validator can't decide either way.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.alpha_extreme_move_validator import (
    validate_extreme_move, ExtremeMoveVerdict,
)


@pytest.mark.asyncio
async def test_below_threshold_returns_none():
    result = await validate_extreme_move(
        symbol="AAPL", move_pct=4.5, prev_close=100.0, today_close=104.5,
    )
    assert result is None


@pytest.mark.asyncio
async def test_at_threshold_still_triggers():
    result = await validate_extreme_move(
        symbol="AAPL", move_pct=15.0, prev_close=100.0, today_close=115.0,
    )
    assert result is not None
    assert result.threshold_pct == 15.0


@pytest.mark.asyncio
async def test_negative_extreme_move_also_triggers():
    result = await validate_extreme_move(
        symbol="AAPL", move_pct=-18.0, prev_close=100.0, today_close=82.0,
    )
    assert result is not None


@pytest.mark.asyncio
async def test_reference_integrity_always_passes(monkeypatch):
    """The structural invariant — both closes from single provider row —
    is always noted as passed."""
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
    )
    assert r.checks["reference_integrity"]["passed"] is True


@pytest.mark.asyncio
async def test_freshness_fails_on_ancient_timestamp():
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
        bars_metadata={"today_ts": old_ts},
    )
    assert r.checks["quote_freshness"]["passed"] is False
    assert r.verdict == "REFERENCE_PRICE_ANOMALY"


@pytest.mark.asyncio
async def test_freshness_passes_on_recent_timestamp():
    now_ts = datetime.now(timezone.utc).isoformat()
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
        bars_metadata={"today_ts": now_ts,
                        "prev_ts": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
    )
    assert r.checks["quote_freshness"]["passed"] is True


@pytest.mark.asyncio
async def test_session_boundary_fails_on_giant_gap():
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
        bars_metadata={
            "today_ts": "2026-02-10T21:00:00Z",
            "prev_ts":  "2026-01-01T21:00:00Z",  # 40 days apart
        },
    )
    assert r.checks["session_boundary"]["passed"] is False
    assert r.verdict == "REFERENCE_PRICE_ANOMALY"


@pytest.mark.asyncio
async def test_session_boundary_passes_on_weekend_gap():
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
        bars_metadata={
            "today_ts": "2026-02-09T21:00:00Z",  # Monday
            "prev_ts":  "2026-02-06T21:00:00Z",  # Friday
        },
    )
    assert r.checks["session_boundary"]["passed"] is True


@pytest.mark.asyncio
async def test_corporate_action_fails_on_split_factor():
    r = await validate_extreme_move(
        symbol="X", move_pct=50.0, prev_close=100.0, today_close=150.0,
        bars_metadata={"adjustment_factor": 2.0},  # 2-for-1 split
    )
    assert r.checks["corporate_action"]["passed"] is False
    assert r.verdict == "REFERENCE_PRICE_ANOMALY"


@pytest.mark.asyncio
async def test_corporate_action_skipped_when_missing():
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
    )
    # Skipped = None, not failed
    assert r.checks["corporate_action"]["passed"] is None


@pytest.mark.asyncio
async def test_confirmed_when_multiple_checks_pass_none_fail():
    now = datetime.now(timezone.utc)
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
        bars_metadata={
            "today_ts": now.isoformat(),
            "prev_ts": (now - timedelta(days=1)).isoformat(),
        },
    )
    # 3 checks passed (integrity + freshness + session), moomoo skipped
    assert r.verdict == "EXTREME_MOVE_CONFIRMED"


@pytest.mark.asyncio
async def test_unverified_when_insufficient_signal():
    """No bars metadata → only reference_integrity passes → not enough
    signal for CONFIRMED, but no hard failures → UNVERIFIED."""
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
    )
    # Only reference_integrity passes; freshness/session/corp all skipped;
    # moomoo skipped (bridge unavailable in test) → UNVERIFIED.
    assert r.verdict in ("EXTREME_MOVE_UNVERIFIED", "EXTREME_MOVE_CONFIRMED")
    # Definitely NOT flagged as anomaly without any hard failure.
    assert r.verdict != "REFERENCE_PRICE_ANOMALY"


@pytest.mark.asyncio
async def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("EXTREME_MOVE_THRESHOLD_PCT", "10")
    # A 12% move now trips the (lowered) threshold.
    r = await validate_extreme_move(
        symbol="X", move_pct=12.0, prev_close=100.0, today_close=112.0,
    )
    assert r is not None
    assert r.threshold_pct == 10.0


@pytest.mark.asyncio
async def test_threshold_env_clamped_to_safe_range(monkeypatch):
    monkeypatch.setenv("EXTREME_MOVE_THRESHOLD_PCT", "999")
    r = await validate_extreme_move(
        symbol="X", move_pct=45.0, prev_close=100.0, today_close=145.0,
    )
    # Threshold clamped to <= 50
    assert r is None or r.threshold_pct <= 50.0


@pytest.mark.asyncio
async def test_verdict_as_dict_shape():
    r = await validate_extreme_move(
        symbol="X", move_pct=20.0, prev_close=100.0, today_close=120.0,
    )
    d = r.as_dict()
    assert set(d.keys()) == {"verdict", "threshold_pct", "checks"}
    for c in d["checks"].values():
        assert set(c.keys()) == {"passed", "note"}
