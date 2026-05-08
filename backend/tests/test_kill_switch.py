"""Tests for `ai_core.kill_switch`.

Covers:
  * Drawdown trip — breach of `max_drawdown` activates the switch.
  * Error-rate trip — rolling window past `max_error_rate` trips,
    but only after the min-samples floor (≥5) to avoid a single
    early failure nuking the fleet.
  * Cooldown auto-clear — `is_active()` flips to False when the
    cooldown has elapsed, emitting a structured clear log line.
  * `guarded_execute` — short-circuits when active, records
    successes AND broker-style `{"error": ...}` failures, and
    supports both sync and async callables.
  * `reset()` wipes both flag and error window (not a stutter).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from ai_core.kill_switch import KillSwitch, guarded_execute


# ────────────────────────────────────────────────────────────────────────────
# Trip conditions
# ────────────────────────────────────────────────────────────────────────────

def test_drawdown_threshold_trips_immediately():
    ks = KillSwitch(max_drawdown=0.25)
    trip, reason = ks.should_trip(drawdown=0.30)
    assert trip is True
    assert "drawdown" in reason
    assert "30.00%" in reason or "30%" in reason


def test_drawdown_below_threshold_does_not_trip():
    ks = KillSwitch(max_drawdown=0.25)
    trip, _ = ks.should_trip(drawdown=0.10)
    assert trip is False


def test_error_rate_requires_min_samples():
    """One failure out of one is 100% error — but 1 sample is noise.
    Trip logic requires ≥5 samples before the rate branch fires."""
    ks = KillSwitch(max_error_rate=0.30, error_window=50)
    for _ in range(4):
        ks.record_result(success=False)
    trip, _ = ks.should_trip()
    assert trip is False

    ks.record_result(success=False)  # now 5 samples, 100% error
    trip, reason = ks.should_trip()
    assert trip is True
    assert "error rate" in reason


def test_error_rate_mixed_results_are_averaged():
    ks = KillSwitch(max_error_rate=0.30)
    for _ in range(7):
        ks.record_result(success=True)
    for _ in range(3):
        ks.record_result(success=False)
    # 3/10 = 30% → at threshold → trip (>= is inclusive).
    assert ks.error_rate() == pytest.approx(0.30)
    trip, _ = ks.should_trip()
    assert trip is True


# ────────────────────────────────────────────────────────────────────────────
# Activation / cooldown / reset
# ────────────────────────────────────────────────────────────────────────────

def test_activate_and_is_active_within_cooldown():
    ks = KillSwitch(cooldown_seconds=300)
    ks.activate("drawdown 30%")
    assert ks.is_active() is True
    status = ks.status()
    assert status["active"] is True
    assert status["last_reason"] == "drawdown 30%"
    assert status["trip_count"] == 1
    assert 0 < status["cooldown_remaining_seconds"] <= 300


def test_is_active_auto_clears_after_cooldown():
    ks = KillSwitch(cooldown_seconds=1)
    ks.activate("test")
    assert ks.is_active() is True
    # Force-age the tripped_at to past the cooldown.
    ks._tripped_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    assert ks.is_active() is False
    assert ks.status()["active"] is False


def test_reset_wipes_flag_and_error_window():
    ks = KillSwitch()
    for _ in range(10):
        ks.record_result(success=False)
    ks.activate("test trip")
    assert ks.status()["error_window_size"] == 10

    ks.reset()
    status = ks.status()
    assert status["active"] is False
    assert status["error_window_size"] == 0
    assert status["error_rate"] == 0.0


# ────────────────────────────────────────────────────────────────────────────
# guarded_execute
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guarded_execute_short_circuits_when_active():
    ks = KillSwitch()
    ks.activate("manual")
    called = False

    async def work():
        nonlocal called
        called = True
        return {"ok": True}

    result = await guarded_execute(work, switch=ks)
    assert called is False
    assert result["skipped"] is True
    assert result["reason"] == "kill switch active"
    assert "last_reason" in result


@pytest.mark.asyncio
async def test_guarded_execute_trips_on_drawdown_before_running():
    ks = KillSwitch(max_drawdown=0.20)
    called = False

    async def work():
        nonlocal called
        called = True

    result = await guarded_execute(work, drawdown=0.25, switch=ks)
    assert called is False
    assert result["skipped"] is True
    assert "kill switch tripped" in result["reason"]
    assert ks.is_active() is True


@pytest.mark.asyncio
async def test_guarded_execute_records_success_and_broker_error_dicts():
    ks = KillSwitch()

    async def ok():
        return {"order": "filled"}

    async def broker_error():
        return {"error": "rate limit"}

    await guarded_execute(ok, switch=ks)
    await guarded_execute(broker_error, switch=ks)

    # One success, one failure → 50% error rate.
    assert ks.error_rate() == 0.5


@pytest.mark.asyncio
async def test_guarded_execute_records_raised_exceptions():
    ks = KillSwitch()

    async def boom():
        raise RuntimeError("upstream 500")

    result = await guarded_execute(boom, switch=ks)
    assert result == {"skipped": True, "reason": "execution error"}
    assert ks.error_rate() == 1.0


@pytest.mark.asyncio
async def test_guarded_execute_propagates_cancellation():
    """CancelledError must NOT count toward the error window and
    must re-raise so the caller's task actually cancels."""
    ks = KillSwitch()

    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await guarded_execute(cancelled, switch=ks)
    assert ks.error_rate() == 0.0  # not recorded


@pytest.mark.asyncio
async def test_guarded_execute_supports_sync_callables():
    ks = KillSwitch()

    def sync_work(x, y):
        return {"sum": x + y}

    result = await guarded_execute(sync_work, 2, 3, switch=ks)
    assert result == {"sum": 5}


@pytest.mark.asyncio
async def test_guarded_execute_eager_trips_after_broker_error_storm():
    """After a broker-error storm pushes us past the rate threshold,
    the next guarded call should see an already-active switch
    without needing an explicit `should_trip()` poll."""
    ks = KillSwitch(max_error_rate=0.30, error_window=10)

    async def broker_error():
        return {"error": "500"}

    # 5 failures → 100% error rate → ≥ min samples → eager trip.
    for _ in range(5):
        await guarded_execute(broker_error, switch=ks)

    # Switch is now active; next call should short-circuit.
    async def work():
        raise AssertionError("should not run")

    result = await guarded_execute(work, switch=ks)
    assert result["skipped"] is True
    assert result["reason"] == "kill switch active"
