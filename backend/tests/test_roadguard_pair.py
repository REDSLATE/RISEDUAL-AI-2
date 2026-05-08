"""Lane-isolated RoadGuard pair tests (closed-loop architecture).

EquityRoadGuard pinned to equity; CryptoRoadGuard pinned to crypto.
A signal arriving at the wrong RG MUST fail LANE_MISMATCH at G00.
Each pair has its own threshold envs and its own decision-log collection.
"""
from __future__ import annotations

import os
from typing import List

import pytest

from services.ml.roadguard import (
    AccountSnapshot,
    CryptoRoadGuard,
    EquityRoadGuard,
    RoadGuardV2,
    TradeIntent,
)


def _account(**kwargs) -> AccountSnapshot:
    base = dict(
        cash_usd=1000.0, equity_value_usd=1000.0,
        daily_realized_pnl_usd=0.0, broker_health_score=1.0,
        total_exposure_usd=0.0, equity_exposure_usd=0.0,
        crypto_exposure_usd=0.0,
        open_positions_total=0, open_positions_in_lane=0,
        existing_open_symbols=[],
    )
    base.update(kwargs)
    return AccountSnapshot(**base)


def _equity_intent(**kwargs) -> TradeIntent:
    base = dict(symbol="AAPL", lane="equity", side="BUY",
                requested_notional_usd=100.0, will_hit_live_broker=False)
    base.update(kwargs)
    return TradeIntent(**base)


def _crypto_intent(**kwargs) -> TradeIntent:
    base = dict(symbol="BTC-USD", lane="crypto", side="BUY",
                requested_notional_usd=100.0, will_hit_live_broker=False)
    base.update(kwargs)
    return TradeIntent(**base)


# ── G00 — LANE_MISMATCH (closed loop) ────────────────────────────


def test_equity_rg_rejects_crypto_intent_at_g00():
    v = EquityRoadGuard().evaluate(_crypto_intent(), _account())
    assert v.decision == "BLOCK"
    assert v.gate == "G00"
    assert v.reason and v.reason.startswith("LANE_MISMATCH")
    assert v.lane == "equity"


def test_crypto_rg_rejects_equity_intent_at_g00():
    v = CryptoRoadGuard().evaluate(_equity_intent(), _account())
    assert v.decision == "BLOCK"
    assert v.gate == "G00"
    assert v.reason and v.reason.startswith("LANE_MISMATCH")
    assert v.lane == "crypto"


def test_lane_match_passes_g00():
    v = EquityRoadGuard().evaluate(_equity_intent(), _account())
    # PASS or fail later, but NOT at G00
    assert v.gate != "G00"


# ── Lane-only exposure (closed loop) ─────────────────────────────


def test_equity_rg_only_checks_equity_exposure():
    """Crypto exposure should not affect equity RG decisions."""
    snap = _account(
        equity_exposure_usd=850,           # near 900 cap
        crypto_exposure_usd=10_000,        # ENORMOUS but ignored
        total_exposure_usd=850 + 10_000,
    )
    # Use a small request that would hit lane cap but is below the
    # total cap *if* total were just equity. The fact that total
    # exposure includes crypto would normally trigger G03 first;
    # we verify this happens via the dispatcher to confirm it's
    # the total-cap that fires, not a lane confusion.
    v = EquityRoadGuard().evaluate(
        _equity_intent(requested_notional_usd=50),
        snap,
    )
    # Since total_exposure (10850) already exceeds the v2 default
    # 1500 total cap, G03 fires and blocks.
    assert v.gate in ("G03",)
    # Lane-cap projection shouldn't include crypto exposure:
    diag = v.diagnostics
    assert diag.get("thresholds", {}).get("lane") == "equity"


def test_crypto_rg_only_checks_crypto_exposure():
    snap = _account(
        crypto_exposure_usd=550,           # near 600 cap
        equity_exposure_usd=10_000,        # ENORMOUS but ignored by crypto RG
        total_exposure_usd=550 + 10_000,
    )
    v = CryptoRoadGuard().evaluate(
        _crypto_intent(requested_notional_usd=50),
        snap,
    )
    # Same — total cap fires first (10550 > 1500). Confirms lane
    # field is "crypto" on the verdict.
    assert v.lane == "crypto"


