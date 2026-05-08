"""Tests for ``services.post_trade_autopsy``.

Critical invariants pinned here:

1. ``build_post_trade_autopsy`` is a PURE function — never mutates
   the input dict (deep-copy invariant).
2. Every reason code in the taxonomy is reachable.
3. Unknown close reasons degrade to ``UNKNOWN_{WIN|LOSS|FLAT}``.
4. Conviction + integrity + adversarial annotations compose on top
   of the primary exit-reason code (they don't replace it).
"""
from __future__ import annotations

import copy

import pytest

from services.post_trade_autopsy import (
    HIGH_CONVICTION_FLOOR,
    LOW_CONVICTION_CEILING,
    build_post_trade_autopsy,
)


# ── Purity invariant ───────────────────────────────────────────────


def test_autopsy_never_mutates_input():
    doc = {
        "symbol": "BTC", "direction": "LONG", "pnl": 123.45,
        "close_reason": "take_profit", "confidence": 0.81,
    }
    before = copy.deepcopy(doc)
    _ = build_post_trade_autopsy(doc)
    assert doc == before


def test_autopsy_handles_empty_input():
    """Partial / empty documents must degrade gracefully, never raise."""
    out = build_post_trade_autopsy({})
    assert isinstance(out["summary"], str)
    assert isinstance(out["reason_codes"], list)
    # No pnl info → flat outcome → unknown exit dispatch.
    assert "UNKNOWN_FLAT" in out["reason_codes"]


def test_autopsy_handles_none_input():
    out = build_post_trade_autopsy(None)  # type: ignore[arg-type]
    assert isinstance(out["reason_codes"], list)


# ── Primary exit-reason dispatch ───────────────────────────────────


def test_take_profit_emits_win_took_profit():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 50.0,
        "close_reason": "take_profit",
    })
    assert out["reason_codes"][0] == "WIN_TOOK_PROFIT"
    assert out["meta"]["outcome"] == "win"


def test_stop_loss_emits_loss_stopped_out():
    out = build_post_trade_autopsy({
        "direction": "SHORT", "pnl_usd": -80.0,
        "close_reason": "stop_loss",
    })
    assert out["reason_codes"][0] == "LOSS_STOPPED_OUT"
    assert out["meta"]["outcome"] == "loss"


def test_day_trade_eod_timer():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -10.0,
        "close_reason": "day_trade_eod_timer",
    })
    assert out["reason_codes"][0] == "TIMED_OUT_DAY_TRADE"


def test_hard_exit_signs_by_outcome():
    win = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 40.0, "close_reason": "hard_exit",
    })
    loss = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -40.0, "close_reason": "hard_exit",
    })
    assert win["reason_codes"][0] == "WIN_HARD_EXIT"
    assert loss["reason_codes"][0] == "LOSS_HARD_EXIT"


def test_hold_window_signs_by_outcome():
    win = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 5.0,
        "close_reason": "hold_window_24h",
    })
    loss = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -5.0,
        "close_reason": "hold_window_expired",
    })
    assert win["reason_codes"][0] == "WIN_HOLD_EXPIRED"
    assert loss["reason_codes"][0] == "LOSS_HOLD_EXPIRED"


def test_unknown_close_reason_still_labels_outcome():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 7.0,
        "close_reason": "weirdo_provider_reason",
    })
    assert out["reason_codes"][0] == "UNKNOWN_WIN"


# ── Conviction annotations ─────────────────────────────────────────


def test_high_conviction_win_appended():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 30.0,
        "close_reason": "take_profit",
        "confidence": HIGH_CONVICTION_FLOOR + 0.01,
    })
    assert "HIGH_CONVICTION_WIN" in out["reason_codes"]
    assert out["reason_codes"][0] == "WIN_TOOK_PROFIT"  # primary unchanged


