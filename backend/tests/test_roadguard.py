"""Tests for the RoadGuard shared execution safety governor.

Locks the four contract pillars:

1. **Authority is veto-only.** ``ROADGUARD_CAN_APPROVE`` is False;
   the response envelope cannot encode a "promote this trade"
   instruction. ``allowed=True`` only happens when no rule fires.
2. **Shadow vs enforce.** ``decision`` reflects the rule cascade
   independently of the env flag; ``enforce`` only flips True
   when ``ROADGUARD_ENFORCE_ENABLED`` AND the cascade said
   non-ALLOW.
3. **Cascade priority.** Account-wide kills (broker health, daily
   loss) supersede per-trade caps. Per-lane caps fire correctly
   for their own lane and don't leak across.
4. **Determinism.** Same input → same output, no side effects,
   no DB calls in the core ``evaluate`` path.
"""
from __future__ import annotations

import pytest

from services import roadguard as rg
from services.roadguard import (
    ROADGUARD_CAN_APPROVE,
    Lane,
    RoadGuard,
    RoadGuardDecision,
    RoadGuardRequest,
    build_roadguard_request,
)


def _baseline_request(**overrides) -> RoadGuardRequest:
    """A clean, all-headroom request the cascade always allows.
    Tests override only the fields they care about."""
    base = dict(
        symbol="BTC-USD",
        lane="crypto",
        requested_notional_usd=100.0,
        current_total_exposure_usd=0.0,
        current_equity_exposure_usd=0.0,
        current_crypto_exposure_usd=0.0,
        open_positions_total=0,
        open_positions_in_lane=0,
        daily_realized_pnl_usd=0.0,
        broker_health_score=1.0,
        existing_open_symbols=[],
    )
    base.update(overrides)
    return RoadGuardRequest(**base)


# ── 1. Authority invariants ──────────────────────────────────────


def test_can_approve_flag_is_false():
    """The single most important RoadGuard contract."""
    assert ROADGUARD_CAN_APPROVE is False


def test_response_envelope_has_no_executor_actionable_fields():
    """Verify nothing in the response can be misread as a BUY/SELL
    instruction. Only ``decision`` (ALLOW/BLOCK/PAUSE_LANE) and
    ``allowed`` (bool) leave the governor."""
    r = RoadGuard().evaluate(_baseline_request())
    forbidden = {"action", "side", "direction", "qty", "size", "buy", "sell"}
    assert not (set(r.__dict__.keys()) & forbidden), (
        "RoadGuardResponse must carry no executor-actionable fields"
    )


# ── 2. Shadow vs enforce ────────────────────────────────────────


def test_shadow_mode_records_block_without_enforcing(monkeypatch):
    monkeypatch.setattr(rg, "ROADGUARD_ENFORCE_ENABLED", False)
    r = RoadGuard().evaluate(_baseline_request(broker_health_score=0.30))
    assert r.decision == "BLOCK"
    assert r.allowed is False
    assert r.enforce is False  # shadow — verdict logged, not enforced


def test_enforce_mode_marks_enforce_true(monkeypatch):
    monkeypatch.setattr(rg, "ROADGUARD_ENFORCE_ENABLED", True)
    r = RoadGuard().evaluate(_baseline_request(broker_health_score=0.30))
    assert r.decision == "BLOCK"
    assert r.enforce is True


def test_enforce_mode_allow_does_not_set_enforce(monkeypatch):
    """Enforce flag has no meaning when the verdict is ALLOW —
    there's nothing to enforce."""
    monkeypatch.setattr(rg, "ROADGUARD_ENFORCE_ENABLED", True)
    r = RoadGuard().evaluate(_baseline_request())
    assert r.decision == "ALLOW"
    assert r.allowed is True
    assert r.enforce is False


# ── 3. Cascade — each rule fires for its own condition ──────────


def test_broker_health_below_floor_blocks():
    r = RoadGuard().evaluate(_baseline_request(broker_health_score=0.50))
    assert r.decision == "BLOCK"
    assert r.reason == "BROKER_HEALTH_DEGRADED"


def test_daily_loss_breach_pauses_lane():
    """Daily loss → PAUSE_LANE (not BLOCK) — the operator should
    still be able to manage existing positions; only NEW entries
    are halted until next session."""
    r = RoadGuard().evaluate(_baseline_request(daily_realized_pnl_usd=-150.0))
    assert r.decision == "PAUSE_LANE"
    assert r.reason == "MAX_DAILY_LOSS_REACHED"


def test_total_exposure_cap_blocks():
    r = RoadGuard().evaluate(_baseline_request(
        current_total_exposure_usd=1450.0,  # default cap is 1500
        requested_notional_usd=100.0,       # 1450 + 100 = 1550 → over
    ))
    assert r.decision == "BLOCK"
    assert r.reason == "MAX_TOTAL_EXPOSURE"


def test_equity_lane_cap_blocks_only_in_equity_lane():
    """Crypto request must NOT be vetoed by the equity cap."""
    rq = _baseline_request(
        lane="crypto",
        current_equity_exposure_usd=1000.0,  # over equity cap (900)
        current_crypto_exposure_usd=0.0,
        requested_notional_usd=100.0,
    )
    # The total exposure (1000+100=1100) is also under 1500 cap,
    # so this request should ALLOW even though equity cap is over.
    r = RoadGuard().evaluate(rq)
    assert r.decision == "ALLOW", (
        "equity cap must not bleed into the crypto lane"
    )


