"""Phase D+ tests: fill economics, fast intraday regime, refined edge lookup."""
from __future__ import annotations

from services.alpha_fill_writer import _resolve_metrics
from services.fast_intraday_regime import _classify


# ── fill writer metrics ──────────────────────────────

def test_resolve_metrics_computes_realized_r_and_slippage():
    row = {
        "entry_price": 100.0,
        "close_price": 103.0,
        "stop_price": 99.0,          # risk_per_share = 1.0
        "peak_price": 104.5,
        "trough_price": 99.2,
        "alpha_daytrader": {"confirmation_price": 99.90},  # trigger below fill
    }
    m = _resolve_metrics(row)
    assert m["realized_r"] == 3.0     # (103 - 100) / 1.0
    assert m["mfe_r"] == 4.5           # (104.5 - 100) / 1.0
    assert m["mae_r"] == -0.8          # (99.2 - 100) / 1.0
    # slippage = (100 - 99.90) / 99.90 * 10000 ≈ 10 bps
    assert m["slippage_bps"] == 10.01
    assert m["entry_fill_price"] == 100.0
    assert m["exit_fill_price"] == 103.0


def test_resolve_metrics_flags_unknown_risk():
    row = {
        "entry_price": 100.0,
        "close_price": 105.0,
        "stop_price": 0.0,  # no stop
        "peak_price": 106.0,
        "trough_price": 99.5,
        "alpha_daytrader": {"confirmation_price": 100.0},
    }
    m = _resolve_metrics(row)
    assert m.get("risk_unknown") is True
    # Falls back to raw % return
    assert m["realized_r"] == 0.05


def test_resolve_metrics_handles_missing_excursions():
    row = {
        "entry_price": 100.0,
        "close_price": 101.5,
        "stop_price": 99.0,
        "alpha_daytrader": {"confirmation_price": 100.0},
    }
    m = _resolve_metrics(row)
    assert m["realized_r"] == 1.5
    assert "mfe_r" not in m       # no peak recorded
    assert "mae_r" not in m       # no trough recorded


# ── fast intraday regime classifier ──────────────────

def test_fast_regime_classifies_momentum_ignition_up():
    label, feats = _classify(
        today_return=0.012, vol_ratio=1.3, run_rate=1.5, body_ratio=0.6,
    )
    assert label == "momentum_ignition_up"


def test_fast_regime_classifies_momentum_ignition_down():
    label, _ = _classify(-0.015, vol_ratio=1.3, run_rate=1.4, body_ratio=-0.7)
    assert label == "momentum_ignition_down"


def test_fast_regime_classifies_volatility_expansion():
    label, _ = _classify(today_return=0.001, vol_ratio=1.6, run_rate=1.1, body_ratio=0.1)
    assert label == "volatility_expansion"


def test_fast_regime_classifies_risk_off():
    label, _ = _classify(today_return=-0.007, vol_ratio=1.3, run_rate=1.0, body_ratio=-0.2)
    assert label == "risk_off"


def test_fast_regime_classifies_trend_up():
    label, _ = _classify(today_return=0.006, vol_ratio=1.0, run_rate=1.0, body_ratio=0.6)
    assert label == "trend_up"


def test_fast_regime_classifies_session_chop():
    label, _ = _classify(today_return=0.001, vol_ratio=0.9, run_rate=0.8, body_ratio=0.05)
    assert label == "session_chop"
