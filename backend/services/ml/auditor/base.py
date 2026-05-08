"""Auditor ML — quality / consistency check.

Reads strategist output + perception scores. Looks for inconsistencies
that would indicate a noisy decision. Authority: full (can confirm
or downgrade — never flip direction). Currently deterministic; replace
with a real model via ``AUDITOR_ARTIFACT``.

Audit rules:
  A1. Strategist NO_TRADE -> NO_TRADE (inheritance)
  A2. perception.confidence avg < 0.30 -> NO_TRADE / AUDITOR_LOW_CONFIDENCE
  A3. drawdown.score > 0.85 -> NO_TRADE / AUDITOR_DD_DANGER
  A4. otherwise confirm with confidence = strategist * 0.9
"""
from __future__ import annotations

from services.ml import boot_receipts
from services.ml.base import BaseMLLayer
from services.ml.contracts import (
    FeatureFrame,
    MLVerdict,
    ModelBootReceipt,
    Verdict,
)


class AuditorML(BaseMLLayer):
    layer_id = "auditor"
    can_approve = True
    shadow_only = False

    def boot(self) -> ModelBootReceipt:
        receipt = ModelBootReceipt(
            layer=self.layer_id,
            lane=None,
            ready=True,
            artifact_path=None,
            artifact_present=True,
            reason="placeholder_deterministic",
            can_approve=self.can_approve,
            shadow_only=self.shadow_only,
        )
        self._boot_receipt = receipt
        boot_receipts.register(receipt)
        return receipt

    def _predict(self, frame: FeatureFrame) -> MLVerdict:
        strategist = frame.strategist or {}
        upstream_decision = strategist.get("decision") or Verdict.NO_TRADE.value
        upstream_reason = strategist.get("reason") or "NO_STRATEGIST_INPUT"
        upstream_conf = float(strategist.get("confidence") or 0.0)

        if upstream_decision == Verdict.NO_TRADE.value:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=upstream_reason,
                can_approve=self.can_approve,
                diagnostics={"upstream": "strategist"},
            )

        scores = (frame.perception or {}).get("scores") or {}
        if scores:
            avg_conf = sum(s["confidence"] for s in scores.values()) / len(scores)
            if avg_conf < 0.30:
                return MLVerdict(
                    layer=self.layer_id,
                    decision=Verdict.NO_TRADE.value,
                    confidence=avg_conf,
                    reason="AUDITOR_LOW_CONFIDENCE",
                    can_approve=self.can_approve,
                    diagnostics={"avg_perception_conf": avg_conf},
                )
            dd = scores.get("drawdown", {})
            if float(dd.get("score") or 0.0) > 0.85:
                return MLVerdict(
                    layer=self.layer_id,
                    decision=Verdict.NO_TRADE.value,
                    confidence=0.5,
                    reason="AUDITOR_DD_DANGER",
                    can_approve=self.can_approve,
                    diagnostics={"drawdown_score": dd.get("score")},
                )

        return MLVerdict(
            layer=self.layer_id,
            decision=upstream_decision,
            confidence=upstream_conf * 0.9,
            reason="AUDITOR_CONFIRM",
            can_approve=self.can_approve,
            diagnostics={"upstream": "strategist"},
        )
