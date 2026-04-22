"""Tests for the Drawdown + Multi-Bot Allocator layer.

Two pure-function modules under test:
  * :mod:`ai_core.drawdown_allocator` — `compute_drawdown`,
    `compute_drawdown_multiplier`, `compute_bot_score`,
    `allocate_capital`, `apply_global_risk_controls`.
  * Integration: the opt-in `equity_curve` / `bot_capital` kwargs
    on :func:`services.trading_bot_service.execute_signal`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_core import drawdown_allocator as da
from services import trading_bot_service as tbs


# ════════════════════════════════════════════════════════════════════════════
# compute_drawdown
# ════════════════════════════════════════════════════════════════════════════

def test_compute_drawdown_empty_curve_returns_zero():
    assert da.compute_drawdown([]) == 0.0


def test_compute_drawdown_single_point_returns_zero():
    assert da.compute_drawdown([10000.0]) == 0.0


def test_compute_drawdown_monotonic_up_returns_zero():
    """A curve that only ever makes new highs has 0% drawdown."""
    assert da.compute_drawdown([100, 110, 120, 130, 140]) == 0.0


def test_compute_drawdown_monotonic_down_uses_first_value_as_peak():
    """Curve 100 → 80: peak=100, trough=80 → 20% drawdown."""
    assert da.compute_drawdown([100.0, 90.0, 80.0]) == 0.2


def test_compute_drawdown_tracks_worst_peak_to_trough():
    """Worst swing was 120 → 90 = 25%, even though curve recovered."""
    curve = [100, 120, 110, 90, 105, 115]
    assert da.compute_drawdown(curve) == 0.25


def test_compute_drawdown_does_not_use_post_trough_recovery_as_peak():
    """Peak only updates when a new high is made after a trough."""
    curve = [100, 80, 95, 85]  # second leg: peak stays 100, trough 80
    assert da.compute_drawdown(curve) == 0.2


def test_compute_drawdown_returns_rounded_to_four_places():
    curve = [100.0, 90.333333]
    # (100 - 90.333333) / 100 = 0.09666... → rounded to 0.0967
    assert da.compute_drawdown(curve) == 0.0967


# ════════════════════════════════════════════════════════════════════════════
# compute_drawdown_multiplier (piecewise)
# ════════════════════════════════════════════════════════════════════════════

def test_multiplier_empty_curve_is_full_risk():
    assert da.compute_drawdown_multiplier([]) == 1.0


def test_multiplier_flat_curve_is_full_risk():
    assert da.compute_drawdown_multiplier([100, 100, 100]) == 1.0


def test_multiplier_below_soft_drawdown_is_full_risk():
    """5% drawdown < SOFT_DRAWDOWN (10%) → no throttle."""
    curve = [100.0, 95.0]
    assert da.compute_drawdown_multiplier(curve) == 1.0


def test_multiplier_at_exact_soft_threshold_is_full_risk():
    """10% drawdown, boundary case (`dd <= SOFT_DRAWDOWN`) → full risk."""
    curve = [100.0, 90.0]
    assert da.compute_drawdown_multiplier(curve) == 1.0


def test_multiplier_midway_between_soft_and_max_is_interpolated():
    """15% drawdown — halfway between 10% and 20%.
    Linear taper: scale = 1 - (0.15 - 0.10) / (0.20 - 0.10) = 0.5.
    0.5 > MIN_RISK_MULTIPLIER (0.3) so the floor isn't hit."""
    curve = [100.0, 85.0]
    assert da.compute_drawdown_multiplier(curve) == pytest.approx(0.5, abs=0.01)


def test_multiplier_three_quarters_hits_floor():
    """17.5% drawdown: scale = 1 - 0.075/0.1 = 0.25 — below MIN, clamps to 0.3."""
    curve = [100.0, 82.5]
    assert da.compute_drawdown_multiplier(curve) == da.MIN_RISK_MULTIPLIER


def test_multiplier_at_max_drawdown_clamps_to_floor():
    """20% drawdown — at the MAX_DRAWDOWN boundary, we clamp."""
    curve = [100.0, 80.0]
    assert da.compute_drawdown_multiplier(curve) == da.MIN_RISK_MULTIPLIER


def test_multiplier_beyond_max_drawdown_stays_at_floor():
    """40% drawdown — well beyond MAX, should stay at MIN_RISK_MULTIPLIER."""
    curve = [100.0, 60.0]
    assert da.compute_drawdown_multiplier(curve) == da.MIN_RISK_MULTIPLIER


def test_multiplier_never_returns_below_floor():
    """Pathological -99% drawdown still respects the floor."""
    curve = [100.0, 1.0]
    assert da.compute_drawdown_multiplier(curve) >= da.MIN_RISK_MULTIPLIER


# ════════════════════════════════════════════════════════════════════════════
# compute_bot_score
# ════════════════════════════════════════════════════════════════════════════

