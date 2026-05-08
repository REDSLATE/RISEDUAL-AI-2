"""ML boundary scaffolding tests — Phase 0.

Pins:
  * BaseMLLayer: missing artifact -> NO_TRADE / LANE_DISABLED
  * ShadowMLLayer: cannot expose execute / place_order / size / veto
  * VetoOnlyMLLayer: BUY/SELL output coerced to NO_TRADE / AUTHORITY_VIOLATION
  * Boot receipts: register + retrieve + clear
"""
from __future__ import annotations

from services.ml import boot_receipts
from services.ml.base import BaseMLLayer, ShadowMLLayer, VetoOnlyMLLayer
from services.ml.contracts import (
    DECISION_STAGES,
    FeatureFrame,
    LaneDisabledError,
    MLVerdict,
    ModelBootReceipt,
    Verdict,
)


def _frame() -> FeatureFrame:
    return FeatureFrame(symbol="AAPL", lane="equity", timestamp="2026-02-01T00:00:00Z")


# ── BaseMLLayer ──────────────────────────────────────────────────


class _AlwaysApproveLayer(BaseMLLayer):
    layer_id = "test_approve"
    can_approve = True

    def _predict(self, frame):
        return MLVerdict(layer=self.layer_id, decision=Verdict.BUY.value,
                         confidence=0.9, reason="OK", can_approve=True)


def test_base_layer_returns_predict_when_ready():
    layer = _AlwaysApproveLayer()
    layer.boot()
    v = layer.decide(_frame())
    assert v.decision == Verdict.BUY.value


def test_base_layer_disabled_returns_no_trade():
    layer = _AlwaysApproveLayer()
    layer._boot_receipt = ModelBootReceipt(
        layer="test_approve", lane=None, ready=False,
        artifact_path=None, artifact_present=False,
        reason="missing_artifact", can_approve=True, shadow_only=False,
    )
    v = layer.decide(_frame())
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason and v.reason.startswith("LANE_DISABLED")


def test_base_layer_predict_exception_returns_no_trade():
    class _Boom(BaseMLLayer):
        layer_id = "boom"
        def _predict(self, frame):
            raise RuntimeError("kaboom")
    v = _Boom().decide(_frame())
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason and v.reason.startswith("LAYER_EXCEPTION")


def test_base_layer_lane_disabled_error_returns_no_trade():
    class _Disabled(BaseMLLayer):
        layer_id = "dis"
        def _predict(self, frame):
            raise LaneDisabledError("dis", reason="missing", lane="equity")
    v = _Disabled().decide(_frame())
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason and v.reason.startswith("LANE_DISABLED")


# ── VetoOnlyMLLayer ──────────────────────────────────────────────


class _BadVeto(VetoOnlyMLLayer):
    layer_id = "veto_bad"
    def _predict(self, frame):
        # Tries to approve — must be coerced.
        return MLVerdict(layer=self.layer_id, decision=Verdict.BUY.value,
                         confidence=0.99, reason="CHEATING", can_approve=True)


def test_veto_only_cannot_approve():
    v = _BadVeto().decide(_frame())
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "AUTHORITY_VIOLATION"
    assert v.can_approve is False


# ── ShadowMLLayer ────────────────────────────────────────────────


class _SilentShadow(ShadowMLLayer):
    layer_id = "shadow_silent"

    def observe(self, frame):
        return {"saw": frame.symbol}


def test_shadow_layer_always_no_trade():
    v = _SilentShadow().decide(_frame())
    assert v.decision == Verdict.NO_TRADE.value
    assert v.reason == "SHADOW_OBSERVATION"
    assert v.can_approve is False


def test_shadow_layer_seal_methods_raise():
    s = _SilentShadow()
    for name in ("execute", "place_order", "request_order", "size", "veto"):
        try:
            getattr(s, name)()
        except RuntimeError:
            continue
        raise AssertionError(f"shadow layer {name}() did not raise")


def test_shadow_layer_can_approve_pinned_false():
    assert _SilentShadow.can_approve is False
    assert _SilentShadow.shadow_only is True


# ── Boot receipts ────────────────────────────────────────────────


def test_boot_receipts_register_and_get():
    boot_receipts.clear()
    r = ModelBootReceipt(
        layer="x", lane="equity", ready=True,
        artifact_path=None, artifact_present=True, reason=None,
        can_approve=True, shadow_only=False,
    )
    boot_receipts.register(r)
    got = boot_receipts.get("x", "equity")
    assert got is r
    assert len(boot_receipts.get_all_receipts()) == 1
    boot_receipts.clear()
    assert len(boot_receipts.get_all_receipts()) == 0


# ── Decision stages ──────────────────────────────────────────────


def test_decision_stages_canonical_order():
    assert DECISION_STAGES == [
        "shelly_recall", "perception", "strategist", "auditor",
        "fast_veto", "shadow", "executor", "roadguard",
    ]
