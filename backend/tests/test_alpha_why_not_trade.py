"""Tests for the ``/why-not-trade`` diagnostic aggregator.

Covers the two data sources it joins:

* SQLite hot store (per-tick lifecycle rows for the rejection gates
  we care about — plus the pass events so the endpoint can report
  how many candidates made it through)
* Mongo ``alpha_outcomes`` (resolved rollup — read directly for the
  window; helper is tolerant of ``db=None``)

Every test drives the aggregator on a private hot-store SQLite file
so it never touches the production one.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from services import alpha_hot_store, alpha_why_not_trade


# ─────────────────────────────────────────────
#  Hot-store isolation
# ─────────────────────────────────────────────
@pytest.fixture
def _isolated_hot_store(tmp_path, monkeypatch):
    """Point ``alpha_hot_store`` at a per-test SQLite file."""
    db = tmp_path / f"why_not_trade_{uuid.uuid4().hex}.sqlite"
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(db))
    # Reset module-level path cache so ``_path()`` picks up the env var.
    monkeypatch.setattr(alpha_hot_store, "_DB_PATH", None)
    alpha_hot_store.init(str(db))
    yield db


def _write_event(
    setup_id: str,
    event: str,
    *,
    symbol: str = "",
    payload: dict | None = None,
    stage: str = "",
    seconds_ago: float = 0.0,
) -> None:
    """Insert a lifecycle row with a controllable timestamp.

    ``alpha_hot_store.record_event`` always stamps ``time.time_ns()``
    so tests that need older rows write directly.
    """
    ts_ns = int((time.time() - seconds_ago) * 1_000_000_000)
    # Best-effort — must not fail the test setup even if the module
    # has been reconfigured under us.
    with sqlite3.connect(alpha_hot_store._path(), timeout=5.0) as con:
        con.execute(
            "INSERT INTO lifecycle_events(setup_id, symbol, event, stage, payload, ts_ns) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (setup_id, symbol.upper(), event, stage,
             json.dumps(payload or {}, default=str), ts_ns),
        )
        con.commit()


def _run(coro):
    """Sync driver on a private loop — same pattern as
    ``test_provider_policy._run_gate``. Doesn't touch the thread's
    default loop, so downstream tests are unaffected.
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────
#  Empty-state defaults
# ─────────────────────────────────────────────
def test_empty_window_returns_stand_down_summary(_isolated_hot_store):
    """Nothing in the hot store → summary calls out the standdown."""
    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=300))
    assert result["totals"]["setups_detected"] == 0
    assert result["totals"]["broker_submitted"] == 0
    assert result["totals"]["total_rejections"] == 0
    assert "No Alpha activity" in result["summary"]
    # every gate must still be present in the response so the UI
    # doesn't have to defensively check for missing keys.
    for gate in alpha_why_not_trade.REJECTION_GATES:
        assert gate in result["rejections"]
        assert result["rejections"][gate]["count"] == 0


# ─────────────────────────────────────────────
#  Gate rollup basics
# ─────────────────────────────────────────────
def test_execution_quote_blocked_rows_roll_up_per_symbol_and_reason(_isolated_hot_store):
    """Two AAPL rejections + one MSFT rejection under
    ``execution_quote_blocked`` — count must be 3, symbols must
    de-duplicate to two, and sub-reasons must be counted."""
    _write_event("s-1", "execution_quote_blocked", symbol="AAPL",
                 payload={"reason": "broker_quote_stale (age=8s > 5s)"})
    _write_event("s-2", "execution_quote_blocked", symbol="AAPL",
                 payload={"reason": "broker_quote_stale (age=12s > 5s)"})
    _write_event("s-3", "execution_quote_blocked", symbol="MSFT",
                 payload={"reason": "data_conflict broker=150 vendor=152 drift=133bps"})

    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=300))
    slot = result["rejections"]["execution_quote_blocked"]
    assert slot["count"] == 3
    assert slot["symbols"] == ["AAPL", "MSFT"]  # sorted, de-duplicated
    # Only the leading token is kept so both stale variants roll together.
    assert slot["sub_reasons"]["broker_quote_stale"] == 2
    assert slot["sub_reasons"]["data_conflict"] == 1
    # Sample must include structured entries with the raw payload.
    assert len(slot["sample"]) >= 1
    assert slot["sample"][0]["symbol"] in {"AAPL", "MSFT"}
    assert "payload" in slot["sample"][0]


def test_sub_reason_falls_back_to_unspecified_when_missing(_isolated_hot_store):
    """A gate row without any ``reason`` field must roll up as
    ``unspecified`` — not raise, not be silently dropped."""
    _write_event("s-1", "intent_deduplicated", symbol="AAPL", payload={})
    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=300))
    slot = result["rejections"]["intent_deduplicated"]
    assert slot["count"] == 1
    assert slot["sub_reasons"] == {"unspecified": 1}


def test_sample_size_capped_per_gate(_isolated_hot_store):
    """20 rejections on the same gate → sample must be capped at
    the requested ``sample_per_gate`` (default 3)."""
    for i in range(20):
        _write_event(f"s-{i}", "executor_rejected", symbol=f"SYM{i}",
                     payload={"reason": "sizing_failed"})
    result = _run(alpha_why_not_trade.compile_why_not_trade(
        None, since_seconds=300, sample_per_gate=3,
    ))
    slot = result["rejections"]["executor_rejected"]
    assert slot["count"] == 20
    assert len(slot["sample"]) == 3


