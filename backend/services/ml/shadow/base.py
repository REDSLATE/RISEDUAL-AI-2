"""Shadow ML — observation-only telemetry layer.

This is layer 5 in the canonical pipeline. It sees the post-veto
state and writes telemetry. It NEVER alters the verdict. The base
class :class:`ShadowMLLayer` already seals execute/place_order/etc.;
this concrete layer adds the layer_id + observe() implementation.

Boot is always ready; failures inside observe() are caught by the
pipeline so a misbehaving shadow logger can't take the chain down.
"""
from __future__ import annotations

from typing import Any, Dict

from services.ml import boot_receipts
from services.ml.base import ShadowMLLayer
from services.ml.contracts import FeatureFrame, ModelBootReceipt


class ShadowML(ShadowMLLayer):
    layer_id = "shadow"

    def boot(self) -> ModelBootReceipt:
        receipt = ModelBootReceipt(
            layer=self.layer_id,
            lane=None,
            ready=True,
            artifact_path=None,
            artifact_present=True,
            reason="shadow_observation_only",
            can_approve=False,
            shadow_only=True,
        )
        self._boot_receipt = receipt
        boot_receipts.register(receipt)
        return receipt

    def observe(self, frame: FeatureFrame) -> Dict[str, Any]:
        # Capture key signal measurements for later analysis.
        scores = (frame.perception or {}).get("scores") or {}
        recall = frame.shelly_recall or {}
        return {
            "regime_label": (scores.get("regime") or {}).get("label"),
            "drawdown_score": (scores.get("drawdown") or {}).get("score"),
            "negative_recall": int(recall.get("negative_count", 0) or 0),
            "positive_recall": int(recall.get("positive_count", 0) or 0),
            "auditor_decision": (frame.auditor or {}).get("decision"),
            "fast_veto_decision": (frame.fast_veto or {}).get("decision"),
        }