def test_high_conviction_loss_appended():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -30.0,
        "close_reason": "stop_loss",
        "confidence": HIGH_CONVICTION_FLOOR + 0.05,
    })
    assert "HIGH_CONVICTION_LOSS" in out["reason_codes"]


def test_low_conviction_loss_appended():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -10.0,
        "close_reason": "stop_loss",
        "confidence": LOW_CONVICTION_CEILING - 0.05,
    })
    assert "LOW_CONVICTION_LOSS" in out["reason_codes"]


def test_mid_conviction_no_annotation():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 10.0,
        "close_reason": "take_profit",
        "confidence": 0.72,  # between LOW_CEILING and HIGH_FLOOR
    })
    # Only the primary code should be there, no conviction annotation.
    assert out["reason_codes"] == ["WIN_TOOK_PROFIT"]


def test_flat_outcome_no_conviction_annotation():
    """Flat (pnl=0) trades aren't won or lost — skip the annotation."""
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 0.0,
        "close_reason": "take_profit",
        "confidence": 0.95,
    })
    assert not any(
        "HIGH_CONVICTION" in c or "LOW_CONVICTION" in c
        for c in out["reason_codes"]
    )


# ── Integrity + adversarial annotations ───────────────────────────


def test_integrity_throttle_annotation():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -20.0,
        "close_reason": "stop_loss",
        "risk_multiplier_at_entry": 0.5,
    })
    assert "INTEGRITY_THROTTLE_WAS_ACTIVE" in out["reason_codes"]


def test_integrity_not_throttled_no_annotation():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 10.0,
        "close_reason": "take_profit",
        "risk_multiplier_at_entry": 1.0,
    })
    assert "INTEGRITY_THROTTLE_WAS_ACTIVE" not in out["reason_codes"]


def test_adversarial_disagree_on_long_trade():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -10.0,
        "close_reason": "stop_loss",
        "adversarial_decision": "SHORT_OR_AVOID",
    })
    assert "ADVERSARIAL_DISAGREED" in out["reason_codes"]


def test_adversarial_agree_no_annotation():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 10.0,
        "close_reason": "take_profit",
        "adversarial_decision": "LONG",
    })
    assert "ADVERSARIAL_DISAGREED" not in out["reason_codes"]


# ── Meta fields ─────────────────────────────────────────────────────


def test_meta_captures_numeric_inputs():
    out = build_post_trade_autopsy({
        "ticker": "AAPL", "direction": "LONG",
        "pnl_usd": 12.34, "pnl_pct": 0.025, "r_multiple": 1.5,
        "confidence": 0.83, "close_reason": "take_profit",
    })
    meta = out["meta"]
    assert meta["symbol"] == "AAPL"
    assert meta["direction"] == "LONG"
    assert meta["pnl"] == 12.34
    assert meta["pnl_pct"] == 0.025
    assert meta["r_multiple"] == 1.5
    assert meta["entry_confidence"] == 0.83
    assert meta["close_reason"] == "take_profit"


def test_bullet_cases_populated_for_known_outcomes():
    win = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 50.0,
        "close_reason": "take_profit",
        "confidence": 0.85,
    })
    assert win["what_went_right"], "high-conviction TP should produce bullets"

    loss = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -50.0,
        "close_reason": "stop_loss",
        "confidence": 0.85,
    })
    assert loss["what_went_wrong"], "high-conviction SL should produce bullets"


# ── Slippage attribution block ───────────────────────────────────


def test_slippage_block_present_when_stamped():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 50.0,
        "close_reason": "take_profit",
        "confidence": 0.80,
        "shares": 10,
        "entry_price": 100.0,
        "slippage_bps": 50.0,
        "slippage_method": "ask_fill",
        "entry_quote_bid": 99.5,
        "entry_quote_ask": 100.5,
        "entry_quote_mid": 100.0,
        "quote_source": "alpaca",
    })
    s = out["slippage"]
    assert s is not None
    assert s["entry_bps"] == 50.0
    # 10 shares × $100 × 50bps = $5
    assert s["entry_dollar_cost"] == pytest.approx(5.0, abs=0.01)
    assert s["fill_method"] == "ask_fill"
    assert s["notional_usd"] == 1000.0
    assert s["quote_source"] == "alpaca"
    # No exit stamp on this row → exit_bps None, total = entry only.
    assert s["exit_bps"] is None
    assert s["total_bps"] == 50.0


