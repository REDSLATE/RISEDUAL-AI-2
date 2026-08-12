"""Tests for Phase C additions: hot store dedup + pattern rollup + break-even math."""
from __future__ import annotations

import os
import tempfile

import pytest

from services import alpha_hot_store
from services.alpha_pattern_performance import _summarize
from services.alpha_breakeven import _r_multiple


@pytest.fixture(autouse=True)
def _isolated_hot_store(monkeypatch):
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "alpha_hot_store.sqlite")
    alpha_hot_store.init(path)
    yield
    try:
        os.remove(path)
    except OSError:
        pass


# ── hot store ──────────────────────────────────────────

def test_hot_store_record_and_read_events():
    alpha_hot_store.record_event("s1", "setup_detected",
                                  stage="detected", symbol="AAA",
                                  payload={"score": 0.72})
    alpha_hot_store.record_event("s1", "triggered",
                                  stage="triggered", symbol="AAA",
                                  payload={"price": 100.0})
    events = alpha_hot_store.events_for_setup("s1")
    assert len(events) == 2
    assert events[0]["event"] == "setup_detected"
    assert events[1]["event"] == "triggered"
    assert events[1]["payload"]["price"] == 100.0


def test_hot_store_latency_samples():
    alpha_hot_store.record_latency("s1", "signal_to_trigger", 340)
    alpha_hot_store.record_latency("s1", "signal_to_trigger", 220)
    alpha_hot_store.record_latency("s1", "trigger_to_intent", 5)
    samples = alpha_hot_store.latency_samples("s1")
    assert sorted(samples["signal_to_trigger"]) == [220, 340]
    assert samples["trigger_to_intent"] == [5]


def test_symbol_lock_blocks_second_caller():
    ok1 = alpha_hot_store.try_acquire_symbol_lock("AAA", setup_id="s1", source="alpha_daytrader")
    ok2 = alpha_hot_store.try_acquire_symbol_lock("AAA", setup_id="s2", source="day_trade_scanner")
    assert ok1 is True
    assert ok2 is False


def test_symbol_lock_reentrant_for_same_setup():
    a = alpha_hot_store.try_acquire_symbol_lock("BBB", setup_id="s1", source="alpha_daytrader")
    b = alpha_hot_store.try_acquire_symbol_lock("BBB", setup_id="s1", source="alpha_daytrader")
    assert a and b


def test_symbol_lock_release_lets_other_scanner_in():
    alpha_hot_store.try_acquire_symbol_lock("CCC", setup_id="s1", source="alpha_daytrader")
    alpha_hot_store.release_symbol_lock("CCC", setup_id="s1")
    ok = alpha_hot_store.try_acquire_symbol_lock("CCC", setup_id="s2", source="day_trade_scanner")
    assert ok is True


# ── pattern performance ───────────────────────────────

def test_summarize_empty_returns_low_confidence():
    s = _summarize([])
    assert s["unique_setups"] == 0
    assert s["sample_confidence"] == "low"


def test_summarize_computes_expectancy_and_pf():
    rows = [
        {"triggered": True, "order_submitted": True, "realized_r": 1.5},
        {"triggered": True, "order_submitted": True, "realized_r": 2.0},
        {"triggered": True, "order_submitted": True, "realized_r": -1.0},
    ]
    s = _summarize(rows)
    assert s["wins"] == 2
    assert s["losses"] == 1
    assert s["win_rate"] == round(2 / 3, 3)
    # avg_win = 1.75, avg_loss = -1.0 → expectancy = 2/3 * 1.75 + 1/3 * -1.0 ≈ 0.833
    assert s["expectancy_r"] == round(2 / 3 * 1.75 + 1 / 3 * -1.0, 3)
    # PF = wins_sum / |losses_sum| = 3.5 / 1.0 = 3.5
    assert s["profit_factor"] == 3.5


def test_summarize_confidence_tiers():
    lo = _summarize([{"realized_r": 1.0}] * 5)
    md = _summarize([{"realized_r": 1.0}] * 15)
    hi = _summarize([{"realized_r": 1.0}] * 40)
    assert lo["sample_confidence"] == "low"
    assert md["sample_confidence"] == "medium"
    assert hi["sample_confidence"] == "high"


# ── break-even math ───────────────────────────────────

def test_r_multiple_computes_correctly():
    # entry=100, stop=99 → risk=1 per share. At price=102 → 2R.
    assert _r_multiple(102.0, 100.0, 99.0) == 2.0
    assert _r_multiple(101.0, 100.0, 99.0) == 1.0
    # Under water
    assert _r_multiple(99.5, 100.0, 99.0) == -0.5


def test_r_multiple_rejects_invalid_stop():
    assert _r_multiple(102.0, 100.0, 100.0) is None  # zero risk
    assert _r_multiple(102.0, 100.0, 101.0) is None  # stop above entry
