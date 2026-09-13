"""Tests for services.alpha_exit_router — position-aware exit classifier."""
from __future__ import annotations

from services.alpha_exit_router import classify


# ── Explicit action overrides ───────────────────────────────────────

def test_explicit_sell_to_close_with_long_position():
    r = classify(
        direction="SELL", intent_action="SELL_TO_CLOSE",
        broker_position_side="long", broker_position_qty=1.5,
    )
    assert r.kind == "close_long"
    assert r.reason == "explicit_sell_to_close"
    assert r.close_qty == 1.5


def test_explicit_sell_to_close_with_no_position_is_noop():
    r = classify(
        direction="SELL", intent_action="SELL_TO_CLOSE",
        broker_position_side=None, broker_position_qty=0,
    )
    assert r.kind == "no_op"
    assert r.reason == "no_long_to_close"


def test_explicit_buy_to_cover_with_short_position():
    r = classify(
        direction="BUY", intent_action="BUY_TO_COVER",
        broker_position_side="short", broker_position_qty=2.0,
    )
    assert r.kind == "close_short"
    assert r.reason == "explicit_buy_to_cover"
    assert r.close_qty == 2.0


def test_explicit_buy_to_cover_with_long_is_noop():
    # Guard: explicit BUY_TO_COVER must never create a new long even when
    # we happen to already hold a long — the caller thought they were
    # covering a short that isn't there.
    r = classify(
        direction="BUY", intent_action="BUY_TO_COVER",
        broker_position_side="long", broker_position_qty=5,
    )
    assert r.kind == "no_op"
    assert r.reason == "no_short_to_cover"


def test_explicit_open_long_blocked_when_short_open():
    r = classify(
        direction="BUY", intent_action="OPEN_LONG",
        broker_position_side="short", broker_position_qty=2.0,
    )
    assert r.kind == "no_op"
    assert r.reason == "ambiguous_reverse_short_open"


def test_explicit_open_short_blocked_when_long_open():
    r = classify(
        direction="SHORT", intent_action="OPEN_SHORT",
        broker_position_side="long", broker_position_qty=5.0,
    )
    assert r.kind == "no_op"
    assert r.reason == "ambiguous_reverse_long_open"


# ── Direction inference (no explicit action) ────────────────────────

def test_buy_with_open_short_covers_never_opens_new_long():
    """The critical safety property from the P1-B spec: a BUY signal
    on an existing short must never accidentally create a new long."""
    r = classify(
        direction="BUY", broker_position_side="short", broker_position_qty=3.0,
    )
    assert r.kind == "close_short"
    assert r.reason == "buy_covers_short"
    assert r.close_qty == 3.0


def test_sell_with_open_long_closes_never_opens_new_short():
    """Symmetric safety: SELL on an existing long must close, not
    accidentally become a fresh SELL_SHORT."""
    r = classify(
        direction="SELL", broker_position_side="long", broker_position_qty=2.3,
    )
    assert r.kind == "close_long"
    assert r.reason == "sell_closes_long"
    assert r.close_qty == 2.3


def test_buy_with_no_position_opens_long():
    r = classify(direction="BUY", broker_position_side=None, broker_position_qty=0)
    assert r.kind == "open_long"
    assert r.reason == "buy_opens_long"


def test_sell_with_no_position_flags_short_candidate():
    # No long to close and no exit_only → the SELL is a short-open
    # candidate. The executor's short-capability gate decides whether to
    # honor it. The router itself is short-agnostic.
    r = classify(direction="SELL", broker_position_side=None, broker_position_qty=0)
    assert r.kind == "open_short"
    assert r.reason == "sell_opens_short_candidate"


def test_short_direction_with_long_is_noop():
    r = classify(
        direction="SHORT", broker_position_side="long", broker_position_qty=1.0,
    )
    assert r.kind == "no_op"
    assert r.reason == "ambiguous_short_signal_on_long"


# ── exit_only guard ─────────────────────────────────────────────────

def test_exit_only_buy_without_short_is_noop():
    """exit_only=True must never open a new position, even for BUY."""
    r = classify(
        direction="BUY", exit_only=True,
        broker_position_side=None, broker_position_qty=0,
    )
    assert r.kind == "no_op"
    assert r.reason == "exit_only_no_short_to_cover"


def test_exit_only_sell_without_long_is_noop():
    r = classify(
        direction="SELL", exit_only=True,
        broker_position_side=None, broker_position_qty=0,
    )
    assert r.kind == "no_op"
    assert r.reason == "exit_only_no_long_to_close"


def test_exit_only_buy_with_short_still_covers():
    """exit_only doesn't block a legitimate cover — it just prevents
    the accidental new-position path."""
    r = classify(
        direction="BUY", exit_only=True,
        broker_position_side="short", broker_position_qty=4.0,
    )
    assert r.kind == "close_short"
    assert r.close_qty == 4.0


# ── Non-directional / bad inputs ────────────────────────────────────

def test_empty_direction_is_noop():
    r = classify(direction="", broker_position_side=None, broker_position_qty=0)
    assert r.kind == "no_op"
    assert r.reason == "non_directional"


def test_unknown_direction_is_noop():
    r = classify(direction="MAYBE", broker_position_side=None, broker_position_qty=0)
    assert r.kind == "no_op"
    assert r.reason == "non_directional"


def test_zero_qty_position_treated_as_no_position():
    # If the broker returned side=long but qty=0 (stale row), we must
    # not treat that as an actionable long.
    r = classify(direction="SELL", broker_position_side="long", broker_position_qty=0)
    assert r.kind == "open_short"  # falls through to short-candidate path
    assert r.reason == "sell_opens_short_candidate"


def test_negative_qty_normalized_to_absolute():
    # Some broker adapters report shorts as qty=-3. Normalize to abs.
    r = classify(direction="BUY", broker_position_side="short", broker_position_qty=-3.0)
    assert r.kind == "close_short"
    assert r.close_qty == 3.0
