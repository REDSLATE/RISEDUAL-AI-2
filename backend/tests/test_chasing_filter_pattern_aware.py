"""Tests for the pattern-aware chasing filter.

The 2026-02 update: the original filter used ``abs(move_pct)``
which killed the entire ``SHORT_SIDE_EXHAUSTION`` family (fires on
-3% to -12% moves, so ANY qualifying candidate was ≥ 4% down and
thus over the cap). The rewrite splits behaviour by ``setup_type``:

* Momentum setups     → block only on positive ≥ cap moves
* Mean-revert setups  → allow negative moves; only block extreme
                        catch-a-knife case (< -2× cap)
* Unknown/classical   → keep the historical abs() safety
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _run(coro):
    """Sync driver on a private loop — same pattern as the rest of
    this test bed to avoid closing the thread-default loop."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _call_chasing_filter(*, setup_type: str, move_pct: float, cap: float = 4.0):
    """Reach into the executor's chasing-filter block via the
    smallest surface we can — call ``maybe_route_live`` with the
    upstream gates stubbed so the filter is the only decision
    point that fires.

    Returns the ``reason`` string from ``_log_skip`` when the
    filter blocks, or ``None`` when the filter would allow the
    trade to continue (subsequent gates then bail us out).
    """
    from services import public_equity_live_executor as exe

    calls: list[dict] = []

    async def _fake_log_skip(_db, *, symbol, reason, intent=None, detail=None):
        calls.append({"reason": reason, "detail": detail or {}})

    async def _fake_move(_symbol):
        return move_pct

    async def _fake_creds(_db):
        # After the filter, this returns ``None`` → executor bails
        # with a ``no_broker_creds`` skip. That's fine — we only
        # want to know whether the chasing filter fired FIRST.
        return None

    intent = {
        "symbol": "TEST",
        "direction": "BUY",
        "action": "OPEN_LONG",
        "confidence": 0.7,
        "strategy_id": "alpha_daytrader:v1",
        "source_signal": "alpha_daytrader:v1",
        "setup_type": setup_type,
        "regime": "trend_up",
    }

    with patch.object(exe, "_log_skip", side_effect=_fake_log_skip), \
         patch.object(exe, "_intraday_move_pct", side_effect=_fake_move), \
         patch.object(exe, "_max_intraday_move_pct", return_value=cap), \
         patch.object(exe, "_aresolve_connect_creds", side_effect=_fake_creds), \
         patch.object(exe, "_rth_only_enabled", return_value=False), \
         patch.object(exe, "_symbol_cooldown_min", return_value=0):
        _run(exe.maybe_route_live(None, intent=intent))

    # First call to _log_skip is the reason we want. If the filter
    # blocked, it will be ``chasing_filter``. If it allowed, the
    # next gate (``no_broker_creds``) fires first.
    if not calls:
        return None
    return calls[0]["reason"]


# ─────────────────────────────────────────────
#  Mean-reversion patterns — dip-buy is now allowed
# ─────────────────────────────────────────────
@pytest.mark.parametrize("setup", [
    "short_side_exhaustion",
    "range_low_bounce",
    "vwap_fade_long",
    "opening_drive_fade",
    "pullback",
])
def test_mean_revert_pattern_allows_negative_dip_buy(setup):
    """A mean-revert BUY on a -6% move (cap 4%) must be allowed
    through — this was the SHORT_SIDE_EXHAUSTION regression the
    Why-Not-Trade diagnostic uncovered.
    """
    reason = _call_chasing_filter(setup_type=setup, move_pct=-6.0, cap=4.0)
    assert reason != "chasing_filter", (
        f"{setup} was blocked at -6% — mean-revert patterns must "
        f"be allowed to buy the dip"
    )


def test_short_side_exhaustion_allows_designed_deep_range():
    """2026-09-03 fix — SHORT_SIDE_EXHAUSTION explicitly fires on
    -3% to -12% moves. The old generic -2× knife guard (-8% at cap
    4%) cut it off before its designed sweet spot; the widened
    -3× guard (-12% at cap 4%) covers the pattern's full range.
    """
    # -10% is well inside the pattern's designed firing range → allow
    reason = _call_chasing_filter(
        setup_type="short_side_exhaustion", move_pct=-10.0, cap=4.0,
    )
    assert reason != "chasing_filter", (
        "short_side_exhaustion at -10% must be allowed — this was "
        "the GPRO regression the Why-Not-Trade diagnostic caught"
    )


def test_short_side_exhaustion_still_blocks_free_fall():
    """Beyond -3× cap (= -12% at cap 4%) is still knife territory
    even for the exhaustion pattern — the pattern's design range
    tops out around -12%."""
    reason = _call_chasing_filter(
        setup_type="short_side_exhaustion", move_pct=-13.0, cap=4.0,
    )
    assert reason == "chasing_filter"


def test_non_exhaustion_mean_revert_still_blocks_at_minus_two_times_cap():
    """Other mean-revert patterns (range_low_bounce, vwap_fade_long,
    etc.) keep the original -2× cap knife guard. Only
    SHORT_SIDE_EXHAUSTION gets the wider -3× band because its
    design range explicitly extends deeper.
    """
    # -8.01% past -2× cap → blocked for range_low_bounce
    r_deep = _call_chasing_filter(
        setup_type="range_low_bounce", move_pct=-8.01, cap=4.0,
    )
    assert r_deep == "chasing_filter"
    # -7.99% just under → allowed
    r_shallow = _call_chasing_filter(
        setup_type="range_low_bounce", move_pct=-7.99, cap=4.0,
    )
    assert r_shallow != "chasing_filter"


