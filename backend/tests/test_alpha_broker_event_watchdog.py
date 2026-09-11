"""Broker order-event watchdog — normalized event contract, freeze-on-stale
semantics, reconciliation transitions, and duplicate-submit guard.

Design guarantees under test:

* SUBMITTED → ACK-tier event within window cancels the timer cleanly.
* SUBMITTED → no event within window → BROKER_EVENT_STALE + frozen.
* Frozen (broker, symbol, account) blocks resubmit at ``is_frozen``.
* Reconciliation outcomes route to normalized states, NEVER assume failure.
* Reconciler exception → BROKER_STATE_UNKNOWN (freeze retained).
* Operator can manually clear a frozen entry.
"""
from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

# Use a per-test SQLite so entries from other suites don't leak in.
import tempfile

from services import alpha_broker_event_watchdog as watchdog
from services.alpha_broker_event_watchdog import Event, WatchdogEntry


@pytest.fixture(autouse=True)
def _short_stale(monkeypatch, tmp_path):
    # 100 ms window keeps tests fast + deterministic.
    monkeypatch.setenv("BROKER_EVENT_STALE_SECONDS", "0.1")
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(tmp_path / "hot_store.sqlite"))
    from services import alpha_hot_store
    alpha_hot_store._DB_PATH = str(tmp_path / "hot_store.sqlite")
    watchdog._reset_for_tests()
    yield
    watchdog._reset_for_tests()


# ── Happy path — ACK within window cancels the timer ────────────────

def test_ack_within_window_cancels_stale_timer():
    watchdog.register_submission(
        broker="public", client_order_id="coid-1",
        symbol="AAPL", account_id="acc-1",
    )
    watchdog.record_event(
        broker="public", client_order_id="coid-1",
        event=Event.ACKNOWLEDGED, broker_order_id="bo-1",
    )
    # Wait longer than the stale window; the timer should NOT fire since it was cancelled.
    time.sleep(0.25)
    entry = watchdog.get_entry("public", "coid-1")
    assert entry is not None
    assert entry.state == Event.ACKNOWLEDGED
    assert entry.frozen is False
    assert entry.broker_order_id == "bo-1"


def test_filled_event_terminal_and_unfrozen():
    watchdog.register_submission(
        broker="moomoo", client_order_id="coid-2",
        symbol="MSFT", account_id="acc-2",
    )
    watchdog.record_event(
        broker="moomoo", client_order_id="coid-2",
        event=Event.FILLED, broker_order_id="mm-99",
    )
    time.sleep(0.25)
    entry = watchdog.get_entry("moomoo", "coid-2")
    assert entry.state == Event.FILLED
    assert entry.frozen is False


# ── Stale path — no event within window → freeze + reconcile ───────

def test_stale_freezes_and_calls_reconciler():
    calls: list[WatchdogEntry] = []

    def _rec(entry: WatchdogEntry) -> dict:
        calls.append(entry)
        return {"outcome": "OPEN", "broker_order_id": "reco-1", "detail": "still working"}

    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="public", client_order_id="coid-stale-1",
        symbol="NVDA", account_id="acc-3",
    )
    time.sleep(0.35)
    entry = watchdog.get_entry("public", "coid-stale-1")
    assert entry is not None
    # After a stale timeout followed by OPEN reconciliation, we're back to ACK-tier
    # (still working) — but the intent was frozen during reconciliation.
    assert entry.state == Event.ACKNOWLEDGED
    assert entry.frozen is False
    assert entry.broker_order_id == "reco-1"
    assert len(calls) == 1


def test_stale_with_filled_reconciliation_transitions_to_filled():
    def _rec(entry):
        return {"outcome": "FILLED", "broker_order_id": "bo-fill", "detail": "post-hoc fill"}
    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="public", client_order_id="coid-fill",
        symbol="NVDA", account_id="acc-3",
    )
    time.sleep(0.35)
    entry = watchdog.get_entry("public", "coid-fill")
    assert entry.state == Event.FILLED
    assert entry.frozen is False
    assert entry.broker_order_id == "bo-fill"


def test_stale_with_absent_reconciliation_releases_intent():
    def _rec(entry):
        return {"outcome": "ABSENT", "detail": "broker never got it"}
    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="public", client_order_id="coid-absent",
        symbol="TSLA", account_id="acc-4",
    )
    time.sleep(0.35)
    entry = watchdog.get_entry("public", "coid-absent")
    assert entry.state == "BROKER_ABSENT"
    assert entry.frozen is False


def test_stale_with_unreachable_reconciliation_stays_unknown():
    def _rec(entry):
        return {"outcome": "UNREACHABLE", "detail": "http timeout"}
    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="public", client_order_id="coid-unk",
        symbol="AMD", account_id="acc-5",
    )
    time.sleep(0.35)
    entry = watchdog.get_entry("public", "coid-unk")
    assert entry.state == Event.BROKER_STATE_UNKNOWN
    assert entry.frozen is True


def test_reconciler_exception_freezes_as_unknown():
    def _rec(entry):
        raise RuntimeError("boom")
    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="public", client_order_id="coid-exc",
        symbol="AAPL", account_id="acc-6",
    )
    time.sleep(0.35)
    entry = watchdog.get_entry("public", "coid-exc")
    assert entry.state == Event.BROKER_STATE_UNKNOWN
    assert entry.frozen is True
    assert "reconciler_raised" in (entry.reason or "")