def test_slippage_block_includes_exit_when_stamped():
    """Round-trip: both entry and exit slippage stamped → total
    correctly aggregates both sides."""
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 50.0,
        "close_reason": "take_profit",
        "shares": 10, "entry_price": 100.0,
        "slippage_bps": 50.0, "slippage_method": "ask_fill",
        "exit_slippage_bps": 30.0, "exit_slippage_method": "bid_fill",
        "exit_quote_bid": 105.0, "exit_quote_ask": 106.0,
        "exit_quote_mid": 105.5,
    })
    s = out["slippage"]
    assert s["entry_bps"] == 50.0
    assert s["exit_bps"] == 30.0
    assert s["exit_method"] == "bid_fill"
    # 10 × 100 × 30bps = $3 exit drag
    assert s["exit_dollar_cost"] == pytest.approx(3.0, abs=0.01)
    # Total = entry + exit = 80bps = $8 on $1000 notional
    assert s["total_bps"] == 80.0
    assert s["total_dollar_cost"] == pytest.approx(8.0, abs=0.01)


def test_slippage_block_exit_unstamped_keeps_entry_only():
    """Half-stamped row (pre-2026-05-04 close) — entry stamp lands,
    exit is None. Total = entry only, no fabricated exit number."""
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 50.0,
        "close_reason": "take_profit",
        "shares": 10, "entry_price": 100.0,
        "slippage_bps": 25.0, "slippage_method": "ask_fill",
        # No exit_slippage_* fields stamped.
    })
    s = out["slippage"]
    assert s["entry_bps"] == 25.0
    assert s["exit_bps"] is None
    assert s["exit_dollar_cost"] is None
    assert s["total_bps"] == 25.0


def test_slippage_block_none_when_not_stamped():
    """Legacy rows that predate the slippage stamp must report
    ``slippage=None`` rather than zero so the UI can render —."""
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 50.0,
        "close_reason": "take_profit",
    })
    assert out["slippage"] is None


def test_slippage_block_handles_crypto_position_size_field():
    """Crypto trades store ``position_size_usd`` instead of
    ``shares × entry_price``. Block should pick that up."""
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl": 50.0,
        "close_reason": "take_profit",
        "slippage_bps": 5.0,
        "slippage_method": "ask_fill",
        "position_size_usd": 500.0,
    })
    s = out["slippage"]
    assert s["notional_usd"] == 500.0
    # 500 × 5bps = $0.25
    assert s["entry_dollar_cost"] == pytest.approx(0.25, abs=0.01)


def test_slippage_loss_emits_loss_bullet():
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": -50.0,
        "close_reason": "stop_loss",
        "shares": 10, "entry_price": 100.0,
        "slippage_bps": 25.0, "slippage_method": "ask_fill",
    })
    assert any("slippage" in b.lower() for b in out["what_went_wrong"])


def test_slippage_zero_bps_no_bullet():
    """``mid_only`` path → 0 bps → no slippage bullet (but the
    block is still present so the UI can show "no spread cost")."""
    out = build_post_trade_autopsy({
        "direction": "LONG", "pnl_usd": 50.0,
        "close_reason": "take_profit",
        "shares": 10, "entry_price": 100.0,
        "slippage_bps": 0.0, "slippage_method": "mid_only",
    })
    assert out["slippage"]["entry_bps"] == 0.0
    assert not any("slippage" in b.lower() for b in out["what_went_right"])
    assert not any("slippage" in b.lower() for b in out["what_went_wrong"])
