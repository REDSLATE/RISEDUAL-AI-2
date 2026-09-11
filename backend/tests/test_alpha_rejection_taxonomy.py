"""Rejection taxonomy — separates *protection* (Alpha correctly
refusing), *infrastructure* (plumbing broken), *session* (market
state), *concurrency* (Alpha's own lock), *unknown* (missing
classification, forces maintenance).

Operator principle under test: mixing these classes hides both.
The taxonomy must:

* Route every known reason to its exact class.
* Route unknown reasons to ``unknown`` (never silently misclassify).
* Compute a health hint that flags infrastructure dominance loudly.
"""
from __future__ import annotations

import pytest

from services.alpha_rejection_taxonomy import classify, summarize, health_hint


# ── Protection guards ─────────────────────────────────────────────

@pytest.mark.parametrize("reason", [
    "chasing_filter", "luld_roadguard", "symbol_cooldown",
    "confidence_floor", "not_in_allowlist", "non_directional",
    "empty_symbol", "insufficient_buying_power", "short_signal_only",
    "hw_kill_switch_tripped", "dup_open_row", "sell_no_position",
    "broker_watchdog_frozen", "qty_zero", "danger_pause_mode",
    "invalidated", "invalidated_before_trigger", "execute_switch_off",
])
def test_protection_reasons_classify_correctly(reason):
    assert classify(reason) == "protection"


# ── Infrastructure failures ───────────────────────────────────────

@pytest.mark.parametrize("reason", [
    "execution_quote_blocked", "no_broker_price", "broker_degraded",
    "no_mark_price", "no_broker_creds", "client_init_failed",
    "place_order_exception", "broker_empty_response",
    "moomoo_not_execution_ready", "moomoo_health_probe_failed",
    "orphaned:no_terminal_event",
])
def test_infrastructure_reasons_classify_correctly(reason):
    assert classify(reason) == "infrastructure"


def test_orphaned_prefix_match():
    """Any ``orphaned:*`` variant is infrastructure — future subcodes
    should classify without table maintenance."""
    assert classify("orphaned:some-new-code") == "infrastructure"
    assert classify("orphaned:heartbeat_missing") == "infrastructure"


# ── Session state ─────────────────────────────────────────────────

@pytest.mark.parametrize("reason", ["market_closed", "live_exec_disabled"])
def test_session_reasons_classify_correctly(reason):
    assert classify(reason) == "session"


# ── Concurrency ──────────────────────────────────────────────────

@pytest.mark.parametrize("reason", ["exec_lock_conflict", "intent_deduplicated"])
def test_concurrency_reasons_classify_correctly(reason):
    assert classify(reason) == "concurrency"


# ── Unknown must not silently misclassify ─────────────────────────

def test_unknown_reason_stays_unknown():
    assert classify("some_new_reason_not_yet_in_table") == "unknown"


def test_empty_reason_is_unknown():
    assert classify("") == "unknown"
    assert classify(None) == "unknown"


def test_reason_matching_is_case_insensitive_and_trimmed():
    assert classify("  Chasing_Filter  ") == "protection"
    assert classify("PLACE_ORDER_EXCEPTION") == "infrastructure"


# ── Summarize folds counts into classes ──────────────────────────

def test_summarize_folds_counts_by_class():
    counts = {
        "chasing_filter": 19,        # protection
        "market_closed": 17,         # session
        "execution_quote_blocked": 7, # infrastructure
        "exec_lock_conflict": 2,     # concurrency
        "mystery_reason": 3,         # unknown
    }
    tax = summarize(counts)
    assert tax["protection"]["total"] == 19
    assert tax["session"]["total"] == 17
    assert tax["infrastructure"]["total"] == 7
    assert tax["concurrency"]["total"] == 2
    assert tax["unknown"]["total"] == 3
    assert tax["protection"]["reasons"] == {"chasing_filter": 19}


def test_summarize_ignores_zero_and_negative_counts():
    tax = summarize({"chasing_filter": 0, "no_mark_price": -1, "market_closed": 5})
    assert tax["protection"]["total"] == 0
    assert tax["infrastructure"]["total"] == 0
    assert tax["session"]["total"] == 5


def test_summarize_handles_bad_count_types():
    tax = summarize({"chasing_filter": "not-a-number"})
    assert tax["protection"]["total"] == 0


# ── Health hint interprets the taxonomy ──────────────────────────

def test_hint_alerts_when_infrastructure_dominates():
    tax = summarize({
        "place_order_exception": 5, "no_mark_price": 3, "chasing_filter": 2,
    })
    hint = health_hint(tax)
    assert hint["tone"] == "alert"
    assert "Infrastructure" in hint["message"]


def test_hint_ok_when_protection_dominates():
    tax = summarize({"chasing_filter": 19, "no_mark_price": 1})
    hint = health_hint(tax)
    assert hint["tone"] == "ok"
    assert "Protection" in hint["message"]


def test_hint_neutral_when_session_dominates():
    tax = summarize({"market_closed": 17, "chasing_filter": 2})
    hint = health_hint(tax)
    assert hint["tone"] == "neutral"


def test_hint_warns_when_unknown_dominates():
    tax = summarize({"new_reason_a": 10, "new_reason_b": 5})
    hint = health_hint(tax)
    assert hint["tone"] == "warn"


def test_hint_neutral_when_empty():
    tax = summarize({})
    hint = health_hint(tax)
    assert hint["tone"] == "neutral"
    assert "No rejections" in hint["message"]


def test_hint_prioritizes_infrastructure_tied_with_protection():
    """When infrastructure and protection tie, we err on the side of
    alerting — infra is the one you must NOT ignore."""
    tax = summarize({"chasing_filter": 5, "no_mark_price": 5})
    hint = health_hint(tax)
    assert hint["tone"] == "alert"
