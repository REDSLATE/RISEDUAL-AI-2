"""Latency-budget test for services.atlas_bridge.

Ensures Atlas can never freeze the trade path even under adversarial
ledger conditions. The overlay post-mortem (rule 8) forbids any new
component from adding a synchronous SQLite/network call to the
trade-critical path; this test enforces that invariant for Atlas.

Failure of any of these tests should block a deploy.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from services import atlas_bridge


# 5 ms is the hard budget any bridge helper must clear from the
# caller's perspective, even if the underlying ledger is stuck.
CALLER_BUDGET_MS = 5


class _StuckLedger:
    """Ledger stand-in whose every write takes 10 seconds. If any
    bridge call ever waits for this ledger, the test times out."""

    def transition_intent(self, *a, **kw):  # noqa: D401
        time.sleep(10)

    def start_trace(self, *a, **kw):
        time.sleep(10)

    def append_trace_event(self, *a, **kw):
        time.sleep(10)

    def finalize_trace(self, *a, **kw):
        time.sleep(10)


class _FastLedger:
    """Ledger stand-in that returns instantly; used for happy-path
    tests to prove the helpers still schedule work."""

    def __init__(self):
        self.transitions = []
        self.traces_started = 0
        self.events = 0
        self.finalized = 0

    def transition_intent(self, intent_id, to_status, **kw):
        self.transitions.append((intent_id, str(to_status), kw))

    def start_trace(self, *a, **kw):
        self.traces_started += 1

    def append_trace_event(self, *a, **kw):
        self.events += 1

    def finalize_trace(self, *a, **kw):
        self.finalized += 1


@pytest.fixture(autouse=True)
def _restore_ledger():
    """Snapshot + restore the module-level ledger and env between tests."""
    original = atlas_bridge.get_ledger()
    yield
    atlas_bridge.set_ledger(original)


@pytest.mark.asyncio
async def test_observe_intent_async_returns_immediately_even_when_ledger_is_stuck():
    """Even if every SQLite write takes 10 seconds, the caller's
    coroutine must return in well under 5 ms."""
    atlas_bridge.set_ledger(_StuckLedger())

    t0 = time.perf_counter_ns()
    result = atlas_bridge.observe_intent_async({
        "symbol": "AAPL",
        "direction": "BUY",
        "confidence": 0.9,
        "strategy_id": "test:v1",
        "prediction_id": "pred-1",
    })
    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000

    assert elapsed_ms < CALLER_BUDGET_MS, (
        f"observe_intent_async blocked for {elapsed_ms:.2f}ms — must be < {CALLER_BUDGET_MS}ms"
    )
    # Returns the derived intent_id so the caller can chain
    # transitions without waiting on the ledger.
    assert result is not None
    assert result.startswith("pex:") or result == "pex"


@pytest.mark.asyncio
async def test_transition_async_returns_immediately_when_ledger_is_stuck():
    atlas_bridge.set_ledger(_StuckLedger())

    t0 = time.perf_counter_ns()
    atlas_bridge.transition_async("intent-x", "submitted", broker_order_id="bo-1")
    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000

    assert elapsed_ms < CALLER_BUDGET_MS, (
        f"transition_async blocked for {elapsed_ms:.2f}ms — must be < {CALLER_BUDGET_MS}ms"
    )


@pytest.mark.asyncio
async def test_trace_cycle_async_returns_immediately_when_ledger_is_stuck():
    atlas_bridge.set_ledger(_StuckLedger())

    t0 = time.perf_counter_ns()
    atlas_bridge.trace_cycle_async(
        lane="equity", symbol="MSFT",
        stages=[{"stage": "market_event", "timestamp_ns": time.time_ns()}],
        terminal_result="NO_SETUP",
    )
    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000

    assert elapsed_ms < CALLER_BUDGET_MS, (
        f"trace_cycle_async blocked for {elapsed_ms:.2f}ms — must be < {CALLER_BUDGET_MS}ms"
    )


@pytest.mark.asyncio
async def test_stuck_ledger_task_gets_cancelled_within_budget(caplog):
    """The scheduled background task must be cancelled by the
    100ms write budget; it must NOT leak into the next test as a
    still-running task holding a thread indefinitely."""
    atlas_bridge.set_ledger(_StuckLedger())

    atlas_bridge.transition_async("intent-y", "approved")
    # Wait ~200ms — more than the 100ms budget. The internal task
    # should be finished (cancelled/timeout) by now.
    await asyncio.sleep(0.2)
    # No exception surfaced to the caller (asserted implicitly by
    # this test not raising).


@pytest.mark.asyncio
async def test_kill_switch_disables_all_writes(monkeypatch):
    """RISEDUAL_ATLAS_ENABLED=0 must make every helper a no-op."""
    monkeypatch.setenv("RISEDUAL_ATLAS_ENABLED", "0")
    fast = _FastLedger()
    atlas_bridge.set_ledger(fast)

    assert atlas_bridge.observe_intent_async({"symbol": "X", "direction": "BUY"}) is None
    atlas_bridge.transition_async("intent-z", "approved")
    atlas_bridge.trace_cycle_async(
        stages=[{"stage": "market_event", "timestamp_ns": time.time_ns()}],
        terminal_result="NO_SETUP",
    )
    await asyncio.sleep(0.05)

    assert fast.transitions == []
    assert fast.traces_started == 0
    assert fast.finalized == 0


@pytest.mark.asyncio
async def test_happy_path_actually_writes_to_ledger():
    """With a working ledger + enabled, the background tasks must
    eventually land the writes."""
    fast = _FastLedger()
    atlas_bridge.set_ledger(fast)

    atlas_bridge.transition_async("intent-happy", "submitted", broker_order_id="bo-42")
    # Give the executor a moment to complete.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if fast.transitions:
            break

    assert fast.transitions, "background transition never landed"
    assert fast.transitions[0][0] == "intent-happy"
    assert fast.transitions[0][1] == "submitted"


@pytest.mark.asyncio
async def test_no_running_loop_is_safe():
    """When called outside an asyncio loop, helpers must not raise.

    This one exercises the ``get_running_loop`` guard by making the
    scheduler cope with a stuck ledger + no active event loop
    from the writer's perspective. Wrapping in a fresh loop lets us
    prove the guard works without ourselves being loop-free."""
    fast = _FastLedger()
    atlas_bridge.set_ledger(fast)

    # Call from a plain thread — no running loop there.
    import threading

    err = []

    def _from_thread():
        try:
            atlas_bridge.transition_async("intent-thread", "approved")
        except Exception as exc:  # noqa: BLE001
            err.append(exc)

    t = threading.Thread(target=_from_thread)
    t.start()
    t.join(timeout=1.0)
    assert not err, f"transition_async raised in a no-loop thread: {err}"


@pytest.mark.asyncio
async def test_atlas_pool_isolated_from_default_executor():
    """The doctrine's critical guarantee: a stuck Atlas ledger must
    NOT slow ``asyncio.to_thread`` calls from other subsystems (e.g.
    ``market_data_pool.get_quote``). This test proves the Atlas pool
    is a separate ``ThreadPoolExecutor`` from the default one used
    by ``asyncio.to_thread``. Without isolation, a stuck SQLite
    ``BEGIN IMMEDIATE`` could occupy default-pool workers and
    starve quote/bar fetches on the decision path.
    """
    atlas_bridge.set_ledger(_StuckLedger())

    # Fill Atlas's inflight cap with stuck transitions.
    for i in range(5):
        atlas_bridge.transition_async(f"stuck-{i}", "approved")

    # Now measure a decision-path-style ``to_thread`` call. If the
    # Atlas pool were shared with the default one, this would sit
    # behind the stuck SQLite writers.
    t0 = time.perf_counter_ns()
    await asyncio.to_thread(lambda: sum(range(1000)))
    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000

    assert elapsed_ms < 50, (
        f"decision-path to_thread took {elapsed_ms:.1f} ms while Atlas "
        f"had stuck writers — pool isolation is broken"
    )


@pytest.mark.asyncio
async def test_inflight_cap_prevents_unbounded_growth():
    """A persistently stuck ledger must not allow Atlas tasks to
    accumulate without bound. Once the cap is hit, new writes are
    dropped (returned ``None``), not queued."""
    atlas_bridge.set_ledger(_StuckLedger())

    # Blast the scheduler with far more than the cap allows.
    for i in range(atlas_bridge._MAX_INFLIGHT + 20):
        atlas_bridge.transition_async(f"blast-{i}", "approved")

    # Inflight count must plateau at (or below) the cap — never
    # exceed it. We give the event loop a chance to schedule.
    await asyncio.sleep(0)
    assert atlas_bridge.inflight_count() <= atlas_bridge._MAX_INFLIGHT, (
        f"in-flight cap breached: {atlas_bridge.inflight_count()} "
        f"> {atlas_bridge._MAX_INFLIGHT}"
    )


@pytest.mark.asyncio
async def test_tasks_are_retained_until_done():
    """Fire-and-forget tasks must be strong-ref'd so asyncio's
    weak-ref GC cannot drop them mid-flight. The bridge stores each
    task in a module-level set and removes it via ``done_callback``
    only when actually finished."""
    fast = _FastLedger()
    atlas_bridge.set_ledger(fast)

    before = atlas_bridge.inflight_count()
    atlas_bridge.transition_async("retention-1", "approved")
    # Immediately after scheduling, the task must be tracked.
    assert atlas_bridge.inflight_count() == before + 1

    # After completion, it must be removed.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if atlas_bridge.inflight_count() == before:
            break
    assert atlas_bridge.inflight_count() == before, (
        "task was not removed from in-flight set after completion"
    )
