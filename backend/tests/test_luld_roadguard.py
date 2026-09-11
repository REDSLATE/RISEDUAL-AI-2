"""LULD RoadGuard — narrow safety gate for equity execution.

Locked-in behaviour:

* Explicit halt / reopening signals BLOCK.
* Derived LULD proximity BLOCKS only with an explicit reference price
  and tier (or explicit bands).
* Unknown state FAILS OPEN by default (does NOT recreate the
  overblocking regression). ``strict=True`` opts into fail-closed.
* Enforcement toggle honours ``LULD_ROADGUARD_ENFORCE`` env.
"""
from __future__ import annotations

import pytest

from services.luld_roadguard import check_luld, _band_pct_for


# ── Band-tier math ─────────────────────────────────────────────────

@pytest.mark.parametrize("price,tier,expected", [
    (100.0, "tier1", 0.05),
    (100.0, "sp500", 0.05),
    (100.0, "tier2", 0.10),
    (100.0, "", 0.10),           # unknown tier → tier 2 conservative
    (2.50, "tier1", 0.20),       # sub-$3 always 20% regardless of tier
    (2.50, "tier2", 0.20),
    (0.50, "tier2", 0.30),       # $0.50: min(0.75, 0.15/0.50) = 0.30
])
def test_band_pct_matches_reg_nms(price, tier, expected):
    assert _band_pct_for(price, tier) == pytest.approx(expected, rel=1e-3)


# ── Explicit halt / reopening ─────────────────────────────────────

def test_halt_status_blocks():
    v = check_luld(symbol="AAPL", mark_price=180.0,
                   context={"halt_status": "halted"})
    assert v.allowed is False
    assert v.source == "halt"
    assert "HALTED" in (v.reason or "")


def test_trading_halt_alias_blocks():
    v = check_luld(symbol="AAPL", mark_price=180.0,
                   context={"halt_status": "trading_halt"})
    assert v.allowed is False
    assert v.source == "halt"


def test_reopening_blocks():
    v = check_luld(symbol="AAPL", mark_price=180.0,
                   context={"reopening": True})
    assert v.allowed is False
    assert v.source == "reopening"


def test_resumed_does_not_block():
    v = check_luld(symbol="AAPL", mark_price=180.0,
                   context={"halt_status": "resumed"})
    assert v.allowed is True
    assert v.source == "clear"


# ── Derived proximity — with explicit reference price ─────────────

def test_upper_proximity_blocks_tier1_5pct():
    # Ref=$100, tier1 → band ±5% → upper=$105, buffer=0.5% → 104.475
    v = check_luld(
        symbol="AAPL", mark_price=104.60,
        context={"reference_price": 100.0, "luld_tier": "tier1"},
    )
    assert v.allowed is False
    assert v.source == "proximity"
    assert "UPPER" in (v.reason or "")


def test_lower_proximity_blocks_tier1_5pct():
    v = check_luld(
        symbol="AAPL", mark_price=95.40,
        context={"reference_price": 100.0, "luld_tier": "tier1"},
    )
    assert v.allowed is False
    assert "LOWER" in (v.reason or "")


def test_within_band_but_outside_buffer_allows():
    # $103 is inside the 5% band (105) and outside the 0.5% buffer (104.475).
    v = check_luld(
        symbol="AAPL", mark_price=103.0,
        context={"reference_price": 100.0, "luld_tier": "tier1"},
    )
    assert v.allowed is True


def test_tier2_wider_band_lets_bigger_move_through():
    # Same $107 mark vs $100 ref: tier1 blocks, tier2 (10% band) allows.
    v1 = check_luld(
        symbol="X", mark_price=107.0,
        context={"reference_price": 100.0, "luld_tier": "tier1"},
    )
    v2 = check_luld(
        symbol="X", mark_price=107.0,
        context={"reference_price": 100.0, "luld_tier": "tier2"},
    )
    assert v1.allowed is False
    assert v2.allowed is True


def test_explicit_bands_override_derived():
    # Explicit band_high overrides derived — mark inside the derived
    # tier2 band but past the tight explicit upper band → block.
    v = check_luld(
        symbol="X", mark_price=102.0,
        context={
            "reference_price": 100.0, "luld_tier": "tier2",
            "band_high": 101.0, "band_low": 99.0,
        },
    )
    assert v.allowed is False


# ── Unknown state — narrow behaviour ──────────────────────────────

def test_unknown_state_fails_open_by_default():
    """The critical anti-overblocking guarantee."""
    v = check_luld(symbol="AAPL", mark_price=180.0)
    assert v.allowed is True
    assert v.source == "clear"


def test_no_context_no_block_regardless_of_price_size():
    """Even a huge move percentage means nothing without a reference."""
    v = check_luld(symbol="AAPL", mark_price=1_000_000.0)
    assert v.allowed is True


def test_strict_mode_fails_closed_on_unknown():
    v = check_luld(symbol="AAPL", mark_price=180.0, strict=True)
    assert v.allowed is False
    assert v.source == "unknown"


def test_strict_mode_does_not_override_explicit_bands():
    """Strict only kicks in when LULD data is missing."""
    v = check_luld(
        symbol="AAPL", mark_price=101.0,
        context={"reference_price": 100.0, "luld_tier": "tier2"},
        strict=True,
    )
    assert v.allowed is True


# ── Enforce toggle ────────────────────────────────────────────────

def test_shadow_mode_never_enforces(monkeypatch):
    monkeypatch.setenv("LULD_ROADGUARD_ENFORCE", "0")
    v = check_luld(symbol="AAPL", mark_price=180.0,
                   context={"halt_status": "halted"})
    # Verdict still records block reason but enforce=False.
    assert v.allowed is False
    assert v.enforce is False


def test_enforce_default_is_true():
    v = check_luld(symbol="AAPL", mark_price=180.0,
                   context={"halt_status": "halted"})
    assert v.enforce is True


# ── Bad input safety ──────────────────────────────────────────────

def test_none_mark_with_halt_still_blocks():
    v = check_luld(symbol="AAPL", mark_price=None,
                   context={"halt_status": "halted"})
    assert v.allowed is False


def test_none_mark_no_context_allows():
    v = check_luld(symbol="AAPL", mark_price=None)
    assert v.allowed is True


def test_bad_reference_price_type_is_safe():
    v = check_luld(
        symbol="AAPL", mark_price=100.0,
        context={"reference_price": "not-a-number", "luld_tier": "tier1"},
    )
    assert v.allowed is True  # Fails open on bad numeric input


def test_zero_reference_price_is_safe():
    v = check_luld(
        symbol="AAPL", mark_price=100.0,
        context={"reference_price": 0.0, "luld_tier": "tier1"},
    )
    assert v.allowed is True


# ── Verdict shape ─────────────────────────────────────────────────

def test_verdict_as_dict_shape():
    v = check_luld(symbol="AAPL", mark_price=180.0,
                   context={"halt_status": "halted"})
    d = v.as_dict()
    assert set(d.keys()) == {"allowed", "reason", "source", "band_high", "band_low", "enforce"}
