"""Auditor ML — real feature-engineered consistency check.

Cross-validates Strategist + Perception with a binary classifier
that says "this signal is internally consistent" vs "downgrade to
NO_TRADE". Authority: full (BUY/SELL/NO_TRADE — never flips
direction; only confirm or downgrade).

Artifact-gated via ``AUDITOR_ARTIFACT``. Falls back to a balanced
synthetic-trained ``RandomForestClassifier`` when no artifact is
present.

Feature vector (8 dims, all in [0, 1]):
   0: strategist confidence
   1: avg perception confidence
   2: perception.regime.score
   3: perception.drawdown.score
   4: perception.system_health.score
   5: perception.liquidity.score
   6: shelly recall positive ratio
   7: shelly recall negative ratio

Label rule for synthetic training:
  CONFIRM (1) when avg_perception_conf >= 0.30 AND drawdown < 0.85
                   AND system_health > 0.4 AND strategist_conf > 0.5
  DOWNGRADE (0) otherwise
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from services.ml import boot_receipts
from services.ml.base import BaseMLLayer
from services.ml.contracts import (
    FeatureFrame,
    LaneDisabledError,
    MLVerdict,
    ModelBootReceipt,
    Verdict,
)

logger = logging.getLogger(__name__)


_FEATURE_DIM = 8


def _build_features(frame: FeatureFrame) -> np.ndarray:
    scores = (frame.perception or {}).get("scores") or {}
    strategist = frame.strategist or {}
    recall = frame.shelly_recall or {}

    def s(key: str) -> float:
        return float((scores.get(key) or {}).get("score") or 0.0)

    confs = [float((scores.get(k) or {}).get("confidence") or 0.0)
             for k in ("event_shock", "regime", "drawdown",
                       "liquidity", "system_health", "pacing")]
    avg_conf = sum(confs) / 6.0 if confs else 0.0

    strat_conf = float(strategist.get("confidence") or 0.0)

    found = max(1, int(recall.get("episodes_found") or 0))
    pos_ratio = float(recall.get("positive_count") or 0) / found
    neg_ratio = float(recall.get("negative_count") or 0) / found

    return np.array([
        strat_conf,
        avg_conf,
        s("regime"),
        s("drawdown"),
        s("system_health"),
        s("liquidity"),
        max(0.0, min(1.0, pos_ratio)),
        max(0.0, min(1.0, neg_ratio)),
    ], dtype=np.float64)


def _train_placeholder(seed: int = 5151):
    rng = np.random.default_rng(seed)
    samples = 512
    X = rng.uniform(0.0, 1.0, size=(samples, _FEATURE_DIM))
    y = np.zeros(samples, dtype=int)
    for i in range(samples):
        strat_c, avg_c, _reg, dd, sys_h, liq, _pos, _neg = X[i]
        if (avg_c >= 0.30 and dd < 0.85 and sys_h > 0.4
                and strat_c > 0.5 and liq > 0.3):
            y[i] = 1
    # Balance via oversampling
    from collections import Counter
    counts = Counter(int(v) for v in y)
    target = max(counts.values()) if counts else 0
    Xs, ys = [X], [y]
    for cls in (0, 1):
        cur = counts.get(cls, 0)
        need = max(0, target - cur)
        if need <= 0 or cur == 0:
            continue
        idx = np.where(y == cls)[0]
        extra = idx[np.arange(need) % len(idx)]
        Xs.append(X[extra])
        ys.append(y[extra])
    X_bal = np.vstack(Xs)
    y_bal = np.concatenate(ys)
    clf = RandomForestClassifier(n_estimators=48, max_depth=10, random_state=seed)
    clf.fit(X_bal, y_bal)
    return clf


def _try_load_artifact() -> Optional[object]:
    path = os.getenv("AUDITOR_ARTIFACT")
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise LaneDisabledError(
            "auditor", reason=f"missing_artifact:AUDITOR_ARTIFACT={path}",
        )
    try:
        return joblib.load(p)
    except Exception as exc:  # noqa: BLE001
        raise LaneDisabledError(
            "auditor", reason=f"artifact_load_failed:{type(exc).__name__}",
        ) from exc


class AuditorML(BaseMLLayer):
    layer_id = "auditor"
    can_approve = True
    shadow_only = False

    def __init__(self):
        super().__init__(lane=None)
        self._init_error: Optional[str] = None
        try:
            loaded = _try_load_artifact()
            self._model = loaded if loaded is not None else _train_placeholder()
            self._artifact_loaded = loaded is not None
        except LaneDisabledError as exc:
            self._model = None
            self._init_error = exc.reason
            self._artifact_loaded = False

    def boot(self) -> ModelBootReceipt:
        ready = self._model is not None
        receipt = ModelBootReceipt(
            layer=self.layer_id,
            lane=None,
            ready=ready,
            artifact_path=os.getenv("AUDITOR_ARTIFACT"),
            artifact_present=self._artifact_loaded,
            reason=self._init_error or (
                "artifact_loaded" if self._artifact_loaded else "placeholder_classifier"
            ),
            can_approve=self.can_approve,
            shadow_only=self.shadow_only,
        )
        self._boot_receipt = receipt
        boot_receipts.register(receipt)
        return receipt

    def _predict(self, frame: FeatureFrame) -> MLVerdict:
        if self._model is None:
            raise LaneDisabledError(
                self.layer_id, self._init_error or "model_unavailable",
            )

        strategist = frame.strategist or {}
        upstream_decision = strategist.get("decision") or Verdict.NO_TRADE.value
        if upstream_decision == Verdict.NO_TRADE.value:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=strategist.get("reason") or "STRATEGIST_NO_TRADE",
                can_approve=self.can_approve,
                diagnostics={"upstream": "strategist", "model_skipped": True},
            )

        feats = _build_features(frame).reshape(1, -1)
        proba = self._model.predict_proba(feats)[0]
        confirm_p = float(proba[1]) if len(proba) > 1 else float(proba[0])

        if confirm_p >= 0.5:
            return MLVerdict(
                layer=self.layer_id,
                decision=upstream_decision,  # confirm — never flip direction
                confidence=confirm_p,
                reason="AUDITOR_CONFIRM",
                can_approve=self.can_approve,
                diagnostics={
                    "confirm_proba": confirm_p,
                    "artifact_loaded": self._artifact_loaded,
                },
            )

        return MLVerdict(
            layer=self.layer_id,
            decision=Verdict.NO_TRADE.value,
            confidence=1.0 - confirm_p,
            reason="AUDITOR_DOWNGRADE",
            can_approve=self.can_approve,
            diagnostics={
                "confirm_proba": confirm_p,
                "artifact_loaded": self._artifact_loaded,
            },
        )
