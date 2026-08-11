"""Tests for services.alpha_day_trader (Phase A+B).

Focus:
- Ranker: scoring monotonicity + tradability weight
- Pattern engine: HOD_BREAK / VWAP_RECLAIM / BREAKOUT detection
- Trigger watcher: freezes confirmation_price on crossover, invalidates below stop
- Level2Confirmation: None → 0.50 (never blocks)
- create_alpha_intent: uses the setup + neutral L2 correctly
- Dedup key: same market move → one setup_id
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.alpha_day_trader import (
    ActiveSetup,
    AlphaOpportunityScanner,
    AlphaPatternEngine,
    AlphaTriggerWatcher,
    Level2Confirmation,
    Level2Snapshot,
    MarketSnapshot,
    SetupState,
    SetupType,
    _setup_dedup_key,
    create_alpha_intent,
)


def _snap(**overrides) -> MarketSnapshot:
    base = dict(
        symbol="XYZ",
        price=100.0,
        volume=1_000_000,
        avg_volume=500_000,
        relative_volume=2.0,
        open_price=98.0,
        high=101.0,
        low=97.5,
        vwap=99.0,
        bid=99.99,
        ask=100.01,
        spread_bps=1.0,
        pct_change=2.0,
        volume_acceleration=1.4,
        timestamp=datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return MarketSnapshot(**base)


# ── ranker ──────────────────────────────────────────────

def test_ranker_prefers_high_relvol_and_movement():
    scanner = AlphaOpportunityScanner()
    hot = _snap(symbol="HOT", relative_volume=8.0, pct_change=7.0, volume_acceleration=2.5)
    cold = _snap(symbol="COLD", relative_volume=0.5, pct_change=0.2, volume_acceleration=0.8)
    assert scanner.score(hot) > scanner.score(cold)


def test_ranker_penalizes_wide_spread():
    scanner = AlphaOpportunityScanner()
    tight = _snap(symbol="TIGHT", spread_bps=10)
    wide = _snap(symbol="WIDE", spread_bps=200)
    assert scanner.score(tight) > scanner.score(wide)


def test_ranker_top_n_orders_correctly():
    scanner = AlphaOpportunityScanner()
    a = _snap(symbol="A", relative_volume=5.0, pct_change=5.0)
    b = _snap(symbol="B", relative_volume=1.1, pct_change=0.5)
    c = _snap(symbol="C", relative_volume=3.0, pct_change=3.0)
    ranked = scanner.rank([a, b, c], top_n=2)
    assert [pair[0].symbol for pair in ranked] == ["A", "C"]


# ── pattern engine ──────────────────────────────────────

def test_pattern_engine_detects_vwap_reclaim():
    engine = AlphaPatternEngine()
    m = _snap(price=99.20, vwap=99.10, relative_volume=1.6, volume_acceleration=1.2,
             high=99.30, pct_change=1.0)
    setup = engine.detect(m)
    assert setup is not None
    assert setup.setup_type == SetupType.VWAP_RECLAIM
    assert setup.state == SetupState.WATCHING


def test_pattern_engine_detects_hod_break():
    engine = AlphaPatternEngine()
    m = _snap(price=100.60, high=100.80, relative_volume=2.5, volume_acceleration=1.4,
             vwap=99.50)
    setup = engine.detect(m)
    assert setup is not None
    assert setup.setup_type in {SetupType.HOD_BREAK, SetupType.BREAKOUT}
    assert setup.state == SetupState.ARMED


def test_pattern_engine_returns_none_for_flat_market():
    engine = AlphaPatternEngine()
    m = _snap(price=100.0, high=100.0, vwap=100.0, relative_volume=0.3,
             volume_acceleration=0.5, pct_change=0.0)
    assert engine.detect(m) is None


# ── trigger watcher ────────────────────────────────────

def test_trigger_watcher_fires_and_freezes_price():
    watcher = AlphaTriggerWatcher()
    setup = ActiveSetup(
        setup_id="s1", symbol="AAA", setup_type=SetupType.HOD_BREAK,
        state=SetupState.ARMED, detected_at=datetime.now(timezone.utc),
        reference_price=50.0, trigger_price=50.20, invalidation_price=49.50,
        score=0.72,
    )
    m = _snap(price=50.30)
    assert watcher.triggered(setup, m) is True
    assert setup.state == SetupState.TRIGGERED
    assert setup.confirmation_price == 50.30  # frozen


def test_trigger_watcher_invalidates_below_stop():
    watcher = AlphaTriggerWatcher()
    setup = ActiveSetup(
        setup_id="s1", symbol="AAA", setup_type=SetupType.HOD_BREAK,
        state=SetupState.ARMED, detected_at=datetime.now(timezone.utc),
        reference_price=50.0, trigger_price=50.20, invalidation_price=49.50,
        score=0.72,
    )
    m = _snap(price=49.40)
    assert watcher.triggered(setup, m) is False
    assert setup.state == SetupState.INVALIDATED


def test_trigger_watcher_waits_between_stop_and_trigger():
    watcher = AlphaTriggerWatcher()
    setup = ActiveSetup(
        setup_id="s1", symbol="AAA", setup_type=SetupType.HOD_BREAK,
        state=SetupState.ARMED, detected_at=datetime.now(timezone.utc),
        reference_price=50.0, trigger_price=50.20, invalidation_price=49.50,
        score=0.72,
    )
    m = _snap(price=49.90)
    assert watcher.triggered(setup, m) is False
    assert setup.state == SetupState.ARMED  # still waiting


# ── level 2 confirmation ───────────────────────────────

def test_l2_returns_neutral_when_missing():
    """Critical: missing L2 must never block. 0.50 is the neutral score."""
    assert Level2Confirmation().score(None) == 0.50


def test_l2_bumps_score_on_positive_imbalance():
    l2 = Level2Snapshot(symbol="X", bid_size=100, ask_size=100,
                        book_imbalance=0.30, tape_delta=0.25,
                        cancel_rate_bid=0.1, cancel_rate_ask=0.1,
                        imbalance_persistence_ms=1000)
    assert Level2Confirmation().score(l2) > 0.60


def test_l2_penalizes_heavy_bid_cancellation():
    l2 = Level2Snapshot(symbol="X", bid_size=100, ask_size=100,
                        book_imbalance=0.0, tape_delta=0.0,
                        cancel_rate_bid=0.80, cancel_rate_ask=0.1,
                        imbalance_persistence_ms=100)
    assert Level2Confirmation().score(l2) < 0.50


# ── intent creation ────────────────────────────────────

def test_create_intent_uses_frozen_confirmation_price():
    setup = ActiveSetup(
        setup_id="s1", symbol="AAA", setup_type=SetupType.HOD_BREAK,
        state=SetupState.TRIGGERED, detected_at=datetime.now(timezone.utc),
        reference_price=50.0, trigger_price=50.20, invalidation_price=49.50,
        score=0.72, confirmation_price=50.25,
    )
    m = _snap(price=52.00)  # market ran away after confirmation
    intent = create_alpha_intent(setup, m, None, Level2Confirmation())
    # Confirmation price is FROZEN — not the runaway market price.
    assert intent.confirmation_price == 50.25
    # 2R target: (50.25 - 49.50) = 0.75 risk → target = 50.25 + 1.5 = 51.75
    assert abs(intent.target_price - 51.75) < 1e-6
    assert intent.stop_price == 49.50


def test_create_intent_combines_setup_and_neutral_l2():
    setup = ActiveSetup(
        setup_id="s1", symbol="AAA", setup_type=SetupType.HOD_BREAK,
        state=SetupState.TRIGGERED, detected_at=datetime.now(timezone.utc),
        reference_price=50.0, trigger_price=50.20, invalidation_price=49.50,
        score=0.80, confirmation_price=50.25,
    )
    intent = create_alpha_intent(setup, _snap(price=50.25), None, Level2Confirmation())
    # 0.80*0.75 + 0.50*0.25 = 0.60 + 0.125 = 0.725
    assert abs(intent.confidence - 0.725) < 1e-6
    assert intent.reason["level2_available"] is False


# ── dedup ──────────────────────────────────────────────

def test_dedup_key_merges_same_market_move():
    """PUMP examined 227 times at $8.40 → ONE setup, not 227."""
    k1 = _setup_dedup_key("PUMP", SetupType.HOD_BREAK, 8.40)
    k2 = _setup_dedup_key("PUMP", SetupType.HOD_BREAK, 8.42)  # within same 0.5% band
    assert k1 == k2


def test_dedup_key_separates_different_symbols():
    a = _setup_dedup_key("AAA", SetupType.HOD_BREAK, 8.40)
    b = _setup_dedup_key("BBB", SetupType.HOD_BREAK, 8.40)
    assert a != b


def test_dedup_key_separates_different_setup_types():
    a = _setup_dedup_key("AAA", SetupType.HOD_BREAK, 8.40)
    b = _setup_dedup_key("AAA", SetupType.VWAP_RECLAIM, 8.40)
    assert a != b
