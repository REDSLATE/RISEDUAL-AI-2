"""Fast Veto ML slot.

Distinct from the legacy ``services/fast_veto_layer.py`` rule cascade.
This is the **ML half** of the dual fast-veto layer — it sees the
upstream auditor verdict + perception scores and may VETO a trade
that the deterministic rule cascade let through.

Authority: VETO ONLY. ``can_approve`` is hard False. The layer can
only return:
  * NO_TRADE with a block reason (veto applied), or
  * passthrough = upstream decision (no veto).

Until a real .joblib lands at ``FAST_VETO_ML_ARTIFACT``, this is a
deterministic placeholder that vetoes when the **product** of
perception confidences is below 0.10 — i.e. all 6 sub-models are
collectively under-confident.
"""
from __future__ import annotations

import os
from functools import reduce

from services.ml import boot_receipts
from services.ml.base import VetoOnlyMLLayer
from services.ml.contracts import (
    FeatureFrame,
    MLVerdict,
    ModelBootReceipt,
    Verdict,
)

ARTIFACT_ENV = "FAST_VETO_ML_ARTIFACT"
CONFIDENCE_PRODUCT_FLOOR = float(os.getenv("FAST_VETO_ML_CONF_PRODUCT_FLOOR", "0.10"))


class FastVetoMLLayer(VetoOnlyMLLayer):
    layer_id = "fast_veto"

    def boot(self) -> ModelBootReceipt:
        artifact_path = os.getenv(ARTIFACT_ENV)
        artifact_present = bool(artifact_path) and os.path.exists(artifact_path)
        # If the env var is set but the file is missing, the lane is
        # disabled (fail-fast). If the env var is unset, the lane uses
        # the deterministic placeholder — still ready=True.
        ready = (not artifact_path) or artifact_present
        reason = None
        if artifact_path and not artifact_present:
            reason = f"missing_artifact:{ARTIFACT_ENV}={artifact_path}"
        receipt = ModelBootReceipt(
            layer=self.layer_id,
            lane=None,
            ready=ready,
            artifact_path=artifact_path,
            artifact_present=artifact_present if artifact_path else True,
            reason=reason or ("placeholder_deterministic" if not artifact_path else None),
            can_approve=False,
            shadow_only=False,
        )
        self._boot_receipt = receipt
        boot_receipts.register(receipt)
        return receipt

    def _predict(self, frame: FeatureFrame) -> MLVerdict:
        # Read upstream auditor verdict.
        auditor = frame.auditor or {}
        upstream_decision = auditor.get("decision") or Verdict.NO_TRADE.value
        upstream_reason = auditor.get("reason") or "NO_AUDITOR_INPUT"

        # If auditor already said NO_TRADE, passthrough.
        if upstream_decision == Verdict.NO_TRADE.value:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=upstream_reason,
                can_approve=False,
                diagnostics={"upstream": "auditor"},
            )

        scores = (frame.perception or {}).get("scores") or {}
        if scores:
            confs = [float(s["confidence"]) for s in scores.values()]
            product = reduce(lambda a, b: a * b, confs, 1.0)
            if product < CONFIDENCE_PRODUCT_FLOOR:
                return MLVerdict(
                    layer=self.layer_id,
                    decision=Verdict.NO_TRADE.value,
                    confidence=product,
                    reason="FAST_VETO_ML_LOW_PRODUCT",
                    can_approve=False,
                    diagnostics={
                        "confidence_product": product,
                        "floor": CONFIDENCE_PRODUCT_FLOOR,
                    },
                )

        # Passthrough (no veto). The base class will normalise
        # can_approve to False even though we're forwarding BUY/SELL —
        # the AUTHORITY_VIOLATION check would trip. Encode passthrough
        # as a NO_TRADE here and let the pipeline preserve the
        # auditor verdict if no veto fires.
        return MLVerdict(
            layer=self.layer_id,
            decision=Verdict.NO_TRADE.value,
            confidence=0.0,
            reason="FAST_VETO_ML_PASSTHROUGH",
            can_approve=False,
            diagnostics={
                "passthrough": True,
                "upstream_decision": upstream_decision,
            },
        )
