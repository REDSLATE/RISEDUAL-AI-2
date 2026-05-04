"""
Tests for the Phase-2 EXIT (hard SL/TP breach) layer in
``services.terminal_aggregator``.

Pins:
1. ``_hard_exit_trigger`` pure-function correctness — LONG SL
   breach, LONG TP hit, SHORT SL breach, SHORT TP hit, all
   symmetrical; zero price returns None; no SL/TP returns None.
2. ``_build_hard_exit_cards`` emits one EXIT card per breaching
   position, priority 0.99, tier="hard", with the trigger type
   (``sl_breach`` / ``tp_hit``) on the card.
3. Broken price fetcher degrades silently — returns [] rather than
   suppressing the rest of the top-actions response.
4. Integration — a position with a breached SL fires a hard-EXIT
   card that:
     a. Ranks above sovereign-reversal EXIT cards (0.99 > 0.95).
     b. Suppresses any duplicate orphan MANAGE card for the same
        symbol.
     c. Prevents a second (reversal) EXIT card from being emitted
        for the same symbol even when the sovereign flipped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_top_universe_service import _FakeDB


def _fixed_now() -> datetime:
    return datetime(2026, 5, 12, 16, 0, tzinfo=timezone.utc)


# ── Pure-function hard-exit trigger classifier ──────────────────────


@pytest.mark.parametrize(
    "direction,price,sl,tp,expected_trigger",
    [
        ("LONG",  95.0, 100.0, 120.0, "sl_breach"),
        ("LONG", 100.0, 100.0, 120.0, "sl_breach"),   # == SL counts
        ("LONG", 120.0, 100.0, 120.0, "tp_hit"),      # == TP counts
        ("LONG", 125.0, 100.0, 120.0, "tp_hit"),
        ("LONG", 110.0, 100.0, 120.0, None),          # in range
        ("SHORT", 105.0, 100.0, 80.0, "sl_breach"),
        ("SHORT", 100.0, 100.0, 80.0, "sl_breach"),
        ("SHORT",  80.0, 100.0, 80.0, "tp_hit"),
        ("SHORT",  75.0, 100.0, 80.0, "tp_hit"),
        ("SHORT",  90.0, 100.0, 80.0, None),
    ],
)
def test_hard_exit_trigger_classification(
    direction, price, sl, tp, expected_trigger,
):
    from services.terminal_aggregator import _hard_exit_trigger

    out = _hard_exit_trigger(
        direction=direction, price=price,
        stop_loss=sl, take_profit=tp,
    )
    if expected_trigger is None:
        assert out is None
    else:
        assert out is not None
        trigger, reason = out
        assert trigger == expected_trigger
        assert f"${price:.2f}" in reason


def test_hard_exit_trigger_zero_price_short_circuits():
    from services.terminal_aggregator import _hard_exit_trigger

    assert _hard_exit_trigger(
        direction="LONG", price=0.0, stop_loss=100.0, take_profit=120.0,
    ) is None


def test_hard_exit_trigger_missing_sl_and_tp_returns_none():
    from services.terminal_aggregator import _hard_exit_trigger

    assert _hard_exit_trigger(
        direction="LONG", price=100.0, stop_loss=None, take_profit=None,
    ) is None


# ── _build_hard_exit_cards ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_hard_exit_cards_emits_one_per_breach():
    from services.terminal_aggregator import (
        TerminalContext, _build_hard_exit_cards,
    )

    db = _FakeDB()
    ctx = TerminalContext(db=db, now=_fixed_now())
    open_positions = {
        "NVDA": {
            "ticker": "NVDA",
            "direction": "LONG",
            "stop_loss": 100.0,
            "take_profit": 120.0,
            "asset_class": "equity",
            "entry_price": 105.0,
        },
        "BTC": {
            "symbol": "BTC",
            "direction": "LONG",
            "stop_loss": 40000.0,
            "take_profit": 60000.0,
            "asset_class": "crypto",
            "entry_price": 50000.0,
        },
        "MSFT": {
            "ticker": "MSFT",
            "direction": "LONG",
            "stop_loss": 200.0,
            "take_profit": 250.0,
            "asset_class": "equity",
            "entry_price": 210.0,  # in range, no breach
        },
    }
    prices = {"NVDA": 95.0, "BTC": 65000.0, "MSFT": 220.0}

    async def fake_price(symbol):
        return {"price": prices.get(symbol, 0.0)}

    cards = await _build_hard_exit_cards(
        ctx, open_positions, price_fetcher=fake_price,
    )
    by_sym = {c["symbol"]: c for c in cards}
    assert "NVDA" in by_sym  # SL breach (95 ≤ 100)
    assert "BTC" in by_sym   # TP hit   (65000 ≥ 60000)
    assert "MSFT" not in by_sym  # in range
    assert by_sym["NVDA"]["trigger"] == "sl_breach"
    assert by_sym["BTC"]["trigger"] == "tp_hit"
    for c in cards:
        assert c["kind"] == "EXIT"
        assert c["priority_score"] == 0.99
        assert c["conviction"]["tier"] == "hard"


@pytest.mark.asyncio
async def test_build_hard_exit_cards_broken_price_fetcher_degrades():
    from services.terminal_aggregator import (
        TerminalContext, _build_hard_exit_cards,
    )

    db = _FakeDB()
    ctx = TerminalContext(db=db, now=_fixed_now())
    open_positions = {
        "NVDA": {
            "ticker": "NVDA",
            "direction": "LONG",
            "stop_loss": 100.0,
            "take_profit": 120.0,
        },
    }

    async def broken_price(_symbol):
        raise RuntimeError("provider down")

    # Must not raise — must return an empty list so the rest of the
    # top-actions response keeps working even when the live-price
    # hop is dead.
    cards = await _build_hard_exit_cards(
        ctx, open_positions, price_fetcher=broken_price,
    )
    assert cards == []


@pytest.mark.asyncio
async def test_build_hard_exit_cards_no_sl_or_tp_skipped():
    from services.terminal_aggregator import (
        TerminalContext, _build_hard_exit_cards,
    )

    db = _FakeDB()
    ctx = TerminalContext(db=db, now=_fixed_now())
    open_positions = {
        "NOPE": {
            "ticker": "NOPE",
            "direction": "LONG",
            # No stop_loss, no take_profit — skipped.
        },
    }

    async def fake_price(_symbol):
        return {"price": 50.0}

    cards = await _build_hard_exit_cards(
        ctx, open_positions, price_fetcher=fake_price,
    )
    assert cards == []


# ── Integration — hard EXIT ranks above reversal EXIT ──────────────
#
# The integration path uses ``get_top_actions`` with a monkeypatched
# price fetcher because that function imports the real provider
# lazily. We patch at the module level.


@pytest.mark.asyncio
async def test_hard_exit_outranks_reversal_exit_and_suppresses_duplicates(
    monkeypatch,
):
    from services.terminal_aggregator import TerminalContext, get_top_actions
    import services.terminal_aggregator as ta

    db = _FakeDB()

    # Sovereign flipped to SHORT at high conviction on NVDA (would
    # normally fire a reversal-EXIT at 0.95). But live price has ALSO
    # breached the SL — hard EXIT must win and suppress the reversal.
    db["sovereign_decisions"].docs.append({
        "symbol": "NVDA",
        "asset_type": "equity",
        "action": "SHORT",
        "confidence": 0.85,
        "conviction_tier": "high",
        "size_multiplier": 0.6,
        "vetoes": [],
        "created_at": _fixed_now() - timedelta(minutes=5),
        "decision_id": "nvda-rev",
        "shadow": True,
    })
    db["paper_trades"].docs.append({
        "trade_id": "pt-nvda",
        "ticker": "NVDA",
        "user_id": "user-mix",
        "status": "open",
        "direction": "LONG",
        "stop_loss": 100.0,
        "take_profit": 120.0,
        "entry_price": 108.0,
    })

    # Inject a price fetcher that reports an SL breach.
    async def fake_price(_symbol):
        return {"price": 95.0}

    # Patch the price_fetcher default by replacing the whole helper
    # to use the fake price fetcher.
    orig_build = ta._build_hard_exit_cards

    async def patched_build(ctx, open_positions, price_fetcher=None):
        return await orig_build(
            ctx, open_positions, price_fetcher=fake_price,
        )
    monkeypatch.setattr(ta, "_build_hard_exit_cards", patched_build)

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-mix", limit=10,
    )
    nvda_cards = [a for a in out["actions"] if a["symbol"] == "NVDA"]
    # Exactly one NVDA card — the hard EXIT. Reversal-EXIT suppressed.
    assert len(nvda_cards) == 1
    card = nvda_cards[0]
    assert card["kind"] == "EXIT"
    assert card["priority_score"] == 0.99
    assert card["conviction"]["tier"] == "hard"
    assert card["rank"] == 1
    assert "Stop-loss breached" in card["reason"]
    assert out["totals"]["exit"] == 1


@pytest.mark.asyncio
async def test_hard_exit_suppresses_orphan_manage_card(monkeypatch):
    from services.terminal_aggregator import TerminalContext, get_top_actions
    import services.terminal_aggregator as ta

    db = _FakeDB()
    # Position with SL breach, no sovereign decision.
    db["paper_trades"].docs.append({
        "trade_id": "pt-orphan",
        "ticker": "TSLA",
        "user_id": "user-orphan",
        "status": "open",
        "direction": "LONG",
        "stop_loss": 200.0,
        "take_profit": 260.0,
        "entry_price": 230.0,
    })

    async def fake_price(_symbol):
        return {"price": 180.0}  # below SL

    orig_build = ta._build_hard_exit_cards

    async def patched_build(ctx, open_positions, price_fetcher=None):
        return await orig_build(
            ctx, open_positions, price_fetcher=fake_price,
        )
    monkeypatch.setattr(ta, "_build_hard_exit_cards", patched_build)

    out = await get_top_actions(
        TerminalContext(db=db, now=_fixed_now()),
        user_id="user-orphan", limit=10,
    )
    tsla_cards = [a for a in out["actions"] if a["symbol"] == "TSLA"]
    # Exactly one TSLA card — the hard EXIT. No orphan MANAGE card.
    assert len(tsla_cards) == 1
    assert tsla_cards[0]["kind"] == "EXIT"
    assert tsla_cards[0]["priority_score"] == 0.99
