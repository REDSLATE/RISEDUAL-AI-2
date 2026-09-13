"""Chasing-filter audit — the operator's forensic diagnostic for the
24/24 rejection finding. Classifies each rejection into one of five
buckets so we know whether the cap is right and Alpha is late, the
cap is miscalibrated, the anchors are broken, or the pipeline is slow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.alpha_chasing_audit import (
    audit_rejections, classify_rejection, current_caps,
)


# ── Classification ────────────────────────────────────────────────

def _detail(**over):
    """Baseline enriched-payload detail; override fields per test."""
    base = {
        "move_pct": 4.5,
        "cap_pct": 4.0,
        "excess_over_cap": 0.5,
        "setup_type": "breakout",
        "direction_class": "momentum",
        "knife_mult": 2.0,
        "reference_price": 100.0,
        "today_close": 104.5,
        "signal_price": 104.0,
        "move_at_signal_pct": 4.0,
        "adverse_drift_since_signal_pct": 0.48,
        "signal_age_seconds": 45.0,
    }
    base.update(over)
    return base


def test_extreme_move_reference_anomaly_verdict_forces_anomaly_bucket():
    """When the validator says REFERENCE_PRICE_ANOMALY, we route to
    the anomaly bucket regardless of the coarse move/cap ratio."""
    d = _detail(
        move_pct=27.0, cap_pct=4.0,
        extreme_move_verdict={"verdict": "REFERENCE_PRICE_ANOMALY", "checks": {}},
    )
    assert classify_rejection(d) == "reference_anomaly"


def test_extreme_move_confirmed_verdict_routes_to_extended():
    """CONFIRMED means the move is real → clearly_extended."""
    d = _detail(
        move_pct=27.0, cap_pct=4.0,
        extreme_move_verdict={"verdict": "EXTREME_MOVE_CONFIRMED", "checks": {}},
    )
    assert classify_rejection(d) == "clearly_extended"


def test_extreme_move_unverified_falls_through_to_normal():
    """UNVERIFIED does NOT override normal classification."""
    d = _detail(
        move_pct=8.0, cap_pct=4.0, today_close=108.0,
        move_at_signal_pct=7.5, signal_price=107.5,
        extreme_move_verdict={"verdict": "EXTREME_MOVE_UNVERIFIED", "checks": {}},
    )
    # 8/4 = 2× > 1.5× → clearly_extended by normal path
    assert classify_rejection(d) == "clearly_extended"


def test_clearly_extended_when_move_is_ge_1_5x_cap():
    d = _detail(move_pct=7.0, cap_pct=4.0, today_close=107.0,
                move_at_signal_pct=6.5, signal_price=106.5)
    assert classify_rejection(d) == "clearly_extended"


def test_marginally_over_below_1_5x_cap():
    d = _detail(move_pct=4.3, cap_pct=4.0, today_close=104.3,
                move_at_signal_pct=4.1, signal_price=104.1)
    assert classify_rejection(d) == "marginally_over"


def test_stale_signal_was_ok_at_signal_time():
    """Move was inside cap when signal fired, extended during
    pipeline processing. Fix the pipeline, not the cap."""
    d = _detail(
        move_pct=4.2, cap_pct=4.0, today_close=104.2,
        move_at_signal_pct=2.5, signal_price=102.5,
        signal_age_seconds=240.0,   # 4 min old
    )
    assert classify_rejection(d) == "stale_signal"


def test_reference_anomaly_when_signal_price_disagrees_with_anchor():
    """signal_price would imply a much different move_at_signal than
    what was recorded. Broken anchor."""
    d = _detail(
        move_pct=4.5, cap_pct=4.0, today_close=104.5,
        # signal_price says 110 but move_at_signal was 4% off 100.
        # Actual signal-vs-ref = 10% → residual 6% > tolerance.
        signal_price=110.0, reference_price=100.0,
        move_at_signal_pct=4.0,
    )
    assert classify_rejection(d) == "reference_anomaly"


def test_insufficient_data_when_payload_missing_move_or_cap():
    """Only rows lacking move_pct or cap_pct are truly unclassifiable.
    Missing signal fields fall back to the coarse move/cap classifier."""
    assert classify_rejection({"cap_pct": 4.0}) == "insufficient_data"
    assert classify_rejection({"move_pct": 5.0}) == "insufficient_data"
    assert classify_rejection({}) == "insufficient_data"


def test_missing_enrichment_falls_back_to_coarse_classifier():
    """Pre-enrichment rows (only move + cap + setup_type) should still
    classify as clearly_extended vs marginally_over so historical
    data remains actionable."""
    # 8% move / 4% cap → 2× → clearly extended
    assert classify_rejection({"move_pct": 8.0, "cap_pct": 4.0, "setup_type": "breakout"}) == "clearly_extended"
    # 4.5% move / 4% cap → 1.125× → marginally over
    assert classify_rejection({"move_pct": 4.5, "cap_pct": 4.0, "setup_type": "breakout"}) == "marginally_over"


def test_insufficient_data_when_cap_zero():
    d = _detail(cap_pct=0)
    assert classify_rejection(d) == "insufficient_data"


def test_stale_takes_precedence_over_marginal_when_both_apply():
    """A 4.3% move + 4% cap that was 2% at signal + 240s old
    should be classified stale_signal (root cause is latency, not
    the cap being wrong)."""
    d = _detail(
        move_pct=4.3, cap_pct=4.0, today_close=104.3,
        move_at_signal_pct=2.0, signal_price=102.0,
        signal_age_seconds=240.0,
    )
    assert classify_rejection(d) == "stale_signal"


def test_reference_anomaly_takes_precedence_over_stale():
    """Broken anchor is more fundamental than stale timing."""
    d = _detail(
        move_pct=4.5, cap_pct=4.0, today_close=104.5,
        signal_price=110.0, reference_price=100.0,
        move_at_signal_pct=4.0, signal_age_seconds=300.0,
    )
    assert classify_rejection(d) == "reference_anomaly"


# ── Per-pattern caps ──────────────────────────────────────────────

def test_current_caps_covers_all_advertised_patterns():
    caps = current_caps(4.0)
    for p in ["VWAP_RECLAIM", "HOD_BREAK", "BREAKOUT", "PULLBACK",
              "MOMENTUM_REACCEL", "MEAN_REVERSION", "DOUBLE_BOTTOM",
              "INVERSE_HEAD_SHOULDERS", "FALLING_WEDGE",
              "SHORT_SIDE_EXHAUSTION"]:
        assert p in caps
        assert caps[p]["upper_cap_pct"] == 4.0


def test_momentum_patterns_have_no_lower_knife():
    caps = current_caps(4.0)
    assert caps["BREAKOUT"]["lower_knife_pct"] is None
    assert caps["VWAP_RECLAIM"]["lower_knife_pct"] is None


def test_dip_buy_patterns_have_negative_knife():
    caps = current_caps(4.0)
    assert caps["PULLBACK"]["lower_knife_pct"] == -8.0  # -2×
    assert caps["DOUBLE_BOTTOM"]["lower_knife_pct"] == -8.0


def test_short_side_exhaustion_has_widened_knife():
    caps = current_caps(4.0)
    assert caps["SHORT_SIDE_EXHAUSTION"]["lower_knife_pct"] == -12.0  # -3×


# ── audit_rejections aggregation ──────────────────────────────────

class _FakeCursor:
    def __init__(self, rows): self._rows = rows
    def limit(self, n):
        self._rows = self._rows[:n]; return self
    async def to_list(self, length): return self._rows


class _FakeSkipLog:
    def __init__(self, rows): self.rows = rows
    def find(self, query, *, sort=None):
        # Filter by reason and ts >= cutoff.
        cutoff = query.get("ts", {}).get("$gte")
        reason = query.get("reason")
        out = [
            r for r in self.rows
            if r.get("reason") == reason
            and (cutoff is None or r.get("ts") >= cutoff)
        ]
        if sort:
            out.sort(key=lambda r: r.get("ts") or datetime.min.replace(tzinfo=timezone.utc),
                      reverse=(sort[0][1] == -1))
        return _FakeCursor(out)


class _FakeDB:
    def __init__(self, rows): self.intent_skip_log = _FakeSkipLog(rows)


@pytest.mark.asyncio
async def test_audit_returns_no_db_shape_when_db_missing():
    result = await audit_rejections(None, env_cap_pct=4.0)
    assert result == {"total": 0, "reason": "no_db"}


@pytest.mark.asyncio
async def test_audit_classifies_mixed_bucket_rows():
    now = datetime.now(timezone.utc)
    rows = [
        # Clearly extended
        {"reason": "chasing_filter", "symbol": "AAPL", "ts": now,
         "detail": _detail(move_pct=8.0, cap_pct=4.0)},
        {"reason": "chasing_filter", "symbol": "MSFT", "ts": now,
         "detail": _detail(move_pct=7.5, cap_pct=4.0)},
        # Marginally over
        {"reason": "chasing_filter", "symbol": "NVDA", "ts": now,
         "detail": _detail(move_pct=4.2, cap_pct=4.0)},
        # Stale signal
        {"reason": "chasing_filter", "symbol": "TSLA", "ts": now,
         "detail": _detail(move_pct=4.3, cap_pct=4.0,
                            move_at_signal_pct=2.0, signal_age_seconds=300.0)},
        # Insufficient data — no move_pct at all
        {"reason": "chasing_filter", "symbol": "AMD", "ts": now,
         "detail": {"cap_pct": 4.0}},
        # Non-chasing rejection — must be excluded
        {"reason": "market_closed", "symbol": "SPY", "ts": now,
         "detail": {}},
    ]
    db = _FakeDB(rows)
    result = await audit_rejections(db, env_cap_pct=4.0, since_seconds=3600)
    assert result["total"] == 5   # excludes market_closed
    assert result["buckets"]["clearly_extended"]["count"] == 2
    assert result["buckets"]["marginally_over"]["count"] == 1
    assert result["buckets"]["stale_signal"]["count"] == 1
    assert result["buckets"]["insufficient_data"]["count"] == 1
    assert result["env_cap_pct"] == 4.0
    assert "caps_by_pattern" in result


@pytest.mark.asyncio
async def test_audit_respects_since_window():
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=2)
    rows = [
        {"reason": "chasing_filter", "symbol": "AAPL", "ts": old,
         "detail": _detail(move_pct=8.0, cap_pct=4.0)},
        {"reason": "chasing_filter", "symbol": "MSFT", "ts": now,
         "detail": _detail(move_pct=8.0, cap_pct=4.0)},
    ]
    db = _FakeDB(rows)
    result = await audit_rejections(db, env_cap_pct=4.0, since_seconds=3600)
    # Only the recent one is in-window.
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_audit_recommendation_flags_reference_anomaly_dominance():
    now = datetime.now(timezone.utc)
    rows = [
        {"reason": "chasing_filter", "symbol": f"S{i}", "ts": now,
         "detail": _detail(signal_price=110.0, reference_price=100.0,
                            move_at_signal_pct=4.0)}
        for i in range(10)
    ]
    db = _FakeDB(rows)
    result = await audit_rejections(db, env_cap_pct=4.0)
    assert "reference-price anomalies" in result["recommendation"]


@pytest.mark.asyncio
async def test_audit_recommendation_flags_stale_dominance():
    now = datetime.now(timezone.utc)
    rows = [
        {"reason": "chasing_filter", "symbol": f"S{i}", "ts": now,
         "detail": _detail(move_pct=4.3, cap_pct=4.0,
                            move_at_signal_pct=2.0, signal_age_seconds=300.0)}
        for i in range(10)
    ]
    db = _FakeDB(rows)
    result = await audit_rejections(db, env_cap_pct=4.0)
    assert "pipeline latency" in result["recommendation"]


@pytest.mark.asyncio
async def test_audit_recommendation_flags_marginal_widening_candidate():
    now = datetime.now(timezone.utc)
    rows = [
        {"reason": "chasing_filter", "symbol": f"S{i}", "ts": now,
         "detail": _detail(move_pct=4.2, cap_pct=4.0)}
        for i in range(10)
    ]
    db = _FakeDB(rows)
    result = await audit_rejections(db, env_cap_pct=4.0)
    assert "widening" in result["recommendation"]


@pytest.mark.asyncio
async def test_audit_recommendation_flags_clearly_extended_dominance():
    now = datetime.now(timezone.utc)
    rows = [
        {"reason": "chasing_filter", "symbol": f"S{i}", "ts": now,
         "detail": _detail(move_pct=8.0, cap_pct=4.0)}
        for i in range(10)
    ]
    db = _FakeDB(rows)
    result = await audit_rejections(db, env_cap_pct=4.0)
    assert "discovery timing" in result["recommendation"]


@pytest.mark.asyncio
async def test_audit_recommendation_empty_window():
    db = _FakeDB([])
    result = await audit_rejections(db, env_cap_pct=4.0)
    assert result["total"] == 0
    assert "No chasing_filter rejections" in result["recommendation"]
