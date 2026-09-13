"""Tests for direction-aware P&L in alpha_fill_writer._resolve_metrics (P1-B)."""
from __future__ import annotations

from services.alpha_fill_writer import _resolve_metrics


def test_long_win_pnl_uses_close_minus_entry():
    row = {
        "direction": "LONG",
        "entry_price": 100.0,
        "close_price": 110.0,
        "size": 2.0,
        "stop_price": 95.0,
    }
    m = _resolve_metrics(row)
    assert m["realized_pnl_usd"] == 20.0        # (110-100)*2
    assert m["realized_r"] == 2.0                # (110-100)/(100-95)
    assert m["direction"] == "LONG"


def test_short_win_pnl_uses_entry_minus_close():
    """SHORT winning: price drops from 100 to 90 → we profit."""
    row = {
        "direction": "SHORT",
        "entry_price": 100.0,
        "close_price": 90.0,
        "size": 2.0,
        "stop_price": 105.0,  # stop sits ABOVE entry for shorts
    }
    m = _resolve_metrics(row)
    assert m["realized_pnl_usd"] == 20.0        # (100-90)*2 = +20
    # R = (entry-close)/|entry-stop| = 10/5 = 2.0
    assert m["realized_r"] == 2.0
    assert m["direction"] == "SHORT"


def test_short_loss_pnl_is_negative():
    row = {
        "direction": "SHORT",
        "entry_price": 100.0,
        "close_price": 108.0,
        "size": 3.0,
        "stop_price": 110.0,
    }
    m = _resolve_metrics(row)
    assert m["realized_pnl_usd"] == -24.0       # (100-108)*3 = -24
    assert m["realized_r"] == -0.8              # (100-108)/|100-110| = -8/10


def test_short_mfe_uses_trough_not_peak():
    """For a SHORT, the best-case excursion is when the price dropped
    the most (trough), NOT when it rose (peak)."""
    row = {
        "direction": "SHORT",
        "entry_price": 100.0,
        "close_price": 100.0,
        "stop_price": 105.0,
        "trough_price": 92.0,    # dropped $8 → favorable
        "peak_price": 103.0,     # rose $3 → adverse
    }
    m = _resolve_metrics(row)
    # MFE = (entry - trough) / risk = (100-92)/5 = 1.6
    assert m["mfe_r"] == 1.6
    # MAE = (entry - peak) / risk = (100-103)/5 = -0.6
    assert m["mae_r"] == -0.6


def test_long_mfe_uses_peak_not_trough():
    """Sanity — long behavior unchanged from pre-P1-B."""
    row = {
        "direction": "LONG",
        "entry_price": 100.0,
        "close_price": 100.0,
        "stop_price": 95.0,
        "peak_price": 108.0,     # rose $8 → favorable
        "trough_price": 97.0,    # dropped $3 → adverse
    }
    m = _resolve_metrics(row)
    assert m["mfe_r"] == 1.6
    assert m["mae_r"] == -0.6


def test_short_entry_slippage_sign_normalized():
    """Positive entry_slippage_bps must always mean 'worse than trigger'.
    For a SHORT we sold at 99 when the trigger said 100 → we got a
    worse fill (sold below intended price) → +100 bps."""
    row = {
        "direction": "SHORT",
        "entry_price": 99.0,
        "close_price": 99.0,
        "size": 1.0,
        "stop_price": 104.0,
        "alpha_daytrader": {"confirmation_price": 100.0},
    }
    m = _resolve_metrics(row)
    assert m["entry_slippage_bps"] == 100.0


def test_short_exit_slippage_against_target():
    """For a SHORT profit-target hit at 90 when target was 91, we did
    better than target (bought lower to cover) → negative bps (better
    than reference)."""
    row = {
        "direction": "SHORT",
        "entry_price": 100.0,
        "close_price": 90.0,
        "size": 1.0,
        "stop_price": 105.0,
        "close_reason": "target_hit",
        "alpha_daytrader": {"target_price": 91.0},
    }
    m = _resolve_metrics(row)
    # (target - close)/target * 10000 = (91-90)/91 * 10000 ≈ 109.89
    assert abs(m["exit_slippage_bps"] - round((91 - 90) / 91 * 10000, 2)) < 1e-6
