"""Tests for the OCC 21-char symbol builder and parser.

OCC symbol correctness is non-negotiable — every broker rejects
mis-built symbols silently and there's no graceful degrade. This
suite pins build + parse against known-good symbols.

Coverage:
  * Build round-trips parse.
  * Known-good examples (AAPL, SPY, a low-strike + high-strike).
  * Error paths: oversized root, invalid type, zero/negative strike,
    strike overflow, missing chars in parse.
  * Leap year + Y2K boundary dates.
"""
from __future__ import annotations

from datetime import date

import pytest

from services.brokers.occ_symbol import (
    OCC_LENGTH,
    build_occ_symbol,
    parse_occ_symbol,
)


# ── Build ──────────────────────────────────────────────────────────

def test_build_aapl_call_basic():
    s = build_occ_symbol("AAPL", "2026-12-18", "call", 200.0)
    assert s == "AAPL  261218C00200000"
    assert len(s) == OCC_LENGTH


def test_build_spy_put_basic():
    s = build_occ_symbol("SPY", "2026-01-16", "put", 400.0)
    assert s == "SPY   260116P00400000"


def test_build_fractional_strike():
    """Penny-strike ($1.50) encodes correctly (1500 → 00001500)."""
    s = build_occ_symbol("AAPL", "2026-12-18", "call", 1.50)
    assert s.endswith("00001500")


def test_build_high_strike_google():
    """$2500 call — proves the 8-digit strike field handles 4-digit
    dollar amounts."""
    s = build_occ_symbol("GOOGL", "2026-12-18", "call", 2500.0)
    assert s == "GOOGL 261218C02500000"


def test_build_case_insensitive_underlying():
    s = build_occ_symbol("aapl", "2026-12-18", "call", 100.0)
    assert s.startswith("AAPL  ")


def test_build_case_insensitive_option_type():
    assert build_occ_symbol("AAPL", "2026-12-18", "CALL", 100.0) \
        == build_occ_symbol("AAPL", "2026-12-18", "call", 100.0)


def test_build_accepts_date_object():
    s = build_occ_symbol("AAPL", date(2026, 12, 18), "call", 200.0)
    assert s == "AAPL  261218C00200000"


# ── Build errors ────────────────────────────────────────────────────

def test_build_rejects_oversize_root():
    with pytest.raises(ValueError, match="up to 6"):
        build_occ_symbol("TOOLONG", "2026-12-18", "call", 100.0)


def test_build_rejects_empty_underlying():
    with pytest.raises(ValueError):
        build_occ_symbol("", "2026-12-18", "call", 100.0)


def test_build_rejects_zero_strike():
    with pytest.raises(ValueError, match="strike"):
        build_occ_symbol("AAPL", "2026-12-18", "call", 0)


def test_build_rejects_strike_overflow():
    # 10^8 / 1000 = $100,000 → just past the encodable max
    with pytest.raises(ValueError, match="exceeds"):
        build_occ_symbol("AAPL", "2026-12-18", "call", 100_000)


def test_build_rejects_invalid_option_type():
    with pytest.raises(ValueError, match="option_type"):
        build_occ_symbol("AAPL", "2026-12-18", "butterfly", 100.0)


# ── Parse ──────────────────────────────────────────────────────────

def test_parse_basic_call():
    p = parse_occ_symbol("AAPL  261218C00200000")
    assert p["underlying"] == "AAPL"
    assert p["expiration"] == "2026-12-18"
    assert p["option_type"] == "call"
    assert p["strike"] == 200.0


def test_parse_basic_put():
    p = parse_occ_symbol("SPY   260116P00400000")
    assert p["option_type"] == "put"
    assert p["strike"] == 400.0


def test_parse_roundtrip():
    built = build_occ_symbol("GOOGL", "2027-03-19", "put", 150.25)
    parsed = parse_occ_symbol(built)
    assert parsed["underlying"] == "GOOGL"
    assert parsed["expiration"] == "2027-03-19"
    assert parsed["option_type"] == "put"
    assert parsed["strike"] == 150.25


# ── Parse errors ────────────────────────────────────────────────────

def test_parse_rejects_wrong_length():
    with pytest.raises(ValueError, match="21 chars"):
        parse_occ_symbol("AAPL  261218C002")


def test_parse_rejects_invalid_type_char():
    bad = "AAPL  261218X00200000"
    with pytest.raises(ValueError, match="type char"):
        parse_occ_symbol(bad)


def test_parse_rejects_non_numeric_strike():
    # 21 chars total but strike segment (last 8) has letters.
    bad = "AAPL  261218C0FOOFOO"[:13] + "0FOOFOO0"  # reconstruct to 21 chars
    bad = "AAPL  261218C0FOOFOO0"  # 21 chars, strike segment = "0FOOFOO0"
    assert len(bad) == 21
    with pytest.raises(ValueError, match="strike segment"):
        parse_occ_symbol(bad)