def test_equity_lane_cap_blocks_when_in_equity():
    rq = _baseline_request(
        lane="equity",
        current_equity_exposure_usd=850.0,
        requested_notional_usd=100.0,  # 850 + 100 = 950 over 900
    )
    r = RoadGuard().evaluate(rq)
    assert r.decision == "BLOCK"
    assert r.reason == "MAX_EQUITY_EXPOSURE"


def test_crypto_lane_cap_blocks_when_in_crypto():
    rq = _baseline_request(
        lane="crypto",
        current_crypto_exposure_usd=550.0,
        requested_notional_usd=100.0,  # 550 + 100 = 650 over 600
    )
    r = RoadGuard().evaluate(rq)
    assert r.decision == "BLOCK"
    assert r.reason == "MAX_CRYPTO_EXPOSURE"


def test_max_open_positions_total_blocks():
    r = RoadGuard().evaluate(_baseline_request(open_positions_total=8))
    assert r.decision == "BLOCK"
    assert r.reason == "MAX_OPEN_POSITIONS_TOTAL"


def test_max_open_positions_per_lane_blocks():
    r = RoadGuard().evaluate(_baseline_request(open_positions_in_lane=5))
    assert r.decision == "BLOCK"
    assert r.reason == "MAX_OPEN_POSITIONS_PER_LANE"


def test_duplicate_symbol_blocks_when_disabled():
    r = RoadGuard().evaluate(_baseline_request(
        symbol="AAPL",
        existing_open_symbols=["AAPL", "TSLA"],
    ))
    assert r.decision == "BLOCK"
    assert r.reason == "DUPLICATE_SYMBOL"


def test_clean_request_allows():
    r = RoadGuard().evaluate(_baseline_request())
    assert r.decision == "ALLOW"
    assert r.allowed is True
    assert r.reason is None


# ── 4. Cascade priority — account-wide kills supersede ─────────


def test_broker_health_supersedes_other_failures():
    """Even when multiple rules would fire, broker-health takes
    priority — it's the most account-critical signal."""
    r = RoadGuard().evaluate(_baseline_request(
        broker_health_score=0.30,
        daily_realized_pnl_usd=-200.0,
        current_total_exposure_usd=1500.0,
    ))
    assert r.reason == "BROKER_HEALTH_DEGRADED"


def test_daily_loss_supersedes_per_trade_caps():
    r = RoadGuard().evaluate(_baseline_request(
        daily_realized_pnl_usd=-200.0,
        current_total_exposure_usd=1500.0,
    ))
    assert r.reason == "MAX_DAILY_LOSS_REACHED"


# ── 5. State-collection helper ──────────────────────────────────


def test_build_request_attributes_lane_from_position_tag():
    """``open_positions`` carrying explicit ``lane`` tags must
    drive per-lane exposure correctly."""
    open_positions = [
        {"symbol": "AAPL", "size_usd": 200.0, "lane": "equity"},
        {"symbol": "BTC-USD", "size_usd": 150.0, "lane": "crypto"},
        {"symbol": "TSLA", "size_usd": 100.0, "lane": "equity"},
    ]
    req = build_roadguard_request(
        symbol="ETH-USD",
        lane="crypto",
        requested_notional_usd=50.0,
        open_positions=open_positions,
    )
    assert req.current_total_exposure_usd == pytest.approx(450.0)
    assert req.current_equity_exposure_usd == pytest.approx(300.0)
    assert req.current_crypto_exposure_usd == pytest.approx(150.0)
    assert req.open_positions_total == 3
    assert req.open_positions_in_lane == 1  # only one crypto position
    assert "AAPL" in req.existing_open_symbols


def test_build_request_falls_back_to_asset_type_for_legacy_positions():
    """Older docs without ``lane`` tag use ``asset_type`` instead."""
    open_positions = [
        {"symbol": "AAPL", "size_usd": 200.0, "asset_type": "stock"},
        {"symbol": "BTC-USD", "size_usd": 150.0, "asset_type": "crypto"},
    ]
    req = build_roadguard_request(
        symbol="ETH-USD",
        lane="crypto",
        requested_notional_usd=50.0,
        open_positions=open_positions,
    )
    assert req.current_equity_exposure_usd == pytest.approx(200.0)
    assert req.current_crypto_exposure_usd == pytest.approx(150.0)


def test_build_request_handles_empty_open_positions():
    req = build_roadguard_request(
        symbol="AAPL",
        lane="equity",
        requested_notional_usd=100.0,
        open_positions=None,
    )
    assert req.current_total_exposure_usd == 0.0
    assert req.open_positions_total == 0
    assert req.existing_open_symbols == []


def test_build_request_default_broker_health_is_one():
    """Missing broker-health probe defaults to healthy (1.0) so a
    missing telemetry source can't accidentally veto everything."""
    req = build_roadguard_request(
        symbol="AAPL", lane="equity", requested_notional_usd=10.0,
    )
    assert req.broker_health_score == 1.0


# ── 6. Determinism / replay ─────────────────────────────────────


def test_evaluate_is_pure_same_input_same_output():
    """Replaying a logged request must reproduce the verdict
    exactly (modulo timestamp). Lets the operator audit a past
    decision from logs alone."""
    rq = _baseline_request(
        broker_health_score=0.30,
    )
    r1 = RoadGuard().evaluate(rq)
    r2 = RoadGuard().evaluate(rq)
    assert r1.decision == r2.decision
    assert r1.reason == r2.reason
    assert r1.allowed == r2.allowed
    # Diagnostics ignore timestamp
    assert r1.diagnostics == r2.diagnostics