def test_equity_rg_lane_cap_uses_equity_only():
    """When total cap is configured high, equity lane cap applies
    based on equity_exposure ONLY — crypto exposure is invisible."""
    os.environ["ROADGUARD_EQUITY_MAX_TOTAL_EXPOSURE_USD"] = "100000"
    try:
        snap = _account(
            equity_exposure_usd=850,       # 50 below 900 cap
            crypto_exposure_usd=10_000,    # invisible to equity RG
            total_exposure_usd=10_850,
        )
        v = EquityRoadGuard().evaluate(
            _equity_intent(requested_notional_usd=200),
            snap,
        )
        # Should REDUCE at G04 (lane cap) — crypto exposure does
        # not contribute to the equity RG's lane projection.
        assert v.gate == "G04"
        assert v.decision == "REDUCE"
        assert v.adjusted_notional_usd == pytest.approx(50.0)
    finally:
        del os.environ["ROADGUARD_EQUITY_MAX_TOTAL_EXPOSURE_USD"]


# ── Decision log collection per lane ─────────────────────────────


def test_equity_rg_collection_name():
    assert EquityRoadGuard.DECISIONS_COLLECTION == "roadguard_equity_decisions"


def test_crypto_rg_collection_name():
    assert CryptoRoadGuard.DECISIONS_COLLECTION == "roadguard_crypto_decisions"


def test_collections_are_distinct():
    # Closed-loop guarantee: equity and crypto verdicts CANNOT mix
    # in the same collection.
    assert (
        EquityRoadGuard.DECISIONS_COLLECTION
        != CryptoRoadGuard.DECISIONS_COLLECTION
    )


# ── Verdict carries lane field ───────────────────────────────────


def test_verdict_carries_originating_lane():
    eq_v = EquityRoadGuard().evaluate(_equity_intent(), _account())
    cr_v = CryptoRoadGuard().evaluate(_crypto_intent(requested_notional_usd=50), _account())
    assert eq_v.lane == "equity"
    assert cr_v.lane == "crypto"


# ── Per-lane env tunability ──────────────────────────────────────


def test_equity_env_does_not_affect_crypto():
    os.environ["ROADGUARD_EQUITY_MIN_TICKET_USD"] = "1000"
    try:
        # Equity RG should reject a $50 ticket
        eq_v = EquityRoadGuard().evaluate(
            _equity_intent(requested_notional_usd=50), _account(),
        )
        assert eq_v.gate == "G09"
        # Crypto RG should NOT be affected — uses crypto default min
        cr_v = CryptoRoadGuard().evaluate(
            _crypto_intent(requested_notional_usd=50), _account(),
        )
        # Crypto default min ticket is 5; $50 passes G09
        assert cr_v.gate != "G09"
    finally:
        del os.environ["ROADGUARD_EQUITY_MIN_TICKET_USD"]


def test_crypto_env_does_not_affect_equity():
    os.environ["ROADGUARD_CRYPTO_BROKER_HEALTH_MIN"] = "0.99"
    try:
        # Crypto RG should fail G01 with default 0.95 broker health
        cr_v = CryptoRoadGuard().evaluate(
            _crypto_intent(), _account(broker_health_score=0.95),
        )
        assert cr_v.gate == "G01"
        # Equity RG should NOT be affected
        eq_v = EquityRoadGuard().evaluate(
            _equity_intent(), _account(broker_health_score=0.95),
        )
        assert eq_v.gate != "G01"
    finally:
        del os.environ["ROADGUARD_CRYPTO_BROKER_HEALTH_MIN"]


# ── Dispatcher backwards-compat ──────────────────────────────────


def test_dispatcher_routes_equity_to_equity_rg():
    v = RoadGuardV2().evaluate(_equity_intent(), _account())
    assert v.lane == "equity"


def test_dispatcher_routes_crypto_to_crypto_rg():
    v = RoadGuardV2().evaluate(_crypto_intent(requested_notional_usd=50), _account())
    assert v.lane == "crypto"


def test_dispatcher_unknown_lane_blocks():
    # Manually craft an intent with an unknown lane.
    bad = TradeIntent(symbol="X", lane="options", side="BUY",
                      requested_notional_usd=100.0)
    v = RoadGuardV2().evaluate(bad, _account())
    assert v.decision == "BLOCK"
    assert v.gate == "G04"


# ── Distinct instances (closed loop) ─────────────────────────────


def test_eq_and_cr_rg_are_distinct_classes():
    assert EquityRoadGuard is not CryptoRoadGuard
    e = EquityRoadGuard()
    c = CryptoRoadGuard()
    assert e.LANE != c.LANE
    assert e is not c
