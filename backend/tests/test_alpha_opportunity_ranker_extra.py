"""Extra safety cases for the Discernment-patch ranker.

The patch ships three baseline tests; this file locks the specific
properties the operator called out in the P1-B Discernment
integration doctrine:

  * WATCH / REJECT / HOLD candidates NEVER compete for capital
    (already covered — re-asserted here with all three enum values
    in a single call so a regression can only manifest as a MULTI-item
    filter break, not a single-value slip).
  * Chase-risk penalty demotes chasing longs below cleaner longs.
  * Execution-risk penalty demotes stale/degraded quotes below fresh.
  * A near-tie long vs short: the borrow-cost penalty is the ONLY
    knob that changes the ordering — proving borrow cost carries its
    own weight independently of the other penalties.
  * A high-discernment REJECT candidate never leaks into the ranked
    output even when it would have out-scored every ACTIONABLE.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.alpha_opportunity_ranker import (
    compute_opportunity_score,
    rank_actionable,
)


@dataclass
class _S:
    symbol: str
    direction: str
    discernment_score: float
    edge_score: float = 0.0
    actionability: str = "ACTIONABLE"
    confidence: float = 0.0  # fallback path in scorer


# ── WATCH / REJECT / HOLD never compete ─────────────────────────────

def test_all_non_actionable_filtered_together():
    candidates = [
        _S("W1", "BUY", 0.99, actionability="WATCH"),
        _S("R1", "SELL_SHORT", 0.99, actionability="REJECT"),
        _S("H1", "HOLD", 0.99),
        _S("A1", "BUY", 0.50),
    ]
    out = rank_actionable(candidates)
    symbols = [x.signal.symbol for x in out]
    assert symbols == ["A1"], (
        "Only A1 is ACTIONABLE + directional; W1/R1/H1 must be filtered."
    )


def test_high_scoring_reject_cannot_leak():
    """A REJECT candidate at max discernment must not out-rank an
    ACTIONABLE at low discernment — filtering happens BEFORE scoring."""
    out = rank_actionable([
        _S("PHANTOM", "BUY", 1.0, actionability="REJECT"),
        _S("REAL", "BUY", 0.30),
    ])
    assert [x.signal.symbol for x in out] == ["REAL"]


# ── Chase-risk demotes chasing longs ────────────────────────────────

def test_chase_risk_demotes_late_entry():
    """Two BUYs with identical discernment; the one flagged as
    chasing (chase_risk=1.0) must rank BELOW the clean one."""
    clean = _S("CLEAN", "BUY", 0.75)
    chasing = _S("CHASING", "BUY", 0.75)
    ranked = rank_actionable(
        [clean, chasing],
        context_by_symbol={
            "CLEAN":   {"chase_risk": 0.0},
            "CHASING": {"chase_risk": 1.0},
        },
    )
    assert [x.signal.symbol for x in ranked] == ["CLEAN", "CHASING"]


# ── Execution-risk demotes stale quotes ─────────────────────────────

def test_execution_risk_demotes_stale_quote():
    fresh = _S("FRESH", "BUY", 0.70)
    stale = _S("STALE", "BUY", 0.70)
    ranked = rank_actionable(
        [fresh, stale],
        context_by_symbol={
            "FRESH": {"execution_risk": 0.0},
            "STALE": {"execution_risk": 1.0},
        },
    )
    assert [x.signal.symbol for x in ranked] == ["FRESH", "STALE"]


# ── Borrow-cost is INDEPENDENT of chase/execution risk ─────────────

def test_borrow_cost_alone_flips_short_below_long():
    """The two candidates have identical execution + chase risk (0/0).
    Only borrow_cost_risk differs. The short with high borrow cost
    must demote below the long — proving borrow cost is not a
    duplicate of the execution-risk penalty."""
    short = _S("SHORT_HTB", "SELL_SHORT", 0.90)
    long = _S("LONG_CLEAN", "BUY", 0.84)
    ranked = rank_actionable(
        [short, long],
        context_by_symbol={
            "SHORT_HTB": {"borrow_cost_risk": 1.0,
                          "chase_risk": 0.0, "execution_risk": 0.0},
            "LONG_CLEAN": {"borrow_cost_risk": 0.0,
                           "chase_risk": 0.0, "execution_risk": 0.0},
        },
    )
    assert ranked[0].signal.symbol == "LONG_CLEAN", (
        "High borrow cost must demote a slightly-higher-discernment "
        "short below a cleaner long."
    )


def test_borrow_cost_zero_for_longs_by_default():
    """When context provides no borrow_cost_risk for a long, the
    scorer treats it as zero — longs are never punished for a
    short-only cost input."""
    long = _S("L", "BUY", 0.60)
    short = _S("S", "SELL_SHORT", 0.60)
    ranked = rank_actionable(
        [long, short],
        context_by_symbol={
            # Deliberately omit borrow_cost_risk for both. Scorer default
            # is 0.0 → the direction alone shouldn't tilt the score.
        },
    )
    # Same discernment, same defaults → scores should tie or be
    # deterministically ordered by stable sort. Either symbol may lead;
    # the important property is that neither is penalized for its
    # direction. Assert the scores are equal.
    scores = sorted(x.opportunity_score for x in ranked)
    assert abs(scores[0] - scores[-1]) < 1e-9


# ── Direct scorer sanity ────────────────────────────────────────────

def test_compute_opportunity_score_clamps_penalties():
    """Penalty inputs above 1.0 (buggy caller) must clamp to 1.0
    rather than driving the score negative or below zero."""
    sig = _S("X", "BUY", 0.80)
    score, comps = compute_opportunity_score(
        sig, chase_risk=5.0, execution_risk=5.0, borrow_cost_risk=5.0,
    )
    # Score is clamped to [0, 1].
    assert 0.0 <= score <= 1.0
    assert comps["chase_risk"] == 1.0
    assert comps["execution_risk"] == 1.0
    assert comps["borrow_cost_risk"] == 1.0
