"""Tests for the broker execution circuit breaker.

Guarantees:

* CLOSED → OPEN when failures cross ``ERROR_THRESHOLD`` inside
  ``WINDOW_SECONDS``.
* OPEN suppresses further calls until ``COOLDOWN_SECONDS`` have
  elapsed, then HALF_OPEN lets exactly one probe through.
* HALF_OPEN + success → CLOSED (recovery).
* HALF_OPEN + failure → OPEN for another full cooldown.
* Failures outside the rolling window are pruned so a slow drip
  never trips the breaker.
* ``fetch_broker_quote`` short-circuits to ``None`` when OPEN
  (no HTTP touched).
* ``fetch_execution_quote`` marks results ``broker_degraded``
  when the breaker is OPEN and blocks auto-execute — the
  operator can tell "broker is degraded" apart from "symbol
  not in coverage".
"""

from __future__ import annotations

import time

import pytest

from services import broker_circuit_breaker as cb


# Every test starts from a clean singleton.
@pytest.fixture(autouse=True)
def _reset_breaker():
    cb._reset_for_tests()
    yield
    cb._reset_for_tests()


# ─────────────────────────────────────────────
#  State machine
# ─────────────────────────────────────────────
def test_starts_closed():
    """Fresh state → CLOSED, calls allowed, snapshot sane."""
    assert cb.allow_call() is True
    snap = cb.snapshot()
    assert snap["state"] == "closed"
    assert snap["failures_in_window"] == 0
    assert snap["opened_at"] is None


def test_trips_open_after_threshold_failures():
    """N failures inside the window → OPEN."""
    for i in range(cb.ERROR_THRESHOLD):
        cb.record_failure(reason=f"err{i}")
    snap = cb.snapshot()
    assert snap["state"] == "open"
    assert snap["failures_in_window"] == cb.ERROR_THRESHOLD
    assert "threshold_exceeded" in (snap["last_reason"] or "")
    # allow_call must now short-circuit.
    assert cb.allow_call() is False


def test_below_threshold_stays_closed():
    """``ERROR_THRESHOLD - 1`` failures must NOT trip the breaker."""
    for _ in range(cb.ERROR_THRESHOLD - 1):
        cb.record_failure(reason="err")
    assert cb.snapshot()["state"] == "closed"
    assert cb.allow_call() is True


def test_success_from_closed_does_not_change_state(monkeypatch):
    """CLOSED + success stays CLOSED but increments the
    observability counter."""
    cb.record_success()
    cb.record_success()
    snap = cb.snapshot()
    assert snap["state"] == "closed"
    assert snap["consecutive_successes"] == 2


# ─────────────────────────────────────────────
#  Cooldown → HALF_OPEN → CLOSED
# ─────────────────────────────────────────────
def _fake_time(base: float):
    """Return a monotonic clock function anchored at ``base``.

    Useful for driving the breaker through cooldowns without
    actually sleeping in tests.
    """
    holder = {"t": base}
    def _clock():
        return holder["t"]
    def _advance(secs: float):
        holder["t"] += secs
    return _clock, _advance


def test_cooldown_elapses_to_half_open(monkeypatch):
    """After COOLDOWN_SECONDS in OPEN, the next state read moves
    the breaker to HALF_OPEN and allows exactly one call."""
    base = time.time()
    clock, advance = _fake_time(base)
    monkeypatch.setattr(cb.time, "time", clock)

    for _ in range(cb.ERROR_THRESHOLD):
        cb.record_failure(reason="err")
    assert cb.snapshot()["state"] == "open"
    assert cb.allow_call() is False

    advance(cb.COOLDOWN_SECONDS + 1)
    # The state transition happens on next read.
    assert cb.allow_call() is True
    assert cb.snapshot()["state"] == "half_open"


