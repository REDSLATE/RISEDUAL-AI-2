"""Perception engine.

Combines the 6 sub-models with a deterministic 5-rule symbolic
overlay. Output is a :class:`MLVerdict` carrying the richest
diagnostic shape in the pipeline — Strategist, Auditor, and the
two Executor MLs all read from this verdict's diagnostics and
the ``frame.perception`` enrichment dict.

Symbolic rules (run AFTER the 6 ML scores are produced; first match
wins):

  R1. SystemHealth.score < 0.4         -> NO_TRADE / SYSTEM_DEGRADED
  R2. EventShock.label == "EVENT_HIGH" -> NO_TRADE / EVENT_SHOCK_ACTIVE
  R3. Liquidity.label == "LIQ_THIN"    -> NO_TRADE / LIQUIDITY_THIN
  R4. Drawdown.label == "DD_DEEP"      -> NO_TRADE / DRAWDOWN_DEEP
  R5. Regime is RISK_OFF and intent is BUY-leaning
                                       -> NO_TRADE / REGIME_RISK_OFF

Otherwise: forward the dominant directional bias as
``BUY``/``SELL``/``NO_TRADE`` based on regime + drawdown distance.
The downstream Strategist/Auditor refine; Perception is intentionally
coarse.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from services.ml import boot_receipts
from services.ml.base import BaseMLLayer
from services.ml.contracts import (
    FeatureFrame,
    LaneDisabledError,
    MLVerdict,
    ModelBootReceipt,
    Verdict,
)
from services.ml.perception.models import (
    DrawdownDistanceModel,
    EventShockModel,
    LiquidityModel,
    PacingModel,
    RegimeStateModel,
    SystemHealthModel,
    extract_drawdown_features,
    extract_event_shock_features,
    extract_liquidity_features,
    extract_pacing_features,
    extract_regime_features,
    extract_system_health_features,
)

logger = logging.getLogger(__name__)


class AutonomousDecisionEngine:
    """Owns the 6 perception sub-models + symbolic rules.

    Held by :class:`PerceptionML` but exposed separately so other
    layers (e.g. ad-hoc admin diagnostic endpoints) can call into
    individual sub-models without spinning up the full layer.
    """

    def __init__(self):
        self.event_shock = EventShockModel()
        self.regime = RegimeStateModel()
        self.drawdown = DrawdownDistanceModel()
        self.liquidity = LiquidityModel()
        self.system_health = SystemHealthModel()
        self.pacing = PacingModel()

    # ── Sub-model evaluation ───────────────────────────────────

    def evaluate_all(self, market: Dict[str, Any]) -> Dict[str, Any]:
        es = self.event_shock.predict(extract_event_shock_features(market))
        rg = self.regime.predict(extract_regime_features(market))
        dd = self.drawdown.predict(extract_drawdown_features(market))
        lq = self.liquidity.predict(extract_liquidity_features(market))
        sh = self.system_health.predict(extract_system_health_features(market))
        pc = self.pacing.predict(extract_pacing_features(market))
        return {
            "event_shock": {"score": es.score, "label": es.label, "confidence": es.confidence},
            "regime": {"score": rg.score, "label": rg.label, "confidence": rg.confidence},
            "drawdown": {"score": dd.score, "label": dd.label, "confidence": dd.confidence},
            "liquidity": {"score": lq.score, "label": lq.label, "confidence": lq.confidence},
            "system_health": {"score": sh.score, "label": sh.label, "confidence": sh.confidence},
            "pacing": {"score": pc.score, "label": pc.label, "confidence": pc.confidence},
        }

    # ── Symbolic overlay ────────────────────────────────────────

    def apply_symbolic_rules(
        self,
        scores: Dict[str, Any],
        intent_hint: str = "BUY",
    ) -> Dict[str, Any]:
        """Returns ``{"decision": Verdict, "reason": str, "rule": str}``.

        ``intent_hint`` is the upstream signal direction; default BUY
        means "if the regime is risk-off, this BUY would be vetoed".
        Symmetrically a SELL during risk-on regime is left to the
        Strategist — Perception veto is asymmetric on purpose since
        ``intent_hint`` arrives from the council *before* perception
        on most flows. The pipeline overrides ``intent_hint`` if a
        prior layer set it.
        """
        # R1
        if scores["system_health"]["score"] < 0.4:
            return {
                "decision": Verdict.NO_TRADE.value,
                "reason": "SYSTEM_DEGRADED",
                "rule": "R1",
            }
        # R2
        if scores["event_shock"]["label"] == "EVENT_HIGH":
            return {
                "decision": Verdict.NO_TRADE.value,
                "reason": "EVENT_SHOCK_ACTIVE",
                "rule": "R2",
            }
        # R3
        if scores["liquidity"]["label"] == "LIQ_THIN":
            return {
                "decision": Verdict.NO_TRADE.value,
                "reason": "LIQUIDITY_THIN",
                "rule": "R3",
            }
        # R4
        if scores["drawdown"]["label"] == "DD_DEEP":
            return {
                "decision": Verdict.NO_TRADE.value,
                "reason": "DRAWDOWN_DEEP",
                "rule": "R4",
            }
        # R5
        if scores["regime"]["label"] == "RISK_OFF" and intent_hint == "BUY":
            return {
                "decision": Verdict.NO_TRADE.value,
                "reason": "REGIME_RISK_OFF",
                "rule": "R5",
            }
        # Forward the intent.
        return {
            "decision": intent_hint if intent_hint in (Verdict.BUY.value, Verdict.SELL.value)
                        else Verdict.NO_TRADE.value,
            "reason": "PERCEPTION_PASS",
            "rule": "R0",
        }


class PerceptionML(BaseMLLayer):
    layer_id = "perception"
    can_approve = True
    shadow_only = False

    def __init__(self):
        super().__init__(lane=None)
        try:
            self.engine = AutonomousDecisionEngine()
            self._init_error: str | None = None
        except LaneDisabledError as exc:
            # An artifact env var pointed at a missing/broken file.
            # Boot will mark the lane disabled.
            self.engine = None
            self._init_error = exc.reason

    def boot(self) -> ModelBootReceipt:
        ready = self.engine is not None
        receipt = ModelBootReceipt(
            layer=self.layer_id,
            lane=None,
            ready=ready,
            artifact_path=None,
            artifact_present=ready,
            reason=None if ready else (self._init_error or "engine_init_failed"),
            can_approve=self.can_approve,
            shadow_only=self.shadow_only,
        )
        self._boot_receipt = receipt
        boot_receipts.register(receipt)
        return receipt

    def _predict(self, frame: FeatureFrame) -> MLVerdict:
        if self.engine is None:
            raise LaneDisabledError(self.layer_id, self._init_error or "engine_unavailable")
        scores = self.engine.evaluate_all(frame.market)
        intent_hint = (frame.extra.get("intent_hint") or "BUY").upper()
        symbolic = self.engine.apply_symbolic_rules(scores, intent_hint=intent_hint)
        # Enrich the frame so downstream layers can read perception output.
        frame.perception = {"scores": scores, "symbolic": symbolic}

        # Confidence is the average of the 6 sub-model confidences.
        conf = sum(scores[k]["confidence"] for k in scores) / 6.0
        return MLVerdict(
            layer=self.layer_id,
            decision=symbolic["decision"],
            confidence=conf,
            reason=symbolic["reason"],
            can_approve=self.can_approve,
            diagnostics={
                "scores": scores,
                "rule": symbolic["rule"],
                "intent_hint": intent_hint,
            },
        )
