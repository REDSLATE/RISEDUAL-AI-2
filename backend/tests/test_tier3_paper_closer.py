"""Unit tests for the Tier-3 paper-bot closer.

Pure decision-function tests — the orchestrator is exercised
end-to-end through integration tests with real Mongo when
needed, but the exit cascade is the IP-relevant bit and lives
in ``_decide_equity_exit``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.tier3_paper_closer import (
    _decide_equity_exit,
    _resolve_equity_exit_config,
)


# ── default cascade ───────────────────────────────────────────


def _cfg(**overrides):
    base = {
        "sl_pct": 1.0,
        "tp_pct": 6.0,
        "tp_disabled": True,
        "trail_enabled": True,
        "trail_trigger_pct": 2.0,
        "trail_giveback_pct": 50.0,
        "hold_hours": 36,
    }
    base.update(overrides)
    return base


def test_stop_loss_fires_at_one_pct_floor():
    """Default SL=1%. Drop to 99 (entry × 0.99) triggers."""
    reason = _decide_equity_exit(
        entry_price=100.0,
        current_price=98.99,
        peak_price=100.0,
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        cfg=_cfg(),
    )
    assert reason == "stop_loss"


def test_no_exit_when_price_holds_inside_band():
    reason = _decide_equity_exit(
        entry_price=100.0,
        current_price=100.5,
        peak_price=100.5,
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        cfg=_cfg(),
    )
    assert reason is None


def test_take_profit_off_by_default_even_when_price_jumps():
    """Default cfg has tp_disabled=True. A +10% move should NOT
    fire a hard TP — trailing logic handles winners instead."""
    reason = _decide_equity_exit(
        entry_price=100.0,
        current_price=110.0,
        peak_price=110.0,
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        cfg=_cfg(),
    )
    # Peak == current_price means no giveback yet, so trail is
    # armed but not fired. Reason should be None.
    assert reason is None


def test_take_profit_fires_when_explicitly_enabled():
    reason = _decide_equity_exit(
        entry_price=100.0,
        current_price=106.5,
        peak_price=106.5,
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        cfg=_cfg(tp_disabled=False, tp_pct=6.0),
    )
    assert reason == "take_profit"


def test_trailing_stop_fires_after_giveback():
    """Peak hit +3% (103). Current at 101.4 — past the 50%-giveback
    floor of 101.5. Trail fires."""
    reason = _decide_equity_exit(
        entry_price=100.0,
        current_price=101.4,
        peak_price=103.0,
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        cfg=_cfg(),
    )
    assert reason == "trailing_stop"


def test_hold_window_expired_is_safety_floor():
    """Even a flat-positioned trade gets force-closed after hold
    window expires."""
    reason = _decide_equity_exit(
        entry_price=100.0,
        current_price=100.1,
        peak_price=100.1,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=37),
        cfg=_cfg(hold_hours=36),
    )
    assert reason == "hold_window_expired"


def test_sl_takes_priority_over_hold_expiration():
    """If SL is hit AND the position is past hold window, SL
    wins (lower price = bigger loss = exit fast)."""
    reason = _decide_equity_exit(
        entry_price=100.0,
        current_price=98.0,
        peak_price=100.0,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=48),
        cfg=_cfg(),
    )
    assert reason == "stop_loss"


def test_zero_entry_returns_none_safely():
    """Defensive: degenerate entry_price would div-by-zero in SL
    calc without the guard. Verify we just return None."""
    reason = _decide_equity_exit(
        entry_price=0.0,
        current_price=10.0,
        peak_price=12.0,
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        cfg=_cfg(),
    )
    assert reason is None


# ── env config ────────────────────────────────────────────────


def test_config_defaults(monkeypatch):
    for key in ["EQUITY_SL_PCT", "EQUITY_TP_PCT", "EQUITY_DISABLE_TP",
                "EQUITY_TRAIL_ENABLED", "EQUITY_TRAIL_TRIGGER_PCT",
                "EQUITY_TRAIL_GIVEBACK_PCT", "EQUITY_HOLD_HOURS"]:
        monkeypatch.delenv(key, raising=False)
    cfg = _resolve_equity_exit_config()
    assert cfg["sl_pct"] == 1.0
    assert cfg["tp_disabled"] is True
    assert cfg["trail_enabled"] is True
    assert cfg["trail_trigger_pct"] == 2.0
    assert cfg["trail_giveback_pct"] == 50.0
    assert cfg["hold_hours"] == 36


def test_config_can_loosen_to_legacy(monkeypatch):
    """Operator can flip back to a permissive 3%/6% / 96h hold for
    a backtest comparison or shadow run."""
    monkeypatch.setenv("EQUITY_SL_PCT", "3.0")
    monkeypatch.setenv("EQUITY_DISABLE_TP", "0")
    monkeypatch.setenv("EQUITY_TP_PCT", "6.0")
    monkeypatch.setenv("EQUITY_HOLD_HOURS", "96")
    cfg = _resolve_equity_exit_config()
    assert cfg["sl_pct"] == 3.0
    assert cfg["tp_disabled"] is False
    assert cfg["hold_hours"] == 96


def test_trail_can_be_disabled(monkeypatch):
    monkeypatch.setenv("EQUITY_TRAIL_ENABLED", "0")
    cfg = _resolve_equity_exit_config()
    assert cfg["trail_enabled"] is False
