"""Strategist ML — real feature-engineered classifier.

Reads ``frame.perception.scores`` + ``frame.shelly_recall`` and
produces a calibrated BUY/SELL/NO_TRADE verdict via a sklearn
``RandomForestClassifier``. Three-class output (sell=0, no_trade=1,
buy=2) so the model can flip direction in rare cases — but the seal
``can_approve`` is enforced by the base class.

Artifact-gated: if ``STRATEGIST_ARTIFACT`` env points at a real
.joblib, we load it. Otherwise we fall back to a balanced synthetic
training set generated in-process — this gives the lane real ML
inference (not deterministic if/else) so the Strategist's
diagnostics are observable on the Promotion Diff.

Feature vector (10 dims, all in [0, 1]):
   0: perception.event_shock.score
   1: perception.regime.score (multi-class softmax max)
   2: perception.drawdown.score
   3: perception.liquidity.score
   4: perception.system_health.score
   5: perception.pacing.score
   6: avg perception confidence
   7: shelly recall positive ratio
   8: shelly recall negative ratio
   9: intent_hint encoded (BUY=1, SELL=0)

Label rule for synthetic training:
  BUY     when system_health, liquidity, regime all > 0.5 AND
          drawdown < 0.7 AND event_shock < 0.5
  NO_TRADE when system_health < 0.4 OR liquidity < 0.3
  SELL    when negative recall ratio > 0.6 AND drawdown > 0.6
  else NO_TRADE (default)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

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


_FEATURE_DIM = 10
_LABELS = {0: Verdict.SELL.value, 1: Verdict.NO_TRADE.value, 2: Verdict.BUY.value}


def _build_features(frame: FeatureFrame) -> np.ndarray:
    scores = (frame.perception or {}).get("scores") or {}
    recall = frame.shelly_recall or {}
    intent = (frame.extra or {}).get("intent_hint") or "BUY"

    def s(key: str, sub: str = "score") -> float:
        return float((scores.get(key) or {}).get(sub) or 0.0)

    confs = [float((scores.get(k) or {}).get("confidence") or 0.0)
             for k in ("event_shock", "regime", "drawdown",
                       "liquidity", "system_health", "pacing")]
    avg_conf = sum(confs) / 6.0 if confs else 0.0

    found = max(1, int(recall.get("episodes_found") or 0))
    pos_ratio = float(recall.get("positive_count") or 0) / found
    neg_ratio = float(recall.get("negative_count") or 0) / found

    return np.array([
        s("event_shock"),
        s("regime"),
        s("drawdown"),
        s("liquidity"),
        s("system_health"),
        s("pacing"),
        avg_conf,
        max(0.0, min(1.0, pos_ratio)),
        max(0.0, min(1.0, neg_ratio)),
        1.0 if intent == "BUY" else 0.0,
    ], dtype=np.float64)


def _train_placeholder(seed: int = 4242):
    """Synthetic-realistic training set with the label rule from the
    docstring. Balanced via oversampling so each class has equal
    representation."""
    rng = np.random.default_rng(seed)
    samples = 768
    X = rng.uniform(0.0, 1.0, size=(samples, _FEATURE_DIM))
    y = np.full(samples, 1, dtype=int)  # default NO_TRADE
    for i in range(samples):
        ev, reg, dd, liq, sys_h, _pace, _conf, _pos, neg, _intent = X[i]
        if sys_h < 0.4 or liq < 0.3:
            y[i] = 1
        elif neg > 0.6 and dd > 0.6:
            y[i] = 0
        elif sys_h > 0.5 and liq > 0.5 and reg > 0.5 and dd < 0.7 and ev < 0.5:
            y[i] = 2
        # else stays NO_TRADE
    # Balance via oversampling
    from collections import Counter
    counts = Counter(int(v) for v in y)
    target = max(counts.values()) if counts else 0
    Xs, ys = [X], [y]
    for cls in (0, 1, 2):
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
    path = os.getenv("STRATEGIST_ARTIFACT")
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise LaneDisabledError(
            "strategist", reason=f"missing_artifact:STRATEGIST_ARTIFACT={path}",
        )
    try:
        return joblib.load(p)
    except Exception as exc:  # noqa: BLE001
        raise LaneDisabledError(
            "strategist", reason=f"artifact_load_failed:{type(exc).__name__}",
        ) from exc


class StrategistML(BaseMLLayer):
    layer_id = "strategist"
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
        from services.ml.model_age import evaluate_artifact, stale_reason

        artifact_path = os.getenv("STRATEGIST_ARTIFACT")
        stale, age_hours, max_age = evaluate_artifact(artifact_path)

        # Stale-model protection: even if the artifact loaded fine,
        # an old .joblib forces observe-only.
        ready = self._model is not None and not stale
        reason = self._init_error
        if stale and not reason:
            reason = stale_reason(age_hours, max_age)
        elif not reason:
            reason = (
                "artifact_loaded" if self._artifact_loaded else "placeholder_classifier"
            )

        receipt = ModelBootReceipt(
            layer=self.layer_id,
            lane=None,
            ready=ready,
            artifact_path=artifact_path,
            artifact_present=self._artifact_loaded,
            reason=reason,
            can_approve=self.can_approve,
            shadow_only=self.shadow_only,
            model_age_hours=age_hours,
            stale=stale,
        )
        self._boot_receipt = receipt
        boot_receipts.register(receipt)
        return receipt

    def _predict(self, frame: FeatureFrame) -> MLVerdict:
        if self._model is None:
            raise LaneDisabledError(
                self.layer_id, self._init_error or "model_unavailable",
            )

        # Inherit perception's NO_TRADE — strategist only refines positive intents.
        symbolic = (frame.perception or {}).get("symbolic") or {}
        upstream_decision = symbolic.get("decision") or Verdict.NO_TRADE.value
        if upstream_decision == Verdict.NO_TRADE.value:
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason=symbolic.get("reason") or "PERCEPTION_NO_TRADE",
                can_approve=self.can_approve,
                diagnostics={"upstream": "perception", "model_skipped": True},
            )

        # ── Feature-health weighting (Phase 5d safety) ──
        # Stash on the frame so heartbeat / decision-log receipts can read
        # the same score the Strategist used.
        from services.ml.feature_health import compute_feature_health, should_force_hold
        feature_health_score, fh_diag = compute_feature_health(frame)
        frame.extra["feature_health_score"] = feature_health_score
        frame.extra["feature_health_diag"] = fh_diag

        if should_force_hold(feature_health_score):
            return MLVerdict(
                layer=self.layer_id,
                decision=Verdict.NO_TRADE.value,
                confidence=0.0,
                reason="STRATEGIST_FEATURE_HEALTH_LOW",
                can_approve=self.can_approve,
                diagnostics={
                    "feature_health_score": feature_health_score,
                    "feature_health": fh_diag,
                    "model_skipped": True,
                },
            )

        feats = _build_features(frame).reshape(1, -1)
        proba = self._model.predict_proba(feats)[0]
        cls = int(np.argmax(proba))
        decision = _LABELS.get(cls, Verdict.NO_TRADE.value)
        raw_conf = float(proba[cls])
        # Clamp confidence by feature health.
        effective_conf = raw_conf * feature_health_score
        clamped = effective_conf < raw_conf - 1e-9
        # Reason carries the dominant class + confidence delta.
        if decision == upstream_decision:
            reason = "STRATEGIST_CONFIRM_CLAMPED" if clamped else "STRATEGIST_CONFIRM"
        elif decision == Verdict.NO_TRADE.value:
            reason = "STRATEGIST_DOWNGRADE"
        else:
            reason = "STRATEGIST_FLIP_CLAMPED" if clamped else "STRATEGIST_FLIP"

        return MLVerdict(
            layer=self.layer_id,
            decision=decision,
            confidence=effective_conf,
            reason=reason,
            can_approve=self.can_approve,
            diagnostics={
                "proba": proba.tolist(),
                "feature_dim": _FEATURE_DIM,
                "artifact_loaded": self._artifact_loaded,
                "raw_confidence": raw_conf,
                "effective_confidence": effective_conf,
                "feature_health_score": feature_health_score,
                "feature_health": fh_diag,
                "clamped": clamped,
            },
        )
