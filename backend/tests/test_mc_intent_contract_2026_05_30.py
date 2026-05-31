"""Tests — MC brain-callable intent contract (2026-05-30).

Pins the schema additions documented in MC's brain-team handoff:
- ``target_price`` + ``stop_price`` fields on the wire body
- ``thesis`` accepted as an operator-facing alias for ``rationale``
- Validation rejects non-positive / non-finite price values
- ``_derive_price_targets`` produces sane defaults when only
  entry_price is present on the receipt
- ``_derive_price_targets`` returns empty dict (graceful skip) when
  no anchor exists — MC accepts intents without these fields
"""
from __future__ import annotations

import math

import pytest

from sovereign.mc_client import MCContractError, build_intent_body
from sovereign.intent_bridge import _derive_price_targets


# ── build_intent_body — wire schema ───────────────────────────────


def test_target_and_stop_price_land_on_wire():
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=1.0, confidence=0.74,
        stack="alpha", action="BUY", lane="equity",
        target_price=145.20, stop_price=138.00,
    )
    assert body["target_price"] == 145.20
    assert body["stop_price"] == 138.00


def test_thesis_alias_maps_to_rationale_on_wire():
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=1.0, confidence=0.7,
        stack="alpha", action="BUY", lane="equity",
        thesis="post-earnings continuation; 5:1 R:R w/ VWAP support",
    )
    assert "rationale" in body
    assert "VWAP support" in body["rationale"]
    # The spec name should NOT leak through as its own field — only
    # ``rationale`` lives on the wire (matches MC's existing schema).
    assert "thesis" not in body


def test_explicit_rationale_wins_over_thesis():
    """If both are provided, explicit ``rationale`` keeps priority."""
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=1.0, confidence=0.7,
        stack="alpha", action="BUY", lane="equity",
        rationale="primary thesis",
        thesis="should be ignored",
    )
    assert body["rationale"] == "primary thesis"


def test_zero_target_price_rejected():
    with pytest.raises(MCContractError, match="target_price"):
        build_intent_body(
            symbol="NVDA", side="BUY", qty=1.0, confidence=0.7,
            target_price=0.0,
        )


def test_negative_stop_price_rejected():
    with pytest.raises(MCContractError, match="stop_price"):
        build_intent_body(
            symbol="NVDA", side="BUY", qty=1.0, confidence=0.7,
            stop_price=-5.0,
        )


def test_nan_target_price_rejected():
    with pytest.raises(MCContractError, match="target_price"):
        build_intent_body(
            symbol="NVDA", side="BUY", qty=1.0, confidence=0.7,
            target_price=float("nan"),
        )


def test_inf_stop_price_rejected():
    with pytest.raises(MCContractError, match="stop_price"):
        build_intent_body(
            symbol="NVDA", side="BUY", qty=1.0, confidence=0.7,
            stop_price=float("inf"),
        )


def test_omitting_prices_does_not_inject_fields():
    """No target_price/stop_price → wire body must NOT include them.

    MC's gate chain accepts intents without these fields (only
    affects R:R scoring). Defaulting to garbage would mis-score
    every signal that didn't carry an explicit anchor."""
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=1.0, confidence=0.7,
        stack="alpha", action="BUY", lane="equity",
    )
    assert "target_price" not in body
    assert "stop_price" not in body


# ── _derive_price_targets — Alpha-side default derivation ─────────


def test_derive_uses_explicit_target_and_stop():
    receipt = {"target_price": 200.0, "stop_price": 180.0}
    out = _derive_price_targets(receipt, "BUY", 0.7)
    assert out == {"target_price": 200.0, "stop_price": 180.0}


def test_derive_falls_back_to_entry_price_for_long():
    receipt = {"entry_price": 100.0}
    out = _derive_price_targets(receipt, "BUY", 0.5)
    # conf=0.5 → band = 0.015 + 0.5*0.03 = 0.03 → target = 103.0
    # stop = 100 * (1 - 0.01) = 99.0
    assert out["target_price"] == pytest.approx(103.0, rel=1e-3)
    assert out["stop_price"] == pytest.approx(99.0, rel=1e-3)


def test_derive_inverts_for_short():
    receipt = {"entry_price": 100.0}
    out = _derive_price_targets(receipt, "SHORT", 0.5)
    # short: target = 100 * (1 - 0.03) = 97.0, stop = 100 * (1 + 0.01) = 101.0
    assert out["target_price"] == pytest.approx(97.0, rel=1e-3)
    assert out["stop_price"] == pytest.approx(101.0, rel=1e-3)


def test_derive_target_band_widens_with_confidence():
    """Higher conviction → wider target. Stop stays fixed-discipline."""
    lo = _derive_price_targets({"entry_price": 100.0}, "BUY", 0.1)
    hi = _derive_price_targets({"entry_price": 100.0}, "BUY", 0.9)
    assert hi["target_price"] > lo["target_price"]
    # Stop is conviction-independent (fixed 1% rule).
    assert lo["stop_price"] == pytest.approx(hi["stop_price"], rel=1e-6)


def test_derive_reads_from_snapshot_price_if_no_entry():
    receipt = {"snapshot": {"price": 142.05}}
    out = _derive_price_targets(receipt, "BUY", 0.6)
    assert "target_price" in out
    assert "stop_price" in out
    assert out["stop_price"] == pytest.approx(140.63, rel=1e-3)  # 142.05*0.99


def test_derive_returns_empty_when_no_anchor():
    """No entry_price, no snapshot.price → graceful skip."""
    assert _derive_price_targets({}, "BUY", 0.5) == {}


def test_derive_partial_explicit_filled_by_derivation():
    """Explicit target alone + entry_price → stop gets derived."""
    receipt = {"target_price": 200.0, "entry_price": 100.0}
    out = _derive_price_targets(receipt, "BUY", 0.5)
    assert out["target_price"] == 200.0          # explicit kept
    assert out["stop_price"] == pytest.approx(99.0, rel=1e-3)


def test_derive_rejects_bad_explicit_values():
    """NaN / zero / negative explicit values should fall through to
    derivation rather than poison the wire."""
    receipt = {"target_price": float("nan"), "stop_price": 0.0, "entry_price": 100.0}
    out = _derive_price_targets(receipt, "BUY", 0.5)
    # Bad explicit values dropped; derived from entry.
    assert math.isfinite(out["target_price"])
    assert out["target_price"] > 0
    assert math.isfinite(out["stop_price"])
    assert out["stop_price"] > 0
