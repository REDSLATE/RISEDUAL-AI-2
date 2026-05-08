"""ML pipeline + executor lanes + Shelly + RoadGuard v2 tests.

Covers Phases 2, 3, 4, 4.5.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.ml import boot_receipts
from services.ml.auditor import AuditorML
from services.ml.contracts import (
    FeatureFrame,
    MLVerdict,
    Verdict,
)
from services.ml.executors import CryptoExecutorML, EquityExecutorML
from services.ml.fast_veto import FastVetoMLLayer
from services.ml.pipeline import RisedualMLPipeline
from services.ml.roadguard import (
    AccountSnapshot,
    RoadGuardV2,
    TradeIntent,
)
from services.ml.shadow import ShadowML
from services.ml.shelly import ShellyClient, ShellyRecall
from services.ml.strategist import StrategistML


def _frame(lane="equity") -> FeatureFrame:
    return FeatureFrame(
        symbol="AAPL", lane=lane,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── Strategist ───────────────────────────────────────────────────


def test_strategist_no_trade_inheritance():
    f = _frame()
    f.perception = {"symbolic": {"decision": "NO_TRADE", "reason": "X"}}
    v = StrategistML().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "X"


def test_strategist_memory_negative_downgrades():
    f = _frame()
    f.perception = {"symbolic": {"decision": "BUY", "reason": "PASS"}}
    f.shelly_recall = {"negative_count": 5, "summary": "many losses"}
    v = StrategistML().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "STRATEGIST_MEMORY_NEGATIVE"


def test_strategist_confirm_when_clean():
    f = _frame()
    f.perception = {"symbolic": {"decision": "BUY", "reason": "PASS"}}
    f.shelly_recall = {"negative_count": 0}
    v = StrategistML().decide(f)
    assert v.decision == Verdict.BUY.value
    assert v.reason == "STRATEGIST_CONFIRM"


# ── Auditor ──────────────────────────────────────────────────────


def test_auditor_passes_through_no_trade():
    f = _frame()
    f.strategist = {"decision": "NO_TRADE", "reason": "SR1", "confidence": 0.0}
    v = AuditorML().decide(f)
    assert v.decision == Verdict.NO_TRADE.value


def test_auditor_low_confidence_downgrade():
    f = _frame()
    f.strategist = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.perception = {"scores": {
        k: {"confidence": 0.1} for k in (
            "event_shock", "regime", "drawdown",
            "liquidity", "system_health", "pacing",
        )
    }}
    v = AuditorML().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "AUDITOR_LOW_CONFIDENCE"


def test_auditor_drawdown_danger():
    f = _frame()
    f.strategist = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.perception = {"scores": {
        k: {"confidence": 0.7, "score": 0.5} for k in (
            "event_shock", "regime", "drawdown",
            "liquidity", "system_health", "pacing",
        )
    }}
    f.perception["scores"]["drawdown"] = {"confidence": 0.7, "score": 0.95}
    v = AuditorML().decide(f)
    assert v.reason == "AUDITOR_DD_DANGER"


def test_auditor_confirm_when_clean():
    f = _frame()
    f.strategist = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.perception = {"scores": {
        k: {"confidence": 0.7, "score": 0.5} for k in (
            "event_shock", "regime", "drawdown",
            "liquidity", "system_health", "pacing",
        )
    }}
    v = AuditorML().decide(f)
    assert v.decision == Verdict.BUY.value
    assert v.reason == "AUDITOR_CONFIRM"


# ── Fast Veto ML ─────────────────────────────────────────────────


def test_fast_veto_passthrough_when_auditor_blocks():
    f = _frame()
    f.auditor = {"decision": "NO_TRADE", "reason": "AUD"}
    v = FastVetoMLLayer().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "AUD"


def test_fast_veto_low_confidence_product_vetoes():
    f = _frame()
    f.auditor = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.perception = {"scores": {
        k: {"confidence": 0.4} for k in (
            "event_shock", "regime", "drawdown",
            "liquidity", "system_health", "pacing",
        )
    }}
    # 0.4^6 = 0.0041 < 0.10 default floor
    v = FastVetoMLLayer().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "FAST_VETO_ML_LOW_PRODUCT"


def test_fast_veto_passthrough_high_confidence():
    f = _frame()
    f.auditor = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.perception = {"scores": {
        k: {"confidence": 0.9} for k in (
            "event_shock", "regime", "drawdown",
            "liquidity", "system_health", "pacing",
        )
    }}
    v = FastVetoMLLayer().decide(f)
    # Passthrough sentinel
    assert v.reason == "FAST_VETO_ML_PASSTHROUGH"
    assert v.can_approve is False


# ── Shadow ML ────────────────────────────────────────────────────


def test_shadow_layer_observe_only():
    f = _frame()
    f.perception = {"scores": {"regime": {"label": "RISK_ON"}}}
    v = ShadowML().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "SHADOW_OBSERVATION"
    assert v.diagnostics.get("regime_label") == "RISK_ON"


# ── Executor MLs ─────────────────────────────────────────────────


def test_equity_executor_lane_mismatch():
    f = _frame(lane="crypto")
    f.auditor = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.fast_veto = {"decision": "NO_TRADE", "reason": "FAST_VETO_ML_PASSTHROUGH"}
    v = EquityExecutorML().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "LANE_MISMATCH"


def test_crypto_executor_lane_mismatch():
    f = _frame(lane="equity")
    f.auditor = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.fast_veto = {"decision": "NO_TRADE", "reason": "FAST_VETO_ML_PASSTHROUGH"}
    v = CryptoExecutorML().decide(f)
    assert v.reason == "LANE_MISMATCH"


def test_equity_executor_respects_real_veto():
    f = _frame(lane="equity")
    f.auditor = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.fast_veto = {"decision": "NO_TRADE", "reason": "FAST_VETO_ML_LOW_PRODUCT"}
    v = EquityExecutorML().decide(f)
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason and v.reason.startswith("EXECUTOR_RESPECT_VETO")


def test_equity_executor_approves_when_clean():
    f = _frame(lane="equity")
    f.auditor = {"decision": "BUY", "reason": "OK", "confidence": 0.7}
    f.fast_veto = {"decision": "NO_TRADE", "reason": "FAST_VETO_ML_PASSTHROUGH"}
    v = EquityExecutorML().decide(f)
    assert v.decision == Verdict.BUY.value
    assert v.reason == "EXECUTOR_APPROVE"


def test_executor_two_distinct_instances():
    e = EquityExecutorML()
    c = CryptoExecutorML()
    assert e.lane == "equity"
    assert c.lane == "crypto"
    assert e is not c


# ── ShellyClient ─────────────────────────────────────────────────


def test_shelly_client_has_no_authority_methods():
    c = ShellyClient()
    for name in ("veto", "size", "execute", "place_order", "request_order"):
        assert not hasattr(c, name), (
            f"ShellyClient must not expose {name}; seal violated"
        )


def test_shelly_recall_returns_recall_object_on_failure():
    c = ShellyClient()
    c._service = False  # force the unavailable path
    r = c.recall(_frame())
    assert isinstance(r, ShellyRecall)
    assert r.episodes_found == 0


def test_shelly_remember_returns_false_when_unavailable():
    c = ShellyClient()
    c._service = False
    v = MLVerdict(layer="executor", decision="BUY", confidence=0.5,
                  reason="OK", can_approve=True)
    assert c.remember(_frame(), v) is False


# ── Pipeline end-to-end ──────────────────────────────────────────


def test_pipeline_runs_without_crashing():
    p = RisedualMLPipeline()
    f = _frame()
    f.market = {
        "broker_uptime": 1.0, "data_lag_ms": 50, "error_rate": 0.0,
        "pipeline_latency_ms": 80,
    }
    decision = p.decide(f)
    assert decision.symbol == "AAPL"
    assert decision.lane == "equity"
    # Either passes through or blocks at a known stage.
    if decision.blocked_at is not None:
        assert decision.blocked_at in (
            "perception", "strategist", "auditor",
            "fast_veto", "executor",
        )


def test_pipeline_records_trail_in_order():
    p = RisedualMLPipeline()
    decision = p.decide(_frame())
    layers = [v.layer for v in decision.trail]
    # Trail starts with perception, follows canonical order.
    assert layers[0] == "perception"
    expected_order = [
        "perception", "strategist", "auditor",
        "fast_veto", "shadow", "executor",
    ]
    # Filter expected to those present in trail (early NO_TRADE may truncate).
    seen = [l for l in expected_order if l in layers]
    assert layers == seen


# ── RoadGuard v2 ─────────────────────────────────────────────────


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


def _intent(**kwargs) -> TradeIntent:
    base = dict(
        symbol="AAPL", lane="equity", side="BUY",
        requested_notional_usd=100.0, will_hit_live_broker=False,
    )
    base.update(kwargs)
    return TradeIntent(**base)


def test_roadguard_v2_pass_clean():
    v = RoadGuardV2().evaluate(_intent(), _account())
    assert v.decision == "PASS"
    assert v.can_approve is False


def test_roadguard_v2_block_broker_health():
    v = RoadGuardV2().evaluate(_intent(), _account(broker_health_score=0.1))
    assert v.decision == "BLOCK"
    assert v.gate == "G01"


def test_roadguard_v2_block_daily_loss_cap():
    v = RoadGuardV2().evaluate(_intent(), _account(daily_realized_pnl_usd=-200))
    assert v.gate == "G02"
    assert v.decision == "BLOCK"


def test_roadguard_v2_g09_min_ticket():
    v = RoadGuardV2().evaluate(_intent(requested_notional_usd=0.1), _account())
    assert v.gate == "G09"


def test_roadguard_v2_reduce_total_exposure():
    v = RoadGuardV2().evaluate(
        _intent(requested_notional_usd=2000),
        _account(total_exposure_usd=1450),
    )
    assert v.decision == "REDUCE"
    assert v.gate == "G03"
    assert v.adjusted_notional_usd is not None


def test_roadguard_v2_block_total_full():
    v = RoadGuardV2().evaluate(
        _intent(requested_notional_usd=2000),
        _account(total_exposure_usd=1500),
    )
    assert v.decision == "BLOCK"


def test_roadguard_v2_lane_caps():
    v = RoadGuardV2().evaluate(
        _intent(lane="equity", requested_notional_usd=200),
        _account(total_exposure_usd=850, equity_exposure_usd=850),
    )
    # Equity cap default 900; lane headroom 50; total cap 1500 has
    # 650 headroom so total gate is fine. Lane gate should REDUCE.
    assert v.decision == "REDUCE"
    assert v.gate == "G04"


def test_roadguard_v2_max_positions_total():
    v = RoadGuardV2().evaluate(
        _intent(),
        _account(open_positions_total=8),
    )
    assert v.gate == "G05"


def test_roadguard_v2_max_positions_per_lane():
    v = RoadGuardV2().evaluate(
        _intent(),
        _account(open_positions_in_lane=5),
    )
    assert v.gate == "G06"


def test_roadguard_v2_duplicate_symbol_block():
    v = RoadGuardV2().evaluate(
        _intent(symbol="AAPL"),
        _account(existing_open_symbols=["AAPL"]),
    )
    assert v.gate == "G07"


def test_roadguard_v2_g10_kill_switch_when_live_attempt():
    v = RoadGuardV2().evaluate(
        _intent(will_hit_live_broker=True),
        _account(),
    )
    # Default BROKER_LIVE_ORDER_ENABLED=false in env → BLOCK
    assert v.gate == "G10"
    assert v.decision == "BLOCK"


def test_roadguard_v2_can_approve_pinned_false():
    from services.ml.roadguard.governor import ROADGUARD_CAN_APPROVE
    assert ROADGUARD_CAN_APPROVE is False


def test_roadguard_v2_unknown_lane_blocks():
    v = RoadGuardV2().evaluate(
        _intent(lane="options"),
        _account(),
    )
    assert v.decision == "BLOCK"
    assert v.gate == "G04"