# ─────────────────────────────────────────────
#  Time window
# ─────────────────────────────────────────────
def test_events_outside_window_are_ignored(_isolated_hot_store):
    """A rejection recorded an hour ago must not appear when the
    window is 5 minutes."""
    _write_event("s-old", "execution_quote_blocked", symbol="AAPL",
                 payload={"reason": "broker_quote_stale"},
                 seconds_ago=3600)  # 1h ago
    _write_event("s-new", "execution_quote_blocked", symbol="MSFT",
                 payload={"reason": "broker_quote_stale"},
                 seconds_ago=10)     # 10s ago

    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=300))
    slot = result["rejections"]["execution_quote_blocked"]
    assert slot["count"] == 1
    assert slot["symbols"] == ["MSFT"]


def test_since_seconds_is_clamped_to_range(_isolated_hot_store):
    """A caller asking for 10 seconds is bumped to 60; a caller
    asking for a year is clamped to 604800 (7 days — the hot store
    retains 14 days, we want operators to see two full sessions
    without accidentally hammering SQLite with a year-long scan).
    Belt-and-suspenders for the endpoint's ``Query`` gate.
    """
    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=10))
    assert result["since_seconds"] == 60

    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=10_000_000))
    assert result["since_seconds"] == 604_800


# ─────────────────────────────────────────────
#  Totals & summary
# ─────────────────────────────────────────────
def test_totals_include_pass_events(_isolated_hot_store):
    """Pass events (execution_quote_confirmed, broker_submitted)
    are counted separately so the operator can tell "everything
    was blocked" from "some passed"."""
    _write_event("s-1", "setup_detected", symbol="AAPL")
    _write_event("s-1", "triggered", symbol="AAPL")
    _write_event("s-1", "intent_created", symbol="AAPL")
    _write_event("s-1", "execution_quote_confirmed", symbol="AAPL")
    _write_event("s-1", "broker_submitted", symbol="AAPL")

    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=300))
    assert result["totals"]["setups_detected"] == 1
    assert result["totals"]["triggers_fired"] == 1
    assert result["totals"]["intents_created"] == 1
    assert result["totals"]["execution_quote_confirmed"] == 1
    assert result["totals"]["broker_submitted"] == 1
    assert result["totals"]["total_rejections"] == 0
    # Summary must indicate the trade went through.
    assert "1 submitted" in result["summary"]


def test_summary_calls_out_worst_offending_gate(_isolated_hot_store):
    """When multiple gates fire, the summary must lead with the
    highest-count gate + its top sub-reason."""
    for i in range(5):
        _write_event(f"eqb-{i}", "execution_quote_blocked",
                     symbol=f"AAA{i}", payload={"reason": "broker_quote_stale"})
    for i in range(2):
        _write_event(f"lock-{i}", "exec_lock_conflict",
                     symbol=f"BBB{i}", payload={"reason": "held_by_scanner"})

    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=300))
    # execution_quote_blocked wins with 5 vs 2.
    assert "execution_quote_blocked" in result["summary"]
    assert "broker_quote_stale" in result["summary"]


# ─────────────────────────────────────────────
#  Regression: unknown-event rows don't blow up
# ─────────────────────────────────────────────
def test_unregistered_event_names_are_silently_ignored(_isolated_hot_store):
    """A future ``foo_bar`` event that no one registered as a gate
    must not crash the aggregator — it should just be filtered out
    by the SQL ``event IN (...)`` clause."""
    _write_event("s-1", "some_new_event_we_do_not_track", symbol="AAPL",
                 payload={"reason": "future"})
    _write_event("s-2", "intent_deduplicated", symbol="MSFT",
                 payload={"reason": "duplicate_fingerprint"})
    result = _run(alpha_why_not_trade.compile_why_not_trade(None, since_seconds=300))
    # Only the tracked gate rolled up.
    assert result["rejections"]["intent_deduplicated"]["count"] == 1


def test_registered_gates_cover_every_reject_reason_writer(_isolated_hot_store):
    """A guardrail so we don't add a new ``_record_observation``
    rejection event without registering it in ``REJECTION_GATES``.

    Scans ``services/alpha_day_trader.py`` for observation events
    whose name contains ``reject``, ``block``, ``dedup``, ``conflict``,
    or ``invalidat`` (the naming pattern we use for gates) and
    asserts every one is registered.
    """
    src = Path(__file__).resolve().parent.parent / "services" / "alpha_day_trader.py"
    text = src.read_text()
    import re
    events = set()
    for m in re.finditer(r'_record_observation\(\s*[a-zA-Z_.]+,\s*[a-zA-Z_.]+,\s*["\']([^"\']+)["\']', text):
        events.add(m.group(1))
    reject_shaped = {
        e for e in events
        if any(tag in e for tag in ("reject", "block", "dedup", "conflict", "invalidat"))
    }
    missing = reject_shaped - set(alpha_why_not_trade.REJECTION_GATES.keys())
    assert not missing, (
        f"New rejection-shaped event(s) in alpha_day_trader.py not "
        f"registered in REJECTION_GATES: {sorted(missing)}. "
        f"Add them to services/alpha_why_not_trade.REJECTION_GATES so "
        f"they surface in the diagnostic."
    )