def test_bot_score_perfect_winner_profitable():
    """win_rate=1, pnl>0 → 0.7 + 0.3 = 1.0."""
    assert da.compute_bot_score({"win_rate": 1.0, "pnl": 1000}) == pytest.approx(1.0)


def test_bot_score_perfect_winner_flat_pnl_no_bonus():
    """win_rate=1, pnl=0 → 0.7 + 0 = 0.7 (strict `> 0` on PnL)."""
    assert da.compute_bot_score({"win_rate": 1.0, "pnl": 0}) == pytest.approx(0.7)


def test_bot_score_losing_bot_gets_floor():
    """win_rate=0, pnl<0 → (0 + 0) = 0, floored to 0.1."""
    assert da.compute_bot_score({"win_rate": 0.0, "pnl": -500}) == 0.1


def test_bot_score_defaults_to_neutral_win_rate():
    """Missing win_rate → 0.5 → 0.35 + 0 = 0.35."""
    assert da.compute_bot_score({"pnl": -100}) == pytest.approx(0.35)


def test_bot_score_empty_stats_returns_floor_or_neutral():
    """Empty dict → neutral win_rate 0.5, zero pnl → 0.35."""
    assert da.compute_bot_score({}) == pytest.approx(0.35)


def test_bot_score_tolerates_none_values():
    """`None` values coerce to defaults, not raise."""
    assert da.compute_bot_score({"win_rate": None, "pnl": None}) == pytest.approx(0.35)


# ════════════════════════════════════════════════════════════════════════════
# allocate_capital
# ════════════════════════════════════════════════════════════════════════════

def test_allocate_capital_empty_dict_returns_empty():
    assert da.allocate_capital(10_000.0, {}) == {}


def test_allocate_capital_single_bot_gets_everything():
    bots = {"bot1": {"win_rate": 0.6, "pnl": 500}}
    out = da.allocate_capital(10_000.0, bots)
    assert out == {"bot1": 10_000.0}


def test_allocate_capital_uses_scores_as_weights():
    """Winner (score 1.0) should get more than loser (score 0.1) ~10× more."""
    bots = {
        "winner": {"win_rate": 1.0, "pnl": 1000},   # score=1.0
        "loser":  {"win_rate": 0.0, "pnl": -500},   # score=0.1
    }
    out = da.allocate_capital(10_000.0, bots)
    # weights: 1.0 / 1.1 ≈ 0.909, 0.1 / 1.1 ≈ 0.0909
    assert out["winner"] == pytest.approx(9090.91, abs=0.1)
    assert out["loser"] == pytest.approx(909.09, abs=0.1)
    assert (out["winner"] + out["loser"]) == pytest.approx(10_000.0, abs=0.1)


def test_allocate_capital_equal_scores_split_evenly():
    bots = {
        "a": {"win_rate": 0.5, "pnl": 0},
        "b": {"win_rate": 0.5, "pnl": 0},
    }
    out = da.allocate_capital(10_000.0, bots)
    assert out["a"] == out["b"] == 5_000.0


# ════════════════════════════════════════════════════════════════════════════
# apply_global_risk_controls
# ════════════════════════════════════════════════════════════════════════════

def test_global_controls_flat_equity_returns_base_or_capital():
    """No drawdown + base under bot capital → base returned unchanged."""
    out = da.apply_global_risk_controls(
        base_size=500.0, equity_curve=[10_000, 10_000], bot_capital=1000.0
    )
    assert out == 500.0


def test_global_controls_caps_at_bot_capital():
    """Base $2000 > bot capital $800 → shrink to $800 (no drawdown)."""
    out = da.apply_global_risk_controls(
        base_size=2000.0, equity_curve=[10_000], bot_capital=800.0
    )
    assert out == 800.0


def test_global_controls_applies_drawdown_taper():
    """15% drawdown → 0.5× multiplier on $1000 base → $500."""
    curve = [10_000, 8500]  # 15% drawdown
    out = da.apply_global_risk_controls(
        base_size=1000.0, equity_curve=curve, bot_capital=1000.0
    )
    # 1000 * 0.5 = 500
    assert out == pytest.approx(500.0, abs=1.0)


def test_global_controls_clamps_at_floor_beyond_max_drawdown():
    """30% drawdown (> MAX) → $1000 × MIN_RISK_MULTIPLIER (0.3) = $300."""
    curve = [10_000, 7000]  # 30% drawdown
    out = da.apply_global_risk_controls(
        base_size=1000.0, equity_curve=curve, bot_capital=1000.0
    )
    assert out == pytest.approx(300.0, abs=0.01)


def test_global_controls_zero_bot_capital_returns_zero():
    out = da.apply_global_risk_controls(
        base_size=500.0, equity_curve=[10_000], bot_capital=0.0
    )
    assert out == 0.0


