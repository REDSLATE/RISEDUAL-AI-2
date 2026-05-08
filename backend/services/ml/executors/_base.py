"""Executor ML — shared scaffolding for the two lane-specific MLs."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import joblib

from services.ml import boot_receipts
from services.ml.base import BaseMLLayer
from services.ml.contracts import (
    FeatureFrame,
    LaneDisabledError,
    MLVerdict,
    ModelBootReceipt,
    Verdict,
)


def _load_artifact_or_none(env_var: str, lane: str):
    """Same fail-fast loader pattern as Perception.

    Returns None when the env var is unset (placeholder path).
    Raises LaneDisabledError when env var is set but file is missing
    or fails to deserialise — that's the operator's intent for that
    lane to be inert until they fix it.
    """
    path = os.getenv(env_var)
    if not path:
        return None, None
    p = Path(path)
    if not p.exists():
        raise LaneDisabledError(
            "executor",
            reason=f"missing_artifact:{env_var}={path}",
            lane=lane,
        )
    try:
        return joblib.load(p), str(p)
    except Exception as exc:  # noqa: BLE001
        raise LaneDisabledError(
            "executor",
            reason=f"artifact_load_failed:{env_var}:{type(exc).__name__}",
            lane=lane,
        ) from exc


class _BaseExecutorML(BaseMLLayer):
    """Shared placeholder logic for both lane MLs.

    Concrete subclasses pin ``lane`` and ``artifact_env``.
    """
    layer_id = "executor"
    can_approve = True
    shadow_only = False
    artifact_env: str = ""

    def __init__(self, *, lane: str):
        super().__init__(lane=lane)
        self._init_error: Optional[str] = None
        self._artifact_path: Optional[str] = None
        try:
            self._model, self._artifact_path = _load_artifact_or_none(
                self.artifact_env, lane=lane,
            )
        except LaneDisabledError as exc:
            self._model = None
            self._init_error = exc.reason

    def boot(self) -> ModelBootReceipt:
        ready = self._init_error is None
        receipt = ModelBootReceipt(
            layer=self.layer_id,
            lane=self.lane,
            ready=ready,
            artifact_path=self._artifact_path,
            artifact_present=bool(self._artifact_path),
            reason=self._init_error or (
                "artifact_loaded" if self._artifact_path else "placeholder_deterministic"
            ),
            can_approve=self.can_approve,
            shadow_only=self.shadow_only,
        )
        self._boot_receipt = receipt
        boot_receipts.register(receipt)
        return receipt

    def _predict(self, frame: FeatureFrame) -> MLVerdict:
        # Placeholder: respect auditor outcome unless fast_veto blocked.
        if self._init_error:
            raise LaneDisabledError(
                self.layer_id, self._init_error, lane=self.lane,
            )

        # Lane consistency check — never let a crypto signal flow
        # through the equity executor or vice versa. Pipeline routes
        # by lane already; this is belt-and-suspenders.
        if frame.lane != self.lane:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason="LANE_MISMATCH",
                can_approve=self.can_approve,
                diagnostics={"frame_lane": frame.lane, "executor_lane": self.lane},
            )

        # Veto wins.
        fv = frame.fast_veto or {}
        fv_decision = fv.get("decision")
        fv_reason = fv.get("reason")
        if fv_decision == Verdict.NO_TRADE.value and fv_reason and fv_reason != "FAST_VETO_ML_PASSTHROUGH":
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=f"EXECUTOR_RESPECT_VETO:{fv_reason}",
                can_approve=self.can_approve,
                diagnostics={"upstream": "fast_veto"},
            )

        auditor = frame.auditor or {}
        upstream_decision = auditor.get("decision") or Verdict.NO_TRADE.value
        upstream_conf = float(auditor.get("confidence") or 0.0)
        if upstream_decision == Verdict.NO_TRADE.value:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=auditor.get("reason") or "EXECUTOR_NO_AUDITOR_INPUT",
                can_approve=self.can_approve,
                diagnostics={"upstream": "auditor"},
            )

        return MLVerdict(
            layer=self.layer_id,
            decision=upstream_decision,
            confidence=upstream_conf,
            reason="EXECUTOR_APPROVE",
            can_approve=self.can_approve,
            diagnostics={
                "lane": self.lane,
                "upstream_conf": upstream_conf,
                "artifact_loaded": self._artifact_path is not None,
            },
        )
