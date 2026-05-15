"""Pytest coverage for the 2026-05-15 Tier 3 digest diagnostic fields:

  * Per-gate ``movers`` panel — shows which gate(s) contributed to a
    score swing instead of just reporting the headline delta.
  * Calendar context — surfaces window_days + first/last trade so
    "Live days N/30" reads as a sparsity hint, not just a raw count.
  * Stale-prior warning — when the most recent stored snapshot is
    more than 1 day old, the digest annotates the comparison so the
    operator knows the "▼ -X" isn't a literal 24-hour change.
"""
from __future__ import annotations

import pytest

from services.tier3_readiness_digest import (
    _compute_gate_movers,
    _format_calendar_context,
    _format_gate_movers_block,
)


@pytest.fixture
def base_stats():
    return {
        "days": 6,
        "total_trades": 868,
        "high_conf_trades": 40,
        "high_conf_win_rate": 0.70,
        "avg_confidence": 75.0,
        "strong_miss_rate": 0.018,
        "overall_win_rate": 0.85,
        "last_7d_win_rate": 0.85,
        "clamp_total": 0,
    }


def test_movers_returns_empty_without_prior(base_stats):
    movers = _compute_gate_movers(current_stats=base_stats, prior_stats=None)
    assert movers == []


def test_movers_returns_empty_on_no_change(base_stats):
    movers = _compute_gate_movers(
        current_stats=base_stats, prior_stats=dict(base_stats),
    )
    assert movers == [], "no-change snapshot must report no movers"


def test_movers_captures_high_conf_collapse(base_stats):
    """Regression guard for the user-reported -25 drop: the high-conf
    gate's ``earned_pts`` swing must surface as the top mover, not be
    silently hidden behind a bare headline delta."""
    prior = dict(base_stats, high_conf_win_rate=1.0, high_conf_trades=32)
    current = dict(base_stats, high_conf_win_rate=0.0, high_conf_trades=40)
    movers = _compute_gate_movers(current_stats=current, prior_stats=prior)
    assert movers, "high-conf collapse must produce a mover entry"
    top = movers[0]
    assert top["key"] == "high_conf_accuracy"
    # Composite formula is `wr * 25` — full swing is exactly 25 pts.
    assert top["delta_pts"] == pytest.approx(-25.0, abs=0.01)
    assert top["prior_pts"] == pytest.approx(25.0)
    assert top["current_pts"] == pytest.approx(0.0)


def test_movers_sorted_by_absolute_magnitude(base_stats):
    """When multiple gates move, the email should lead with the
    biggest mover so the operator's eye lands on the cause."""
    prior = dict(base_stats, high_conf_win_rate=0.50, days=4, total_trades=400)
    current = dict(base_stats, high_conf_win_rate=0.70, days=5, total_trades=420)
    movers = _compute_gate_movers(current_stats=current, prior_stats=prior)
    # high_conf swung 25 * 0.20 = +5.0; exposure +0.67 pts. high_conf
    # must rank first.
    assert movers[0]["key"] == "high_conf_accuracy"
    assert movers[0]["delta_pts"] > 0


def test_movers_filters_micro_noise(base_stats):
    """A ±0.49pt swing isn't worth the operator's attention. Anything
    below the half-point floor must be dropped."""
    prior = dict(base_stats, days=6)
    # days unchanged → exposure_pts unchanged at 4.0 pts; all gates
    # match. No mover should be emitted.
    movers = _compute_gate_movers(current_stats=base_stats, prior_stats=prior)
    assert movers == []


def test_calendar_context_renders_first_last_window():
    stats = {
        "days": 6,
        "first_trade_at": "2026-04-16T12:00:00+00:00",
        "last_trade_at": "2026-05-14T08:30:00+00:00",
        "window_days": 29,
    }
    html = _format_calendar_context(stats)
    assert "6/30 across 29-day calendar window" in html
    assert "2026-04-16" in html
    assert "2026-05-14" in html


def test_calendar_context_sparse_warning():
    """6 days out of a 29-day window = 21 % density — sparse."""
    stats = {
        "days": 6,
        "first_trade_at": "2026-04-16T12:00:00+00:00",
        "last_trade_at": "2026-05-14T08:30:00+00:00",
        "window_days": 29,
    }
    html = _format_calendar_context(stats)
    assert "sparse" in html.lower()


def test_calendar_context_intermittent_warning():
    """50 % density should flag intermittent (gap days don't count)."""
    stats = {
        "days": 10,
        "first_trade_at": "2026-04-16T12:00:00+00:00",
        "last_trade_at": "2026-05-04T08:30:00+00:00",
        "window_days": 19,
    }
    html = _format_calendar_context(stats)
    assert "intermittent" in html.lower()


def test_calendar_context_returns_empty_without_history():
    assert _format_calendar_context({"days": 0}) == ""
    assert _format_calendar_context(
        {"days": 5, "first_trade_at": None, "last_trade_at": None}
    ) == ""


def test_calendar_context_no_warning_when_target_met():
    """Once Tier 3 days target is met, sparsity warning is moot."""
    stats = {
        "days": 30,
        "first_trade_at": "2026-03-01T00:00:00+00:00",
        "last_trade_at": "2026-05-14T00:00:00+00:00",
        "window_days": 75,
    }
    html = _format_calendar_context(stats)
    assert "sparse" not in html.lower()
    assert "intermittent" not in html.lower()


def test_movers_block_empty_when_no_movers():
    assert _format_gate_movers_block([], delta_days=1) == ""


def test_movers_block_renders_top_5_only():
    movers = [
        {"key": f"k{i}", "label": f"Gate {i}",
         "prior_pts": 0, "current_pts": float(i),
         "delta_pts": float(i),
         "current_hint": "", "prior_hint": ""}
        for i in range(10, 0, -1)  # 10..1
    ]
    html = _format_gate_movers_block(movers, delta_days=1)
    # Top 5 only
    assert html.count("<tr>") == 5
    assert "Gate 10" in html
    assert "Gate 6" in html
    # Gate 5..1 truncated
    assert "Gate 5" not in html


def test_movers_block_surfaces_stale_prior_warning():
    movers = [{
        "key": "k", "label": "Gate", "prior_pts": 10.0, "current_pts": 0.0,
        "delta_pts": -10.0, "current_hint": "", "prior_hint": "",
    }]
    html = _format_gate_movers_block(movers, delta_days=4)
    assert "4 days ago" in html
    assert "spans" in html.lower()


def test_movers_block_no_warning_when_delta_one_day():
    movers = [{
        "key": "k", "label": "Gate", "prior_pts": 10.0, "current_pts": 0.0,
        "delta_pts": -10.0, "current_hint": "", "prior_hint": "",
    }]
    html = _format_gate_movers_block(movers, delta_days=1)
    assert "days ago" not in html