def test_stale_without_registered_reconciler_freezes_unknown():
    watchdog.register_submission(
        broker="public", client_order_id="coid-no-rec",
        symbol="AAPL", account_id="acc-7",
    )
    time.sleep(0.35)
    entry = watchdog.get_entry("public", "coid-no-rec")
    assert entry.state == Event.BROKER_STATE_UNKNOWN
    assert entry.frozen is True


# ── Freeze-based duplicate-submit guard ─────────────────────────────

def test_is_frozen_blocks_duplicate_symbol_account_pair():
    def _rec(entry):
        return {"outcome": "UNREACHABLE", "detail": "broker down"}
    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="public", client_order_id="coid-lock-1",
        symbol="NVDA", account_id="acc-x",
    )
    time.sleep(0.35)
    # NVDA/acc-x is now frozen
    assert watchdog.is_frozen("public", "NVDA", "acc-x") is True
    # Different account is NOT frozen
    assert watchdog.is_frozen("public", "NVDA", "acc-y") is False
    # Different symbol on same account is NOT frozen
    assert watchdog.is_frozen("public", "AAPL", "acc-x") is False
    # Different broker on same symbol/account is NOT frozen
    assert watchdog.is_frozen("moomoo", "NVDA", "acc-x") is False


def test_is_frozen_is_case_insensitive_symbol_broker():
    def _rec(entry):
        return {"outcome": "UNREACHABLE"}
    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="Public", client_order_id="coid-case",
        symbol="nvda", account_id="acc-z",
    )
    time.sleep(0.35)
    # register normalizes symbol → NVDA, broker → public
    assert watchdog.is_frozen("public", "NVDA", "acc-z") is True
    assert watchdog.is_frozen("PUBLIC", "nvda", "acc-z") is True


def test_clear_frozen_unlocks_symbol():
    def _rec(entry):
        return {"outcome": "UNREACHABLE"}
    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="public", client_order_id="coid-clear",
        symbol="AAPL", account_id="acc-c",
    )
    time.sleep(0.35)
    assert watchdog.is_frozen("public", "AAPL", "acc-c") is True
    assert watchdog.clear_frozen("public", "coid-clear") is True
    assert watchdog.is_frozen("public", "AAPL", "acc-c") is False


def test_clear_frozen_returns_false_when_no_such_entry():
    assert watchdog.clear_frozen("public", "nonexistent") is False


# ── Normalized event contract ───────────────────────────────────────

def test_terminal_events_unfreeze_regardless_of_prior_state():
    def _rec(entry):
        return {"outcome": "UNREACHABLE"}
    watchdog.register_reconciler("public", _rec)
    watchdog.register_submission(
        broker="public", client_order_id="coid-term",
        symbol="AAPL", account_id="acc-t",
    )
    time.sleep(0.35)
    assert watchdog.get_entry("public", "coid-term").frozen is True
    # Later manual/observed terminal event
    watchdog.record_event(
        broker="public", client_order_id="coid-term",
        event=Event.FILLED,
    )
    entry = watchdog.get_entry("public", "coid-term")
    assert entry.state == Event.FILLED
    assert entry.frozen is False


def test_record_event_on_unknown_key_is_noop():
    result = watchdog.record_event(
        broker="public", client_order_id="never-registered",
        event=Event.FILLED,
    )
    assert result is None


def test_list_entries_only_frozen_filter():
    def _rec(entry):
        return {"outcome": "UNREACHABLE"}
    watchdog.register_reconciler("public", _rec)
    # One will freeze, one will ACK cleanly.
    watchdog.register_submission(
        broker="public", client_order_id="coid-free-1",
        symbol="FOO", account_id="acc-1",
    )
    watchdog.record_event(
        broker="public", client_order_id="coid-free-1", event=Event.ACKNOWLEDGED,
    )
    watchdog.register_submission(
        broker="public", client_order_id="coid-frozen-1",
        symbol="BAR", account_id="acc-2",
    )
    time.sleep(0.35)
    all_entries = watchdog.list_entries(only_frozen=False)
    frozen_entries = watchdog.list_entries(only_frozen=True)
    assert len(all_entries) == 2
    assert len(frozen_entries) == 1
    assert frozen_entries[0]["symbol"] == "BAR"


# ── Race conditions ─────────────────────────────────────────────────

def test_ack_between_timer_fire_and_callback_does_not_clobber():
    """If an ACK slips in between the timer callback grabbing the lock
    and the state transition, we must NOT clobber the terminal state."""
    # We simulate this by pre-recording an ACK, then invoking the stale
    # callback directly. The callback should be a no-op.
    watchdog.register_submission(
        broker="public", client_order_id="coid-race",
        symbol="AAPL", account_id="acc-race",
    )
    watchdog.record_event(
        broker="public", client_order_id="coid-race", event=Event.FILLED,
    )
    watchdog._on_stale(("public", "coid-race"))
    entry = watchdog.get_entry("public", "coid-race")
    assert entry.state == Event.FILLED
    assert entry.frozen is False
