"""
Tests pinning the Signal.from_dict contract after the complexity
refactor (2026-05-03).

Covers every loose-dict shape our scanner / dispatcher emits:
* direction: LONG / SHORT / long / short / BUY / SELL / buy / sell /
  STRONG_BUY / BULLISH / UP / WEAK_SELL / BEARISH / DOWN
* unknown verdict token → SHORT fallback (with log warning)
* confidence: 0.0-1.0 float / 0-100 percentage / missing / legacy
  ``ai_confidence`` fallback
* symbol ↔ asset alias, price ↔ entry alias, sl/tp ↔ stop_loss/take_profit
* timestamp injection + default
"""
from __future__ import annotations

from ai_core.models import Signal


# ─── Direction parser ────────────────────────────────────────────────────────


def test_from_dict_long_direction_preserved():
    s = Signal.from_dict({
        "asset": "AAPL", "direction": "LONG",
        "entry": 100, "stop_loss": 95, "take_profit": 110,
    })
    assert s.direction == "LONG"


def test_from_dict_short_direction_preserved():
    s = Signal.from_dict({
        "asset": "AAPL", "direction": "SHORT",
        "entry": 100, "stop_loss": 105, "take_profit": 90,
    })
    assert s.direction == "SHORT"


def test_from_dict_lowercase_direction_normalized():
    s = Signal.from_dict({
        "asset": "X", "direction": "long",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.direction == "LONG"


def test_from_dict_uses_ai_verdict_when_direction_missing():
    # BUY → LONG via canonicaliser
    s = Signal.from_dict({
        "asset": "X", "ai_verdict": "BUY",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.direction == "LONG"


def test_from_dict_uses_side_when_direction_missing():
    s = Signal.from_dict({
        "asset": "X", "side": "sell",
        "entry": 1, "stop_loss": 1.1, "take_profit": 0.9,
    })
    assert s.direction == "SHORT"


def test_from_dict_bullish_token_maps_to_long():
    """Regression pin: pre-2026-05-01 this silently mapped to SHORT."""
    s = Signal.from_dict({
        "asset": "X", "ai_verdict": "BULLISH",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.direction == "LONG"


def test_from_dict_weak_buy_maps_to_long():
    s = Signal.from_dict({
        "asset": "X", "ai_verdict": "WEAK_BUY",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.direction == "LONG"


def test_from_dict_bearish_maps_to_short():
    s = Signal.from_dict({
        "asset": "X", "ai_verdict": "BEARISH",
        "entry": 1, "stop_loss": 1.1, "take_profit": 0.9,
    })
    assert s.direction == "SHORT"


def test_from_dict_unknown_verdict_falls_back_to_short_with_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        s = Signal.from_dict({
            "asset": "X", "ai_verdict": "SOMETHING_WEIRD",
            "entry": 1, "stop_loss": 1, "take_profit": 1,
        })
    assert s.direction == "SHORT"
    assert any("unknown verdict token" in rec.message for rec in caplog.records)


# ─── Confidence parser ────────────────────────────────────────────────────────


def test_from_dict_confidence_0_1_float_preserved():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG", "confidence": 0.75,
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.confidence == 0.75


def test_from_dict_confidence_0_100_percentage_normalized():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG", "confidence": 85,
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.confidence == 0.85


def test_from_dict_confidence_missing_defaults_to_zero():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.confidence == 0.0


def test_from_dict_ai_confidence_fallback():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG", "ai_confidence": 72,
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.confidence == 0.72


def test_from_dict_confidence_exactly_1_stays_1():
    """Edge: a raw 1.0 is a valid normalized confidence, not 1%."""
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG", "confidence": 1.0,
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.confidence == 1.0


def test_from_dict_confidence_zero_handled():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG", "confidence": 0,
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.confidence == 0.0


# ─── Aliases ────────────────────────────────────────────────────────────────


def test_from_dict_symbol_alias_for_asset():
    s = Signal.from_dict({
        "symbol": "AAPL", "direction": "LONG",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.asset == "AAPL"


def test_from_dict_price_alias_for_entry():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG", "price": 100,
        "stop_loss": 95, "take_profit": 110,
    })
    assert s.entry == 100


def test_from_dict_sl_tp_aliases():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG", "entry": 100,
        "sl": 95, "tp": 110,
    })
    assert s.stop_loss == 95
    assert s.take_profit == 110


def test_from_dict_asset_uppercased():
    s = Signal.from_dict({
        "asset": "aapl", "direction": "LONG",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    assert s.asset == "AAPL"


def test_from_dict_timestamp_default_is_iso():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
    })
    # ISO format has 'T' separator
    assert "T" in s.timestamp


def test_from_dict_timestamp_explicit_preserved():
    s = Signal.from_dict({
        "asset": "X", "direction": "LONG",
        "entry": 1, "stop_loss": 0.9, "take_profit": 1.1,
        "timestamp": "2026-05-03T12:00:00+00:00",
    })
    assert s.timestamp == "2026-05-03T12:00:00+00:00"


# ─── Helper method isolation ────────────────────────────────────────────────


def test_parse_direction_helper_is_pure():
    """_parse_direction is a static method — callable without instance."""
    assert Signal._parse_direction({"direction": "LONG"}) == "LONG"
    assert Signal._parse_direction({"ai_verdict": "BUY"}) == "LONG"


def test_parse_confidence_helper_is_pure():
    """_parse_confidence is a static method — callable without instance."""
    assert Signal._parse_confidence({"confidence": 0.5}) == 0.5
    assert Signal._parse_confidence({"ai_confidence": 75}) == 0.75
    assert Signal._parse_confidence({}) == 0.0