# ─────────────────────────────────────────────
#  Momentum patterns — chasing tops still blocked
# ─────────────────────────────────────────────
@pytest.mark.parametrize("setup", [
    "breakout",
    "high_of_day_break",
    "vwap_reclaim",
    "momentum_reacceleration",
])
def test_momentum_pattern_blocks_chasing_top(setup):
    """A momentum BUY on a +5% move at cap 4% is exactly the
    "already-mooned" case the filter was originally added for.
    Must still block."""
    reason = _call_chasing_filter(setup_type=setup, move_pct=5.0, cap=4.0)
    assert reason == "chasing_filter"


def test_momentum_pattern_allows_negative_move():
    """A momentum BUY signal fires on a -3% pullback isn't a
    "chasing top" — it's fine. Under the old ``abs()`` rule
    this was wrongly blocked; the new logic lets it through."""
    reason = _call_chasing_filter(
        setup_type="breakout", move_pct=-3.5, cap=4.0,
    )
    assert reason != "chasing_filter"


# ─────────────────────────────────────────────
#  Classical bullish reversal patterns — dip-buy is now allowed
#  (2026-09-03 fix — ANET inverse_head_and_shoulders regression)
# ─────────────────────────────────────────────
@pytest.mark.parametrize("setup", [
    "double_bottom",
    "inverse_head_and_shoulders",
    "falling_wedge",
])
def test_classical_reversal_pattern_allows_negative_dip_buy(setup):
    """Classical bullish reversal patterns (double_bottom,
    inverse_head_and_shoulders, falling_wedge) are dip-buys by
    construction. The 2026-09-03 fix moved them out of the
    unknown-abs() branch and into the dip-buy family so a -6% dip
    (the exact setup they're designed to catch) is no longer
    blocked by the chasing filter."""
    reason = _call_chasing_filter(setup_type=setup, move_pct=-6.0, cap=4.0)
    assert reason != "chasing_filter", (
        f"{setup} was blocked at -6% — classical reversal patterns "
        f"must be allowed to buy the dip"
    )


def test_classical_reversal_still_blocks_extreme_knife_catch():
    """Classical reversals get the -2× cap knife guard so a -10%
    free-fall (well past the pattern's design range) is still
    blocked."""
    reason = _call_chasing_filter(
        setup_type="inverse_head_and_shoulders", move_pct=-10.0, cap=4.0,
    )
    assert reason == "chasing_filter"


def test_classical_reversal_blocks_chasing_top():
    """Classical reversals are bullish reversals — buying at
    +5% is still buying a top, so block."""
    reason = _call_chasing_filter(
        setup_type="double_bottom", move_pct=5.0, cap=4.0,
    )
    assert reason == "chasing_filter"


# ─────────────────────────────────────────────
#  Unknown / unrecognized setups keep the safe default
# ─────────────────────────────────────────────
@pytest.mark.parametrize("setup", [
    "",           # missing
    "brand_new",  # unrecognized
])
def test_unknown_pattern_falls_back_to_abs_check(setup):
    """Symmetric block: unknown/unrecognized setups keep the
    historical ``abs(move_pct) >= cap`` behaviour so we don't
    accidentally widen a gate we haven't reasoned about."""
    # Positive over cap → blocked
    r_up = _call_chasing_filter(setup_type=setup, move_pct=4.5, cap=4.0)
    assert r_up == "chasing_filter"
    # Negative over cap → also blocked (abs semantics)
    r_dn = _call_chasing_filter(setup_type=setup, move_pct=-4.5, cap=4.0)
    assert r_dn == "chasing_filter"


# ─────────────────────────────────────────────
#  Data outage / cap disabled
# ─────────────────────────────────────────────
def test_data_outage_does_not_block(monkeypatch):
    """When ``_intraday_move_pct`` returns None (all providers
    down), the filter fails open — a legit signal must never
    be punished for a provider hiccup."""
    from services import public_equity_live_executor as exe

    calls: list[str] = []

    async def _fake_log_skip(_db, *, symbol, reason, intent=None, detail=None):
        calls.append(reason)

    async def _fake_move(_symbol):
        return None

    async def _fake_creds(_db):
        return None

    with patch.object(exe, "_log_skip", side_effect=_fake_log_skip), \
         patch.object(exe, "_intraday_move_pct", side_effect=_fake_move), \
         patch.object(exe, "_max_intraday_move_pct", return_value=4.0), \
         patch.object(exe, "_aresolve_connect_creds", side_effect=_fake_creds), \
         patch.object(exe, "_rth_only_enabled", return_value=False), \
         patch.object(exe, "_symbol_cooldown_min", return_value=0):
        _run(exe.maybe_route_live(None, intent={
            "symbol": "TEST", "direction": "BUY", "action": "OPEN_LONG",
            "confidence": 0.7, "setup_type": "breakout",
        }))
    assert "chasing_filter" not in calls
