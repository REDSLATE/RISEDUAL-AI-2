"""Tests for the Foundation v2.1 ports: kill switch (fail-closed),
RVOL time-of-day baseline, and NYSE session state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


# ─────────────────────────────────────────────
#  Isolated SQLite hot store for kill switch + RVOL
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_hot_store(tmp_path, monkeypatch):
    db_path = tmp_path / "hs.db"
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(db_path))
    from services import alpha_hot_store
    alpha_hot_store._DB_PATH = None
    alpha_hot_store.init()
    yield


# ─────────────────────────────────────────────
#  Hardware Kill Switch
# ─────────────────────────────────────────────

def test_kill_switch_default_is_clean():
    from services import alpha_hardware_kill_switch as hw
    tripped, reason = hw.check()
    assert tripped is False
    assert reason is None


def test_kill_switch_explicit_trip():
    from services import alpha_hardware_kill_switch as hw
    hw.trip("operator halt for maintenance", by="admin@risedual.ai")
    tripped, reason = hw.check()
    assert tripped is True
    assert "operator halt" in reason


def test_kill_switch_reset_requires_confirmed_by():
    from services import alpha_hardware_kill_switch as hw
    hw.trip("test", by="test")
    with pytest.raises(ValueError):
        hw.reset(confirmed_by="")
    hw.reset(confirmed_by="admin")
    tripped, _ = hw.check()
    assert tripped is False


def test_kill_switch_consecutive_errors_trip():
    """N consecutive errors trip the switch."""
    import os
    os.environ["ALPHA_HW_KILL_MAX_ERRORS"] = "3"
    from services import alpha_hardware_kill_switch as hw
    for i in range(2):
        hw.record_error(f"attempt-{i}")
    tripped, _ = hw.check()
    assert tripped is False  # only 2 so far
    hw.record_error("attempt-3")
    tripped, reason = hw.check()
    assert tripped is True
    assert "consecutive errors" in reason


def test_kill_switch_success_resets_error_count():
    import os
    os.environ["ALPHA_HW_KILL_MAX_ERRORS"] = "3"
    from services import alpha_hardware_kill_switch as hw
    hw.record_error("a")
    hw.record_error("b")
    hw.record_success()
    hw.record_error("c")
    tripped, _ = hw.check()
    assert tripped is False, "success should have reset the counter to 0"


def test_kill_switch_fail_closed_on_corrupt_row():
    """Foundation v2.1 core guarantee — malformed state = TRIPPED."""
    from services import alpha_hot_store
    with alpha_hot_store._connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS alpha_hw_kill_switch("
                    "id INTEGER PRIMARY KEY CHECK (id = 1), "
                    "state_json TEXT NOT NULL, "
                    "updated_at_ns INTEGER NOT NULL)")
        con.execute(
            "INSERT OR REPLACE INTO alpha_hw_kill_switch(id, state_json, updated_at_ns) "
            "VALUES (1, ?, ?)",
            ("{not valid json", 0),
        )
    from services import alpha_hardware_kill_switch as hw
    tripped, reason = hw.check()
    assert tripped is True
    assert reason.startswith("FAIL_CLOSED:state_not_json")


def test_kill_switch_fail_closed_on_missing_required_keys():
    from services import alpha_hot_store
    with alpha_hot_store._connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS alpha_hw_kill_switch("
                    "id INTEGER PRIMARY KEY CHECK (id = 1), "
                    "state_json TEXT NOT NULL, "
                    "updated_at_ns INTEGER NOT NULL)")
        con.execute(
            "INSERT OR REPLACE INTO alpha_hw_kill_switch(id, state_json, updated_at_ns) "
            "VALUES (1, ?, ?)",
            (json.dumps({"only": "some_key"}), 0),
        )
    from services import alpha_hardware_kill_switch as hw
    tripped, reason = hw.check()
    assert tripped is True
    assert "FAIL_CLOSED:state_missing_required_keys" == reason


def test_kill_switch_drawdown_trip():
    from services import alpha_hardware_kill_switch as hw
    import os
    os.environ["ALPHA_HW_KILL_MAX_DRAWDOWN_PCT"] = "10"
    hw.record_equity(100.0)  # sets peak
    hw.record_equity(95.0)   # 5% drawdown — under cap
    tripped, _ = hw.check()
    assert tripped is False
    hw.record_equity(85.0)   # 15% drawdown — over cap
    tripped, reason = hw.check()
    assert tripped is True
    assert "drawdown_limit_exceeded" in reason


# ─────────────────────────────────────────────
#  RVOL Time-of-Day Baseline
# ─────────────────────────────────────────────

def test_rvol_returns_none_during_warmup():
    """Foundation v2.1 core guarantee — no fabricated 1.0."""
    from services import alpha_volume_baseline as avb
    now = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)
    # No historical rows for this symbol/slot → warm-up
    rv, samples = avb.rvol("NFLX", now, current_volume=1_000_000)
    assert rv is None
    assert samples == 0


def test_rvol_resolves_after_enough_samples():
    from services import alpha_volume_baseline as avb
    from datetime import timedelta
    now = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)
    # Seed 5 prior days at the same slot
    for i in range(1, 6):
        dt = now - timedelta(days=i)
        avb.observe("NFLX", dt, volume=1_000_000)
    rv, samples = avb.rvol("NFLX", now, current_volume=2_000_000)
    assert samples == 5
    assert rv is not None
    assert abs(rv - 2.0) < 1e-6


def test_rvol_slots_use_utc_bucketing():
    """A 14:33 observation and a 14:44 observation must land in the
    same 15-minute slot; 14:59 goes to the next slot."""
    from services import alpha_volume_baseline as avb
    from datetime import timedelta
    base = datetime(2026, 9, 11, 14, 33, tzinfo=timezone.utc)
    # Seed 5 prior days at exactly the same slot (14:30 bucket)
    for i in range(1, 6):
        avb.observe("MSFT", base - timedelta(days=i, minutes=0), volume=100)
    # Query at 14:44 UTC (same 14:30 bucket) — should resolve.
    q1 = datetime(2026, 9, 11, 14, 44, tzinfo=timezone.utc)
    rv1, s1 = avb.rvol("MSFT", q1, current_volume=200)
    assert s1 == 5
    assert rv1 is not None
    # Query at 14:59 UTC (14:45 bucket) — different slot, warm-up
    q2 = datetime(2026, 9, 11, 14, 59, tzinfo=timezone.utc)
    rv2, s2 = avb.rvol("MSFT", q2, current_volume=200)
    assert s2 == 0
    assert rv2 is None


def test_rvol_ignores_zero_and_negative_volumes():
    from services import alpha_volume_baseline as avb
    now = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)
    avb.observe("BAD", now, 0)  # dropped
    avb.observe("BAD", now, -5)  # dropped
    avb.observe("BAD", now, None)  # dropped
    rv, samples = avb.rvol("BAD", now, current_volume=100)
    assert samples == 0
    assert rv is None


# ─────────────────────────────────────────────
#  Session State (NYSE + Holidays)
# ─────────────────────────────────────────────

def test_session_state_labor_day_is_holiday():
    """Labor Day 2026 (2026-09-07) — the exact scenario the
    operator reported as looking like a bug."""
    from services.alpha_session_state import get_session_state
    # 14:00 UTC on Labor Day = 10:00 ET, inside would-be RTH
    now = datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc)
    state = get_session_state(now)
    assert state["is_market_open"] is False
    assert state["phase"] == "closed_holiday"
    assert state["holiday_name"] == "Labor Day"
    assert "Labor Day" in state["note"]


def test_session_state_regular_weekday():
    """Wednesday, 2026-09-09, 14:30 UTC → 10:30 ET → regular."""
    from services.alpha_session_state import get_session_state
    now = datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)
    state = get_session_state(now)
    assert state["is_market_open"] is True
    assert state["phase"] == "regular"


def test_session_state_weekend():
    from services.alpha_session_state import get_session_state
    # Saturday 2026-09-05
    now = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
    state = get_session_state(now)
    assert state["is_market_open"] is False
    assert state["phase"] == "closed_weekend"


def test_session_state_pre_market():
    """Tuesday 2026-09-08, 08:00 ET (12:00 UTC) → pre-market."""
    from services.alpha_session_state import get_session_state
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    state = get_session_state(now)
    assert state["is_market_open"] is False
    assert state["phase"] == "pre_market"


def test_session_state_after_hours():
    """Tuesday 2026-09-08, 17:30 ET (21:30 UTC) → after-hours."""
    from services.alpha_session_state import get_session_state
    now = datetime(2026, 9, 8, 21, 30, tzinfo=timezone.utc)
    state = get_session_state(now)
    assert state["is_market_open"] is False
    assert state["phase"] == "after_hours"


def test_session_state_thanksgiving_early_close():
    """Day-after-Thanksgiving 2026-11-27 half-day. At 18:00 UTC
    (13:00 ET, right at the 13:00 early close), we should be in
    after-hours; at 17:59 UTC still regular."""
    from services.alpha_session_state import get_session_state
    just_before = datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc)
    state1 = get_session_state(just_before)
    assert state1["phase"] == "regular"
    # 15:00 ET / 20:00 UTC — after the 13:00 ET early close.
    after = datetime(2026, 11, 27, 20, 0, tzinfo=timezone.utc)
    state2 = get_session_state(after)
    assert state2["phase"] == "after_hours"
    assert state2["holiday_name"] and "early close" in state2["holiday_name"]