def test_half_open_success_closes_circuit(monkeypatch):
    """Probe succeeds → CLOSED, failure buffer cleared."""
    base = time.time()
    clock, advance = _fake_time(base)
    monkeypatch.setattr(cb.time, "time", clock)

    for _ in range(cb.ERROR_THRESHOLD):
        cb.record_failure(reason="err")
    advance(cb.COOLDOWN_SECONDS + 1)
    assert cb.allow_call() is True  # transitions to HALF_OPEN

    cb.record_success()
    snap = cb.snapshot()
    assert snap["state"] == "closed"
    assert snap["failures_in_window"] == 0
    assert snap["consecutive_successes"] == 1


def test_half_open_failure_reopens_for_full_cooldown(monkeypatch):
    """Probe fails → OPEN again, cooldown restarts from now."""
    base = time.time()
    clock, advance = _fake_time(base)
    monkeypatch.setattr(cb.time, "time", clock)

    for _ in range(cb.ERROR_THRESHOLD):
        cb.record_failure(reason="err")
    advance(cb.COOLDOWN_SECONDS + 1)
    assert cb.allow_call() is True  # → HALF_OPEN

    cb.record_failure(reason="probe_boom")
    snap = cb.snapshot()
    assert snap["state"] == "open"
    assert "probe_failed" in (snap["last_reason"] or "")
    # cooldown remaining is close to the full cooldown, not zero
    assert snap["cooldown_remaining_seconds"] is not None
    assert snap["cooldown_remaining_seconds"] > cb.COOLDOWN_SECONDS * 0.9


# ─────────────────────────────────────────────
#  Window pruning
# ─────────────────────────────────────────────
def test_old_failures_are_pruned_from_window(monkeypatch):
    """A failure older than WINDOW_SECONDS must not count."""
    base = time.time()
    clock, advance = _fake_time(base)
    monkeypatch.setattr(cb.time, "time", clock)

    # 4 old failures — beyond the window — then 4 fresh
    # (below threshold on their own). Should stay CLOSED.
    for _ in range(4):
        cb.record_failure(reason="old")
    advance(cb.WINDOW_SECONDS + 5)
    for _ in range(cb.ERROR_THRESHOLD - 1):
        cb.record_failure(reason="fresh")

    snap = cb.snapshot()
    assert snap["state"] == "closed"
    assert snap["failures_in_window"] == cb.ERROR_THRESHOLD - 1


# ─────────────────────────────────────────────
#  Manual reset
# ─────────────────────────────────────────────
def test_reset_forces_closed():
    for _ in range(cb.ERROR_THRESHOLD):
        cb.record_failure(reason="err")
    assert cb.snapshot()["state"] == "open"

    snap = cb.reset()
    assert snap["state"] == "closed"
    assert snap["failures_in_window"] == 0
    assert snap["last_reason"] == "manual_reset"


# ─────────────────────────────────────────────
#  Integration: fetch_broker_quote respects the breaker
# ─────────────────────────────────────────────
def test_fetch_broker_quote_short_circuits_when_open(monkeypatch):
    """When the breaker is OPEN, ``fetch_broker_quote`` returns
    ``None`` without touching any provider — no HTTP, no timeout."""
    for _ in range(cb.ERROR_THRESHOLD):
        cb.record_failure(reason="err")
    assert cb.snapshot()["state"] == "open"

    import services.market_data_pool as pool_mod
    from services.provider_pool import ProviderPool

    monkeypatch.setattr(pool_mod, "market_pool", ProviderPool([
        {"name": "public-primary", "provider": "public",
         "api_key": "__from_db__", "priority": 1},
    ], name="TEST_MARKET_POOL"))

    called = []
    async def _would_be_called(_provider, _symbol):
        called.append(1)
        return {"price": 100.0}
    monkeypatch.setattr(pool_mod, "_dispatch_quote", _would_be_called)

    import asyncio
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(pool_mod.fetch_broker_quote("AAPL"))
    finally:
        loop.close()

    assert result is None
    assert called == [], "breaker should have prevented the HTTP call"