def test_global_controls_negative_bot_capital_returns_zero():
    """Defensive: a bot with negative allocated capital is effectively closed."""
    out = da.apply_global_risk_controls(
        base_size=500.0, equity_curve=[10_000], bot_capital=-100.0
    )
    assert out == 0.0


# ════════════════════════════════════════════════════════════════════════════
# execute_signal integration — new equity_curve / bot_capital kwargs
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def capture_execution(monkeypatch):
    """Patch `_execute_bot_trade` to capture the call args."""
    calls: list[dict] = []

    async def _fake_exec(bot, symbol, side, qty, price, **kw):
        calls.append({"symbol": symbol, "side": side, "qty": qty, "price": price})
        return {"status": "filled", "filled_qty": qty, "filled_price": price}

    monkeypatch.setattr(tbs, "_execute_bot_trade", _fake_exec)
    return calls


def _healthy_readiness() -> dict:
    return {
        "confidence_score": 100.0,
        "unlocked": True,
        "stats": {
            "days": 30, "total_trades": 150, "high_conf_trades": 60,
            "strong_miss_rate": 0.05, "clamp_total": 0,
        },
    }


@pytest.mark.asyncio
async def test_execute_signal_equity_curve_none_skips_risk_layer(capture_execution):
    """Omitting equity_curve / bot_capital → drawdown layer bypassed."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1500)

    out = await tbs.execute_signal(signal, {}, _healthy_readiness(), config)
    assert out.get("skipped") is not True
    assert len(capture_execution) == 1


@pytest.mark.asyncio
async def test_execute_signal_flat_equity_curve_lets_trade_through(capture_execution):
    """Flat equity curve = 0% drawdown → full risk. Tier 3 sizing at
    confidence=100, readiness=100 scales $1000 base to $1500; drawdown
    layer is a no-op, bot_capital $2000 gives headroom, result $1500."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1000)

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config,
        equity_curve=[10_000, 10_000, 10_000],
        bot_capital=2000.0,
    )
    assert out.get("skipped") is not True
    assert out["size_usd"] == pytest.approx(1500.0, abs=0.01)


@pytest.mark.asyncio
async def test_execute_signal_drawdown_taper_shrinks_size(capture_execution):
    """Tier 3 sizes $1000 → $1500 first; 15% drawdown → 0.5× → $750."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1000)

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config,
        equity_curve=[10_000, 8500],  # 15% drawdown
        bot_capital=2000.0,
    )
    assert out.get("skipped") is not True
    assert out["size_usd"] == pytest.approx(750.0, abs=1.0)
    assert len(capture_execution) == 1


@pytest.mark.asyncio
async def test_execute_signal_bot_capital_cap_beats_base_size(capture_execution):
    """Bot is allocated only $500 — base of $1500 shrinks to $500."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1500)

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config,
        equity_curve=[10_000, 10_000],  # no drawdown
        bot_capital=500.0,
    )
    assert out["size_usd"] == pytest.approx(500.0, abs=0.01)


@pytest.mark.asyncio
async def test_execute_signal_risk_control_skips_on_zero_bot_capital(capture_execution):
    """Bot with zero allocation → skipped with risk control reason."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1000)

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config,
        equity_curve=[10_000],
        bot_capital=0.0,
    )
    assert out["skipped"] is True
    assert out["reason"] == "risk control"
    assert capture_execution == []


@pytest.mark.asyncio
async def test_execute_signal_full_stack_portfolio_plus_drawdown(capture_execution):
    """All three gates stacked. Tier 3 scales $1500 base up (high
    confidence+readiness), portfolio headroom $2500 leaves it alone,
    drawdown layer caps at bot_capital $2000 then × 0.5 (15% DD) = $1000."""
    signal = {
        "symbol": "NVDA", "entry": 500, "direction": "LONG", "confidence": 100,
    }
    config = SimpleNamespace(trade_size=1500)
    open_positions = [{"size_usd": 500, "sector": "finance"}]  # $2500 headroom

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config,
        open_positions=open_positions,
        equity_curve=[10_000, 8500],  # 15% drawdown
        bot_capital=2000.0,
    )
    assert out.get("skipped") is not True
    # bot_capital ($2000) caps first, then 0.5× drawdown = $1000.
    assert out["size_usd"] == pytest.approx(1000.0, abs=2.0)


@pytest.mark.asyncio
async def test_execute_signal_only_equity_curve_supplied_skips_risk_layer(
    capture_execution,
):
    """If bot_capital is None but equity_curve provided, layer is skipped.
    Both args must be supplied together — matches the `is not None` guard.
    Tier 3 still scales $1000 → $1500 at confidence=100."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1000)

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config,
        equity_curve=[10_000, 7000],  # would be 30% drawdown → 0.3× mult
        bot_capital=None,
    )
    # Layer skipped → Tier-3-sized trade
    assert out["size_usd"] == pytest.approx(1500.0, abs=0.01)
