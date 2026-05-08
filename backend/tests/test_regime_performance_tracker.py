"""Tests for the analytics-only Regime-Conditioned Performance Tracker.

Pins:
  * win-rate / avg-pnl / avg-confidence / calibration-gap math.
  * unresolved rows are excluded from rate math but counted in
    ``samples``.
  * empty input is safe.
  * the envelope's safety flags are unconditionally ``False``.
  * the module performs no DB writes and does not pull in any
    decision-stack / execution / memory-writer module.
"""
from __future__ import annotations

import importlib

import pytest

from services.regime_performance_tracker import (
    RegimePerformance,
    RegimePerformanceTracker,
    ResolvedTradeRow,
)


# ─── helpers ─────────────────────────────────────────────────────────


def _row(**overrides) -> ResolvedTradeRow:
    base = dict(
        symbol="NVDA",
        date="2024-06-01",
        action="BUY",
        confidence=0.70,
        win=True,
        pnl_pct=1.5,
        regime_label="expansion",
        vix_level="low",
        yield_curve="inverted",
        liquidity="high",
        market_event="AI_BUBBLE",
        event_family="bubble",
        is_crisis=False,
        regime_id="abc123",
    )
    base.update(overrides)
    return ResolvedTradeRow(**base)


@pytest.fixture
def tracker() -> RegimePerformanceTracker:
    return RegimePerformanceTracker()


# ─── single-bucket math ──────────────────────────────────────────────


def test_win_rate_and_avg_pnl_basic(tracker):
    rows = [
        _row(win=True, pnl_pct=2.0, confidence=0.8),
        _row(win=False, pnl_pct=-1.0, confidence=0.6),
        _row(win=True, pnl_pct=1.0, confidence=0.7),
    ]
    out = tracker.group_by(rows, "event_family")
    perf = out["bubble"]
    assert isinstance(perf, RegimePerformance)
    assert perf.samples == 3
    assert perf.wins == 2
    assert perf.losses == 1
    assert perf.unresolved == 0
    assert perf.win_rate == pytest.approx(2 / 3)
    assert perf.avg_pnl_pct == pytest.approx((2.0 - 1.0 + 1.0) / 3)
    assert perf.avg_confidence == pytest.approx((0.8 + 0.6 + 0.7) / 3)


def test_calibration_gap_equals_confidence_minus_winrate(tracker):
    rows = [
        _row(win=True, confidence=0.9),
        _row(win=False, confidence=0.9),
        _row(win=False, confidence=0.9),
        _row(win=False, confidence=0.9),
    ]
    perf = tracker.group_by(rows, "event_family")["bubble"]
    # confidence 0.9 vs win rate 0.25 → overconfident by 0.65.
    assert perf.win_rate == pytest.approx(0.25)
    assert perf.avg_confidence == pytest.approx(0.9)
    assert perf.calibration_gap == pytest.approx(0.65)


def test_calibration_gap_can_be_negative_for_underconfident(tracker):
    rows = [
        _row(win=True, confidence=0.5),
        _row(win=True, confidence=0.5),
        _row(win=True, confidence=0.5),
        _row(win=False, confidence=0.5),
    ]
    perf = tracker.group_by(rows, "event_family")["bubble"]
    # confidence 0.5 vs win rate 0.75 → underconfident by 0.25.
    assert perf.calibration_gap == pytest.approx(-0.25)


def test_unresolved_rows_excluded_from_rate_math(tracker):
    rows = [
        _row(win=True, pnl_pct=2.0, confidence=0.8),
        _row(win=None, pnl_pct=None, confidence=0.7),
        _row(win=None, pnl_pct=None, confidence=0.6),
    ]
    perf = tracker.group_by(rows, "event_family")["bubble"]
    assert perf.samples == 3
    assert perf.unresolved == 2
    assert perf.wins == 1
    assert perf.losses == 0
    assert perf.win_rate == pytest.approx(1.0)
    assert perf.avg_pnl_pct == pytest.approx(2.0)


def test_all_unresolved_yields_none_metrics(tracker):
    rows = [
        _row(win=None, pnl_pct=None, confidence=0.7),
        _row(win=None, pnl_pct=None, confidence=0.6),
    ]
    perf = tracker.group_by(rows, "event_family")["bubble"]
    assert perf.samples == 2
    assert perf.unresolved == 2
    assert perf.wins == 0
    assert perf.losses == 0
    assert perf.win_rate is None
    assert perf.avg_pnl_pct is None
    assert perf.calibration_gap is None