def test_fetch_broker_quote_records_failure_on_provider_error(monkeypatch):
    """A raising provider counts as a broker failure — the breaker
    trips after ``ERROR_THRESHOLD`` of them."""
    import services.market_data_pool as pool_mod
    from services.provider_pool import ProviderPool

    monkeypatch.setattr(pool_mod, "market_pool", ProviderPool([
        {"name": "public-primary", "provider": "public",
         "api_key": "__from_db__", "priority": 1},
    ], name="TEST_MARKET_POOL"))

    async def _always_raise(_provider, _symbol):
        raise RuntimeError("public down")
    monkeypatch.setattr(pool_mod, "_dispatch_quote", _always_raise)

    import asyncio
    loop = asyncio.new_event_loop()
    try:
        for _ in range(cb.ERROR_THRESHOLD):
            loop.run_until_complete(pool_mod.fetch_broker_quote("AAPL"))
    finally:
        loop.close()

    snap = cb.snapshot()
    assert snap["state"] == "open"
    assert snap["failures_in_window"] >= cb.ERROR_THRESHOLD


def test_fetch_broker_quote_records_success_and_stays_closed(monkeypatch):
    """Successful provider dispatch records a success and leaves
    the breaker CLOSED."""
    import services.market_data_pool as pool_mod
    from services.provider_pool import ProviderPool

    monkeypatch.setattr(pool_mod, "market_pool", ProviderPool([
        {"name": "public-primary", "provider": "public",
         "api_key": "__from_db__", "priority": 1},
    ], name="TEST_MARKET_POOL"))

    async def _ok(_provider, symbol):
        return {"symbol": symbol, "price": 100.0, "fetched_at": time.time()}
    monkeypatch.setattr(pool_mod, "_dispatch_quote", _ok)

    import asyncio
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(pool_mod.fetch_broker_quote("AAPL"))
    finally:
        loop.close()

    assert result is not None
    snap = cb.snapshot()
    assert snap["state"] == "closed"
    assert snap["consecutive_successes"] >= 1


# ─────────────────────────────────────────────
#  Integration: fetch_execution_quote marks broker_degraded
# ─────────────────────────────────────────────
def test_execution_quote_reports_broker_degraded_when_circuit_open(monkeypatch):
    """When the breaker is OPEN, ``fetch_execution_quote`` still
    surfaces the vendor price for context but marks
    ``reason == "broker_degraded"`` so the operator can tell the
    difference from "no broker coverage for this symbol"."""
    for _ in range(cb.ERROR_THRESHOLD):
        cb.record_failure(reason="err")
    assert cb.snapshot()["state"] == "open"

    import services.market_data_pool as pool_mod
    async def _no_broker(_sym):
        return None
    async def _vendor(_sym):
        return {"price": 150.0, "fetched_at": time.time(),
                "source": "finnhub", "provider_name": "finnhub-backup"}
    monkeypatch.setattr(pool_mod, "fetch_broker_quote", _no_broker)
    monkeypatch.setattr(pool_mod, "fetch_vendor_quote", _vendor)

    from services import provider_policy
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        xq = loop.run_until_complete(provider_policy.fetch_execution_quote("AAPL"))
    finally:
        loop.close()

    assert xq.execution_allowed is False
    assert xq.reason == "broker_degraded"
    assert xq.vendor_price == 150.0


def test_execution_quote_still_says_no_broker_price_when_circuit_closed(monkeypatch):
    """CLOSED breaker + broker returns None (symbol not in coverage)
    must keep the old ``no_broker_price`` reason so we don't
    confuse "symbol not covered" with "broker outage"."""
    # explicit — breaker starts CLOSED via the fixture reset
    assert cb.snapshot()["state"] == "closed"

    import services.market_data_pool as pool_mod
    async def _no_broker(_sym):
        return None
    async def _vendor(_sym):
        return {"price": 150.0, "fetched_at": time.time(),
                "source": "finnhub", "provider_name": "finnhub-backup"}
    monkeypatch.setattr(pool_mod, "fetch_broker_quote", _no_broker)
    monkeypatch.setattr(pool_mod, "fetch_vendor_quote", _vendor)

    from services import provider_policy
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        xq = loop.run_until_complete(provider_policy.fetch_execution_quote("XYZ"))
    finally:
        loop.close()

    assert xq.execution_allowed is False
    assert xq.reason == "no_broker_price"
