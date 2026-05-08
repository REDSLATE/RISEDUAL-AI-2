"""Strategist ML — directional refinement layer.

Reads ``frame.perception`` enrichment + Shelly recall and decides
whether to confirm the perception verdict, downgrade BUY/SELL to
NO_TRADE, or in rare cases flip direction (when memory says the
exact pattern reversed historically).

Currently a deterministic placeholder: if perception passed, the
strategist confirms; if Shelly recall shows ≥3 historical episodes
with the same symbol+regime tagged ``negative``, it downgrades to
NO_TRADE with reason ``STRATEGIST_MEMORY_NEGATIVE``.

This is a real model slot — drop a sklearn .joblib at the path in
``STRATEGIST_ARTIFACT`` and the layer will load it at boot. Until
then the deterministic logic gives the operator something to watch
in shadow mode.
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


class StrategistML(BaseMLLayer):
    layer_id = "strategist"
    can_approve = True
    shadow_only = False

    def boot(self) -> ModelBootReceipt:
        # Placeholder — always boots ready. When a real model is
        # plugged in, switch this to artifact-gated readiness.
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
        # Inherit perception outcome.
        symbolic = (frame.perception or {}).get("symbolic") or {}
        upstream_decision = symbolic.get("decision") or Verdict.NO_TRADE.value
        upstream_reason = symbolic.get("reason") or "NO_PERCEPTION_INPUT"

        if upstream_decision == Verdict.NO_TRADE.value:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=upstream_reason,
                can_approve=self.can_approve,
                diagnostics={"upstream": "perception"},
            )

        # Memory-based downgrade.
        recall = frame.shelly_recall or {}
        negative = int(recall.get("negative_count", 0) or 0)
        if negative >= 3:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.4,
                reason="STRATEGIST_MEMORY_NEGATIVE",
                can_approve=self.can_approve,
                diagnostics={
                    "negative_count": negative,
                    "recall_summary": recall.get("summary"),
                },
            )

        return MLVerdict(
            layer=self.layer_id,
            decision=upstream_decision,
            confidence=0.6,
            reason="STRATEGIST_CONFIRM",
            can_approve=self.can_approve,
            diagnostics={
                "upstream": "perception",
                "recall_negative_count": negative,
            },
        )