def test_partial_pnl_and_confidence_handled_gracefully(tracker):
    rows = [
        _row(win=True, pnl_pct=2.0, confidence=None),
        _row(win=False, pnl_pct=None, confidence=0.6),
    ]
    perf = tracker.group_by(rows, "event_family")["bubble"]
    # win rate = 1/2 = 0.5; avg_pnl uses only the row with pnl=2.0.
    assert perf.win_rate == pytest.approx(0.5)
    assert perf.avg_pnl_pct == pytest.approx(2.0)
    # avg confidence uses only the row with conf=0.6.
    assert perf.avg_confidence == pytest.approx(0.6)
    # calibration_gap is computable: 0.6 - 0.5 = 0.1
    assert perf.calibration_gap == pytest.approx(0.1)


# ─── grouping ────────────────────────────────────────────────────────


def test_group_by_splits_rows_correctly(tracker):
    rows = [
        _row(market_event="AI_BUBBLE", event_family="bubble", win=True,
             pnl_pct=1.5, confidence=0.8),
        _row(market_event="AI_BUBBLE", event_family="bubble", win=True,
             pnl_pct=2.0, confidence=0.7),
        _row(market_event="COVID_CRASH", event_family="crash", win=False,
             pnl_pct=-3.0, confidence=0.6),
    ]
    by_event = tracker.group_by(rows, "market_event")
    assert set(by_event.keys()) == {"AI_BUBBLE", "COVID_CRASH"}
    assert by_event["AI_BUBBLE"].samples == 2
    assert by_event["AI_BUBBLE"].win_rate == pytest.approx(1.0)
    assert by_event["COVID_CRASH"].samples == 1
    assert by_event["COVID_CRASH"].win_rate == pytest.approx(0.0)


def test_group_by_with_unknown_field_falls_back_to_unknown(tracker):
    rows = [_row()]
    out = tracker.group_by(rows, "this_field_does_not_exist")
    assert out == {"unknown": out["unknown"]}
    assert out["unknown"].samples == 1


# ─── full_report ─────────────────────────────────────────────────────


def test_full_report_envelope_carries_explicit_safety_flags(tracker):
    rows = [
        _row(win=True, pnl_pct=1.0, confidence=0.7),
        _row(market_event="COVID_CRASH", event_family="crash",
             is_crisis=True, win=False, pnl_pct=-2.0, confidence=0.55),
    ]
    report = tracker.full_report(rows)
    # Hard rails — must not be opt-in.
    assert report["safe_for_trade_promotion"] is False
    assert report["safe_for_memory"] is False
    assert report["can_change_direction"] is False
    assert report["can_increase_risk"] is False
    # Every advertised dimension is present.
    for dim in (
        "by_market_event", "by_event_family", "by_regime_label",
        "by_vix_level", "by_liquidity", "by_yield_curve", "by_regime_id",
    ):
        assert dim in report["reports"]
    # Each dimension serialises buckets as plain dicts (asdict-ed).
    for dim, buckets in report["reports"].items():
        for bucket_name, bucket_obj in buckets.items():
            assert isinstance(bucket_obj, dict), f"{dim}/{bucket_name}"
            assert "samples" in bucket_obj


def test_full_report_handles_empty_rows(tracker):
    report = tracker.full_report([])
    assert report["safe_for_trade_promotion"] is False
    for dim_buckets in report["reports"].values():
        assert dim_buckets == {}


# ─── ISOLATION CONTRACT ──────────────────────────────────────────────


def test_module_does_not_import_decision_or_execution_layers():
    import services.regime_performance_tracker as mod
    src = open(mod.__file__).read()
    forbidden_imports = [
        "from services.prediction_tracker",
        "from services.confidence_gate",
        "from services.conviction_service",
        "from services.commander_decision_stream",
        "from services.commander_phase2_brake",
        "from services.council_risk_modulator",
        "from services.regime_memory_retrieval",
        "from services.adversarial_enforcer",
        "from services.adversarial_promotion_gate",
        "from services.broker_service",
        "from services.crypto_paper_trader",
        "from services.paper_trading",
        "from routes.options_trading",
        "from routes.broker",
        "from routes.trading",
        "from motor",
        "from pymongo",
        "AsyncIOMotor",
    ]
    for token in forbidden_imports:
        assert token not in src, (
            f"forbidden import found in analytics-only tracker: {token}"
        )


def test_module_performs_no_db_writes():
    import services.regime_performance_tracker as mod
    src = open(mod.__file__).read()
    write_patterns = [
        ".insert_one(",
        ".insert_many(",
        ".update_one(",
        ".update_many(",
        ".replace_one(",
        ".delete_one(",
        ".delete_many(",
        ".find_one_and_update(",
    ]
    for token in write_patterns:
        assert token not in src, (
            f"DB write pattern found in analytics-only tracker: {token}"
        )


def test_module_can_be_imported_in_isolation():
    importlib.import_module("services.regime_performance_tracker")
