"""Tests for alpha_top10_state — in-process watchlist.

The state module is intentionally tiny; these tests lock down the
guarantees the ``alpha_top10_stream`` service depends on:

* Duplicate symbols are collapsed
* Order matches insertion (which is score-desc from the day trader)
* Truncation at ``MAX_TOP_N``
* Empty/malformed entries are silently dropped
* ``get_top10`` returns a defensive copy
"""
from __future__ import annotations

import pytest

from services import alpha_top10_state


@pytest.fixture(autouse=True)
def _reset_state():
    alpha_top10_state.clear()
    yield
    alpha_top10_state.clear()


def test_set_top10_stores_symbols_in_order():
    entries = [
        {"symbol": "ANET", "score": 0.82},
        {"symbol": "ADBE", "score": 0.71},
        {"symbol": "NVDA", "score": 0.69},
    ]
    alpha_top10_state.set_top10(entries, source_tick="5min")
    snap = alpha_top10_state.get_top10()
    assert snap["symbols"] == ["ANET", "ADBE", "NVDA"]
    assert snap["source_tick"] == "5min"
    assert snap["refreshed_at"] is not None


def test_set_top10_truncates_to_max_top_n():
    entries = [
        {"symbol": f"SYM{i}", "score": 1.0 - i * 0.01}
        for i in range(alpha_top10_state.MAX_TOP_N + 5)
    ]
    alpha_top10_state.set_top10(entries)
    snap = alpha_top10_state.get_top10()
    assert len(snap["symbols"]) == alpha_top10_state.MAX_TOP_N


def test_set_top10_dedupes_symbols():
    entries = [
        {"symbol": "ANET", "score": 0.82},
        {"symbol": "anet", "score": 0.60},   # lowercase → same symbol
        {"symbol": "ADBE", "score": 0.71},
    ]
    alpha_top10_state.set_top10(entries)
    snap = alpha_top10_state.get_top10()
    assert snap["symbols"] == ["ANET", "ADBE"]


def test_set_top10_drops_blank_and_missing_symbols():
    entries = [
        {"symbol": "", "score": 0.9},
        {"symbol": None, "score": 0.9},
        {"score": 0.9},
        {"symbol": "ADBE", "score": 0.71},
    ]
    alpha_top10_state.set_top10(entries)
    snap = alpha_top10_state.get_top10()
    assert snap["symbols"] == ["ADBE"]


def test_get_top10_returns_defensive_copy():
    alpha_top10_state.set_top10([{"symbol": "ADBE", "score": 0.71}])
    snap = alpha_top10_state.get_top10()
    snap["symbols"].append("MUTATED")
    snap["entries"].append({"symbol": "MUTATED"})
    fresh = alpha_top10_state.get_top10()
    assert fresh["symbols"] == ["ADBE"]
    assert len(fresh["entries"]) == 1


def test_set_top10_replaces_prior_watchlist():
    alpha_top10_state.set_top10([{"symbol": "OLD", "score": 0.5}])
    alpha_top10_state.set_top10([{"symbol": "NEW", "score": 0.6}])
    snap = alpha_top10_state.get_top10()
    assert snap["symbols"] == ["NEW"]


def test_clear_resets_state():
    alpha_top10_state.set_top10([{"symbol": "ADBE", "score": 0.71}])
    alpha_top10_state.clear()
    snap = alpha_top10_state.get_top10()
    assert snap["symbols"] == []
    assert snap["refreshed_at"] is None
