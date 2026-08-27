"""Tests for the economic-fingerprint dedup hash (2026-02).

Ported from IGNISpilot's ``camaroOpportunityEngine.ts::economic_fingerprint``.
Guardrails:
* Deterministic — identical inputs produce identical hex output
* Tiny price wiggles collide (same bucket) — bigger real moves don't
* Direction / setup-type / timeframe flips produce different hashes
* Bad inputs (NaN, None, negative ATR) don't crash and don't collapse
  to a single collision hash
"""
from __future__ import annotations

import pytest

from services.alpha_fingerprint import economic_fingerprint


def _fp(**overrides) -> str:
    base = dict(
        symbol="GOOGL", strategy="vwap_reclaim", direction="long",
        timeframe="intraday", entry_zone=185.20, atr=2.5,
    )
    base.update(overrides)
    return economic_fingerprint(**base)


# ─── deterministic ──────────────────────────────────────────────


def test_same_inputs_same_hash():
    assert _fp() == _fp()


def test_hash_is_64_char_hex():
    h = _fp()
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_case_and_whitespace_insensitive_symbol():
    assert _fp(symbol="  googl ") == _fp(symbol="GOOGL")


def test_case_insensitive_strategy_and_direction():
    a = economic_fingerprint("AAPL", "MOMENTUM", "LONG", "INTRADAY", 200.0, 2.0)
    b = economic_fingerprint("AAPL", "momentum", "long", "intraday", 200.0, 2.0)
    assert a == b


# ─── zone-bucket collision behaviour ────────────────────────────


def test_tiny_price_wiggle_collides_same_bucket():
    """$185.19 vs $185.21 with ATR=2.5 → zone_size = max(0.625, 0.185)
    = 0.625. Both round to the same bucket integer (~296)."""
    assert _fp(entry_zone=185.19) == _fp(entry_zone=185.21)


def test_meaningful_price_move_does_not_collide():
    """$185 vs $189 with ATR=2.5 → different buckets."""
    assert _fp(entry_zone=185.0) != _fp(entry_zone=189.0)


def test_atr_scaled_bucket_widens_for_volatile_stocks():
    """Same price move should collide when ATR is much larger."""
    # $185 vs $186 with ATR=1 (0.25 bucket) → different buckets
    tight = economic_fingerprint("GOOGL", "s", "long", "intraday", 185.0, 1.0)
    tight2 = economic_fingerprint("GOOGL", "s", "long", "intraday", 186.0, 1.0)
    assert tight != tight2
    # Same $1 move with ATR=10 (2.5 bucket) → SAME bucket
    loose = economic_fingerprint("GOOGL", "s", "long", "intraday", 185.0, 10.0)
    loose2 = economic_fingerprint("GOOGL", "s", "long", "intraday", 186.0, 10.0)
    assert loose == loose2


# ─── field flips produce different hashes ───────────────────────


def test_direction_flip_differs():
    assert _fp(direction="long") != _fp(direction="short")


def test_symbol_flip_differs():
    assert _fp(symbol="GOOGL") != _fp(symbol="AAPL")


def test_setup_type_flip_differs():
    assert _fp(strategy="vwap_reclaim") != _fp(strategy="hod_break")


def test_timeframe_flip_differs():
    assert _fp(timeframe="intraday") != _fp(timeframe="swing")


# ─── bad-input safety ───────────────────────────────────────────


def test_none_atr_defaults_to_one():
    a = _fp(atr=None)
    b = _fp(atr=1.0)
    assert a == b


def test_negative_atr_defaults_to_one():
    a = _fp(atr=-3.0)
    b = _fp(atr=1.0)
    assert a == b


def test_zero_atr_defaults_to_one():
    a = _fp(atr=0.0)
    b = _fp(atr=1.0)
    assert a == b


def test_zero_entry_zone_does_not_crash():
    # Both entry and atr degenerate → still produces a valid hash
    h = economic_fingerprint("X", "s", "long", "intraday", 0.0, 0.0)
    assert len(h) == 64


def test_empty_symbol_does_not_crash():
    h = economic_fingerprint("", "s", "long", "intraday", 100.0, 2.0)
    assert len(h) == 64


def test_nan_entry_does_not_crash():
    h = economic_fingerprint("X", "s", "long", "intraday", float("nan"), 2.0)
    assert len(h) == 64


# ─── penny-stock behaviour ──────────────────────────────────────


def test_penny_stock_uses_price_based_bucket():
    """For a $2 stock with ATR=$0.10, ``max(0.025, 0.002)`` = 0.025
    bucket. 2.00 vs 2.02 → 80 vs 81 buckets → different hashes."""
    a = economic_fingerprint("SWVL", "s", "long", "intraday", 2.00, 0.10)
    b = economic_fingerprint("SWVL", "s", "long", "intraday", 2.02, 0.10)
    assert a != b
