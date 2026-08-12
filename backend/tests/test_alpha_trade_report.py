"""Tests for Alpha Trade Report — edge agreement + serialization."""
from __future__ import annotations

from services.alpha_trade_report import _edge_agreement


def test_edge_agreement_positive_and_win():
    assert _edge_agreement("POSITIVE", 1.2) == "AGREE"


def test_edge_agreement_positive_but_lost():
    assert _edge_agreement("POSITIVE", -0.5) == "DISAGREE"


def test_edge_agreement_negative_and_lost():
    assert _edge_agreement("NEGATIVE", -0.8) == "AGREE"


def test_edge_agreement_negative_but_won():
    assert _edge_agreement("NEGATIVE", 0.4) == "DISAGREE"


def test_edge_agreement_discovering_is_na():
    assert _edge_agreement("DISCOVERING", 1.0) == "N/A"
    assert _edge_agreement("FLAT", -0.2) == "N/A"


def test_edge_agreement_missing_state_or_r_is_na():
    assert _edge_agreement(None, 1.0) == "N/A"
    assert _edge_agreement("POSITIVE", None) == "N/A"


# ── new fill metrics: exit slippage + pnl_usd ─────────

def test_resolve_metrics_computes_exit_slippage_on_stop_hit():
    from services.alpha_fill_writer import _resolve_metrics
    row = {
        "entry_price": 100.0,
        "close_price": 98.90,      # filled slightly below stop
        "stop_price": 99.0,
        "close_reason": "stop_loss",
        "size": 10.0,
        "alpha_daytrader": {"confirmation_price": 100.0, "target_price": 102.0},
    }
    m = _resolve_metrics(row)
    # exit slippage = (98.90 - 99.0) / 99.0 * 10000 ≈ -10.1 bps (worse than stop)
    assert m["exit_slippage_bps"] == -10.1
    # realized_pnl_usd = (98.90 - 100) * 10 = -11.00
    assert m["realized_pnl_usd"] == -11.0
    assert m["gross_notional_usd"] == 1000.0
    assert m["close_reason"] == "stop_loss"


def test_resolve_metrics_computes_exit_slippage_on_target_hit():
    from services.alpha_fill_writer import _resolve_metrics
    row = {
        "entry_price": 100.0,
        "close_price": 102.15,      # slight positive slippage past target
        "stop_price": 99.0,
        "close_reason": "profit_target",
        "size": 5.0,
        "alpha_daytrader": {"confirmation_price": 100.0, "target_price": 102.0},
    }
    m = _resolve_metrics(row)
    # exit slippage = (102.15 - 102.0) / 102.0 * 10000 ≈ 14.71 bps positive
    assert m["exit_slippage_bps"] == 14.71
    assert m["realized_pnl_usd"] == 10.75


def test_resolve_metrics_omits_exit_slippage_on_time_exit():
    from services.alpha_fill_writer import _resolve_metrics
    row = {
        "entry_price": 100.0,
        "close_price": 100.30,
        "stop_price": 99.0,
        "close_reason": "day_trade_eod_timer",
        "size": 3.0,
        "alpha_daytrader": {"confirmation_price": 100.0, "target_price": 102.0},
    }
    m = _resolve_metrics(row)
    # No stop-hit / target-hit → no reference price → no exit slippage
    assert "exit_slippage_bps" not in m
    assert m["realized_pnl_usd"] == 0.9
