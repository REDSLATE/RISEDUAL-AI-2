"""Trailing-stop unit tests for the crypto closer.

Pins the 2026-Q2 calibration upgrade: hard TP off by default,
SL=1%, trailing stop arms at +2% with 50% giveback. Tests are
pure (no Mongo, no quote provider) — they hit the helper
functions directly so the assertions are robust against any
upstream wiring changes.
"""
from __future__ import annotations

from services.crypto_closer import (
    _check_trailing_exit,
    _resolve_trail_config,
    _update_peak_price,
)


# ── peak watermark ────────────────────────────────────────────


def test_peak_initialised_when_missing():
    assert _update_peak_price("LONG", None, 100.0) == 100.0
    assert _update_peak_price("SHORT", None, 100.0) == 100.0


def test_peak_long_tracks_high():
    assert _update_peak_price("LONG", 100.0, 102.0) == 102.0
    assert _update_peak_price("LONG", 102.0, 101.0) == 102.0  # no regression


def test_peak_short_tracks_low():
    assert _update_peak_price("SHORT", 100.0, 98.0) == 98.0
    assert _update_peak_price("SHORT", 98.0, 99.0) == 98.0  # no regression


# ── trailing exit ─────────────────────────────────────────────


def test_trail_disarmed_below_trigger():
    """Peak gain at 1.5% < trigger of 2% → trail not armed."""
    assert not _check_trailing_exit(
        direction="LONG",
        entry_price=100.0,
        current_price=99.0,
        peak_price=101.5,
        trigger_pct=2.0,
        giveback_pct=50.0,
    )


def test_trail_long_armed_and_fires_at_50pct_giveback():
    """Peak hit +3%, current price back to entry+1.4% (clearly below
    the 50%-giveback floor of entry+1.5%) → trail fires."""
    assert _check_trailing_exit(
        direction="LONG",
        entry_price=100.0,
        current_price=101.4,
        peak_price=103.0,
        trigger_pct=2.0,
        giveback_pct=50.0,
    )


def test_trail_long_armed_but_holds_above_floor():
    """Peak hit +3%, current price at +2.0% → above the 50%-giveback
    floor (entry+1.5%) so trail does NOT fire yet."""
    assert not _check_trailing_exit(
        direction="LONG",
        entry_price=100.0,
        current_price=102.0,
        peak_price=103.0,
        trigger_pct=2.0,
        giveback_pct=50.0,
    )


def test_trail_short_armed_and_fires_at_50pct_giveback():
    """SHORT favourable = falling price. Peak (low) at 97 = -3%
    from entry. Current at 98.6 — past 50% giveback (above the
    98.5 ceiling) → trail fires."""
    assert _check_trailing_exit(
        direction="SHORT",
        entry_price=100.0,
        current_price=98.6,
        peak_price=97.0,
        trigger_pct=2.0,
        giveback_pct=50.0,
    )


def test_trail_short_armed_but_holds_below_ceiling():
    assert not _check_trailing_exit(
        direction="SHORT",
        entry_price=100.0,
        current_price=98.0,
        peak_price=97.0,
        trigger_pct=2.0,
        giveback_pct=50.0,
    )


def test_trail_no_peak_means_no_fire():
    assert not _check_trailing_exit(
        direction="LONG",
        entry_price=100.0,
        current_price=98.0,
        peak_price=None,
        trigger_pct=2.0,
        giveback_pct=50.0,
    )


def test_trail_zero_entry_safe():
    """Defensive: a degenerate entry_price=0 would div-by-zero
    without the guard. Verify we just return False."""
    assert not _check_trailing_exit(
        direction="LONG",
        entry_price=0.0,
        current_price=10.0,
        peak_price=12.0,
        trigger_pct=2.0,
        giveback_pct=50.0,
    )


# ── env config ────────────────────────────────────────────────


def test_trail_config_defaults(monkeypatch):
    monkeypatch.delenv("CRYPTO_TRAIL_ENABLED", raising=False)
    monkeypatch.delenv("CRYPTO_TRAIL_TRIGGER_PCT", raising=False)
    monkeypatch.delenv("CRYPTO_TRAIL_GIVEBACK_PCT", raising=False)
    cfg = _resolve_trail_config()
    assert cfg == {"enabled": True, "trigger_pct": 2.0, "giveback_pct": 50.0}


def test_trail_config_can_be_disabled(monkeypatch):
    monkeypatch.setenv("CRYPTO_TRAIL_ENABLED", "0")
    cfg = _resolve_trail_config()
    assert cfg["enabled"] is False


def test_trail_config_overrides(monkeypatch):
    monkeypatch.setenv("CRYPTO_TRAIL_TRIGGER_PCT", "1.5")
    monkeypatch.setenv("CRYPTO_TRAIL_GIVEBACK_PCT", "33.3")
    cfg = _resolve_trail_config()
    assert cfg["trigger_pct"] == 1.5
    assert cfg["giveback_pct"] == 33.3


def test_trail_config_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("CRYPTO_TRAIL_TRIGGER_PCT", "not-a-number")
    cfg = _resolve_trail_config()
    assert cfg["trigger_pct"] == 2.0
