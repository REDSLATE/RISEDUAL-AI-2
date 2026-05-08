"""Perception ML — 6 dummy-trained sklearn sub-models.

These are placeholders. The shape, output range, and feature contract
are real; the training data is synthetic (small in-memory bootstrap)
so the lane has a deterministic, non-zero confidence and the pipeline
can run end-to-end without external data.

Replacing each dummy with a real model is a per-model task — flip the
``MODEL_ARTIFACT_PATH`` env var, drop the .joblib in, and the loader
picks it up. If an artifact path is set but missing, the lane fails
fast (LaneDisabledError -> NO_TRADE) instead of silently using the
dummy.

Six sub-models, one per perception axis:
  1. EventShockModel       — news / catalyst shock magnitude
  2. RegimeStateModel      — risk-on / risk-off / chop classifier
  3. DrawdownDistanceModel — distance from recent peak in stdev units
  4. LiquidityModel        — relative liquidity vs 20-day avg
  5. SystemHealthModel     — broker/data-pipeline health
  6. PacingModel           — intraday pacing vs typical day
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression

from services.ml.contracts import LaneDisabledError

logger = logging.getLogger(__name__)


# ── Feature width contracts ───────────────────────────────────────


_EVENT_SHOCK_FEATURES = 4   # news_count, sentiment, catalyst_present, hours_to_event
_REGIME_FEATURES = 5        # vix, breadth, momentum, sector_rotation, dispersion
_DRAWDOWN_FEATURES = 3      # current_dd_pct, dd_velocity, atr_normalised
_LIQUIDITY_FEATURES = 4     # rel_volume, spread_bps, depth_top_5, time_of_day
_SYSTEM_HEALTH_FEATURES = 4 # broker_uptime, data_lag_ms, error_rate, pipeline_latency_ms
_PACING_FEATURES = 4        # intraday_progress, vol_progress, range_progress, trade_progress


@dataclass(frozen=True)
class ModelOutput:
    """Uniform output shape across all 6 sub-models.

    ``score`` ∈ [0, 1]. Higher = stronger signal (definition is
    sub-model specific). ``confidence`` ∈ [0, 1]. Higher = more
    certain. ``label`` is a short token (e.g. "RISK_ON", "EVENT_LOW")
    used by the symbolic engine.
    """
    score: float
    confidence: float
    label: str
    diagnostics: Dict[str, Any]


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _try_load(env_var: str) -> Optional[object]:
    """Load a joblib artifact if the env var points at a real file.

    If the env var is set but the file does not exist, we raise
    LaneDisabledError. If the env var is unset, we return None and
    the caller falls back to its dummy-trained model — that's the
    only path where a placeholder is allowed.
    """
    path = os.getenv(env_var)
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise LaneDisabledError(
            "perception",
            reason=f"missing_artifact:{env_var}={path}",
        )
    try:
        return joblib.load(p)
    except Exception as exc:  # noqa: BLE001
        raise LaneDisabledError(
            "perception",
            reason=f"artifact_load_failed:{env_var}:{type(exc).__name__}",
        ) from exc


class _DummyTrainer:
    """Deterministic synthetic-training helper.

    Each sub-model uses a domain-informed label function so the
    trained dummy correlates sensibly with feature semantics. This
    lets the operator watch shadow-mode behaviour without any real
    training data; the moment a real .joblib lands, the slot
    swaps out with no engine code changes.
    """

    @staticmethod
    def _sample(width: int, samples: int, seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        # Sample around the reference operating points so the model
        # sees both healthy + unhealthy regions of the feature space.
        return rng.standard_normal((samples, width)) * 0.5 + 0.5

    @staticmethod
    def _balance(X: np.ndarray, y: np.ndarray, *, classes: int) -> tuple:
        """Oversample minority classes so each is at least 25% of the
        dataset. The dummy data generators tend to be very imbalanced
        because the label rules are conjunctive."""
        from collections import Counter
        counts = Counter(int(v) for v in y)
        target = max(counts.values()) if counts else 0
        Xs = [X]
        ys = [y]
        for cls in range(classes):
            cnt = counts.get(cls, 0)
            if cnt == 0:
                # Synthesise one row from class mean if class is empty.
                continue
            need = max(0, target - cnt)
            if need <= 0:
                continue
            idx = np.where(y == cls)[0]
            extra = idx[np.arange(need) % len(idx)]
            Xs.append(X[extra])
            ys.append(y[extra])
        X_bal = np.vstack(Xs)
        y_bal = np.concatenate(ys)
        return X_bal, y_bal

    @staticmethod
    def fit_classifier(X: np.ndarray, y: np.ndarray, seed: int):
        X, y = _DummyTrainer._balance(X, y, classes=2)
        if len(set(y.tolist())) < 2:
            y = y.copy()
            y[0] = 0
            y[-1] = 1
        # RandomForest captures conjunctive label rules (e.g.
        # "all features above threshold") that LogisticRegression's
        # linear boundary cannot.
        clf = RandomForestClassifier(n_estimators=32, max_depth=8, random_state=seed)
        clf.fit(X, y)
        return clf

    @staticmethod
    def fit_multiclass(X: np.ndarray, y: np.ndarray, seed: int, classes: int):
        X, y = _DummyTrainer._balance(X, y, classes=classes)
        for c in range(classes):
            y[c] = c
        clf = RandomForestClassifier(n_estimators=24, random_state=seed)
        clf.fit(X, y)
        return clf

    @staticmethod
    def fit_regressor(X: np.ndarray, y: np.ndarray, seed: int):
        reg = RandomForestRegressor(n_estimators=24, random_state=seed)
        reg.fit(X, y)
        return reg


# ── Sub-models ────────────────────────────────────────────────────


class EventShockModel:
    """Predict probability of an active event-shock state."""
    artifact_env = "PERCEPTION_EVENT_SHOCK_ARTIFACT"

    def __init__(self):
        loaded = _try_load(self.artifact_env)
        if loaded is not None:
            self._model = loaded
        else:
            X = _DummyTrainer._sample(_EVENT_SHOCK_FEATURES, 256, seed=11)
            # Label = high event shock when news_count high, sentiment
            # extreme, catalyst present, hours_to_event small.
            y = (
                (X[:, 0] > 0.7)               # news_count
                | (np.abs(X[:, 1]) > 0.8)     # sentiment magnitude
                | (X[:, 2] > 0.5)             # catalyst_present
                | (X[:, 3] < 0.2)             # hours_to_event small
            ).astype(int)
            self._model = _DummyTrainer.fit_classifier(X, y, seed=11)

    def predict(self, features: np.ndarray) -> ModelOutput:
        proba = self._model.predict_proba(features.reshape(1, -1))[0]
        score = float(proba[1]) if len(proba) > 1 else float(proba[0])
        label = "EVENT_HIGH" if score >= 0.6 else "EVENT_LOW"
        return ModelOutput(
            score=_clip01(score),
            confidence=_clip01(abs(score - 0.5) * 2),
            label=label,
            diagnostics={"proba": proba.tolist()},
        )


class RegimeStateModel:
    """Classify market regime: 0=risk-off, 1=chop, 2=risk-on."""
    artifact_env = "PERCEPTION_REGIME_STATE_ARTIFACT"
    _LABELS = {0: "RISK_OFF", 1: "CHOP", 2: "RISK_ON"}

    def __init__(self):
        loaded = _try_load(self.artifact_env)
        if loaded is not None:
            self._model = loaded
        else:
            X = _DummyTrainer._sample(_REGIME_FEATURES, 256, seed=22)
            # vix high + breadth low + momentum negative -> risk-off
            # vix low + breadth high + momentum positive -> risk-on
            score = -X[:, 0] + X[:, 1] + X[:, 2]
            y = np.where(score < -0.3, 0, np.where(score > 0.3, 2, 1)).astype(int)
            self._model = _DummyTrainer.fit_multiclass(X, y, seed=22, classes=3)

    def predict(self, features: np.ndarray) -> ModelOutput:
        proba = self._model.predict_proba(features.reshape(1, -1))[0]
        cls = int(np.argmax(proba))
        return ModelOutput(
            score=float(proba[cls]),
            confidence=float(proba[cls]),
            label=self._LABELS.get(cls, "UNKNOWN"),
            diagnostics={"proba": proba.tolist(), "class": cls},
        )


class DrawdownDistanceModel:
    """Regress current drawdown distance from peak (stdev units).

    Score normalised to [0,1] via a sigmoid: 0 = at peak, 1 = far below.
    """
    artifact_env = "PERCEPTION_DRAWDOWN_ARTIFACT"

    def __init__(self):
        loaded = _try_load(self.artifact_env)
        if loaded is not None:
            self._model = loaded
        else:
            X = _DummyTrainer._sample(_DRAWDOWN_FEATURES, 256, seed=33)
            # Label = magnitude of drawdown (sum of dd_pct + dd_velocity)
            y = X[:, 0] + 0.5 * X[:, 1]
            self._model = _DummyTrainer.fit_regressor(X, y, seed=33)

    def predict(self, features: np.ndarray) -> ModelOutput:
        raw = float(self._model.predict(features.reshape(1, -1))[0])
        score = _clip01(1.0 / (1.0 + np.exp(-raw)))
        label = "DD_DEEP" if score >= 0.7 else ("DD_NEAR_PEAK" if score <= 0.3 else "DD_NORMAL")
        return ModelOutput(
            score=score,
            confidence=_clip01(abs(score - 0.5) * 2),
            label=label,
            diagnostics={"raw": raw},
        )


class LiquidityModel:
    """Classify liquidity adequacy: 0=thin, 1=normal."""
    artifact_env = "PERCEPTION_LIQUIDITY_ARTIFACT"

    def __init__(self):
        loaded = _try_load(self.artifact_env)
        if loaded is not None:
            self._model = loaded
        else:
            X = _DummyTrainer._sample(_LIQUIDITY_FEATURES, 256, seed=44)
            # liquid when rel_volume high AND spread low AND depth high
            y = (
                (X[:, 0] > 0.4)               # rel_volume above ~half
                & (X[:, 1] < 0.7)             # spread low-ish
                & (X[:, 2] > 0.4)             # depth ok
            ).astype(int)
            self._model = _DummyTrainer.fit_classifier(X, y, seed=44)

    def predict(self, features: np.ndarray) -> ModelOutput:
        proba = self._model.predict_proba(features.reshape(1, -1))[0]
        score = float(proba[1]) if len(proba) > 1 else float(proba[0])
        return ModelOutput(
            score=_clip01(score),
            confidence=_clip01(abs(score - 0.5) * 2),
            label="LIQ_OK" if score >= 0.5 else "LIQ_THIN",
            diagnostics={"proba": proba.tolist()},
        )


class SystemHealthModel:
    """Score system health: 0=degraded, 1=healthy."""
    artifact_env = "PERCEPTION_SYSTEM_HEALTH_ARTIFACT"

    def __init__(self):
        loaded = _try_load(self.artifact_env)
        if loaded is not None:
            self._model = loaded
        else:
            X = _DummyTrainer._sample(_SYSTEM_HEALTH_FEATURES, 256, seed=55)
            # All features post-normalisation: uptime higher = healthier;
            # data_lag/error_rate/latency LOWER = healthier (raw → normalised)
            y = (
                (X[:, 0] > 0.6)               # broker_uptime high
                & (X[:, 1] < 0.4)             # data_lag low
                & (X[:, 2] < 0.4)             # error_rate low
                & (X[:, 3] < 0.4)             # pipeline_latency low
            ).astype(int)
            self._model = _DummyTrainer.fit_classifier(X, y, seed=55)

    def predict(self, features: np.ndarray) -> ModelOutput:
        proba = self._model.predict_proba(features.reshape(1, -1))[0]
        score = float(proba[1]) if len(proba) > 1 else float(proba[0])
        return ModelOutput(
            score=_clip01(score),
            confidence=_clip01(abs(score - 0.5) * 2),
            label="HEALTHY" if score >= 0.5 else "DEGRADED",
            diagnostics={"proba": proba.tolist()},
        )


class PacingModel:
    """Score intraday pacing: 0=behind, 1=on track."""
    artifact_env = "PERCEPTION_PACING_ARTIFACT"

    def __init__(self):
        loaded = _try_load(self.artifact_env)
        if loaded is not None:
            self._model = loaded
        else:
            X = _DummyTrainer._sample(_PACING_FEATURES, 256, seed=66)
            # on-pace when intraday_progress matches vol_progress AND range_progress
            y = (
                (X[:, 1] >= X[:, 0] - 0.2)    # vol on or ahead of pace
                & (X[:, 2] >= X[:, 0] - 0.2)  # range on or ahead of pace
            ).astype(int)
            self._model = _DummyTrainer.fit_classifier(X, y, seed=66)

    def predict(self, features: np.ndarray) -> ModelOutput:
        proba = self._model.predict_proba(features.reshape(1, -1))[0]
        score = float(proba[1]) if len(proba) > 1 else float(proba[0])
        return ModelOutput(
            score=_clip01(score),
            confidence=_clip01(abs(score - 0.5) * 2),
            label="ON_PACE" if score >= 0.5 else "BEHIND_PACE",
            diagnostics={"proba": proba.tolist()},
        )


# ── Feature extraction ────────────────────────────────────────────
#
# All extractors emit values roughly in [0, 1] so they match the
# placeholder training distribution. Operators pass raw market values
# (vix=18, spread_bps=4, lag_ms=50, …); we normalise per-feature with
# soft caps tuned to typical session ranges. The moment a real
# .joblib lands the normalisation is preserved — real models will
# also be trained on the same [0, 1] feature space.


def _norm(value, lo: float, hi: float) -> float:
    """Linear clip to [0, 1]."""
    if hi <= lo:
        return 0.0
    x = (float(value) - lo) / (hi - lo)
    return max(0.0, min(1.0, x))


def extract_event_shock_features(market: Dict[str, Any]) -> np.ndarray:
    return np.array([
        _norm(market.get("news_count", 0.0), 0, 10),
        _norm(market.get("news_sentiment", 0.0), -1, 1),
        _norm(market.get("catalyst_present", 0.0), 0, 1),
        # hours_to_event: closer = higher signal. Invert so 0h -> 1.0
        1.0 - _norm(market.get("hours_to_event", 24.0), 0, 24),
    ], dtype=np.float64)


def extract_regime_features(market: Dict[str, Any]) -> np.ndarray:
    return np.array([
        _norm(market.get("vix", 16.0), 10, 40),     # higher vix -> risk-off
        _norm(market.get("breadth", 0.5), 0, 1),    # higher breadth -> risk-on
        _norm(market.get("momentum", 0.0), -1, 1),  # positive momentum -> risk-on
        _norm(market.get("sector_rotation", 0.0), -1, 1),
        _norm(market.get("dispersion", 0.0), 0, 1),
    ], dtype=np.float64)


def extract_drawdown_features(market: Dict[str, Any]) -> np.ndarray:
    return np.array([
        _norm(market.get("dd_pct", 0.0), 0, 25),     # 0% to -25% peak distance
        _norm(market.get("dd_velocity", 0.0), -2, 2),
        _norm(market.get("atr_norm", 1.0), 0, 3),
    ], dtype=np.float64)


def extract_liquidity_features(market: Dict[str, Any]) -> np.ndarray:
    return np.array([
        _norm(market.get("rel_volume", 1.0), 0, 3),     # 1x normal = mid; 3x = thick
        # spread: lower is better. Invert so 0 bps -> 1.0
        1.0 - _norm(market.get("spread_bps", 5.0), 0, 200),
        _norm(market.get("depth_top_5", 1.0), 0, 3),
        _norm(market.get("time_of_day", 0.5), 0, 1),
    ], dtype=np.float64)


def extract_system_health_features(market: Dict[str, Any]) -> np.ndarray:
    return np.array([
        _norm(market.get("broker_uptime", 1.0), 0, 1),
        _norm(market.get("data_lag_ms", 0.0), 0, 2000),
        _norm(market.get("error_rate", 0.0), 0, 1),
        _norm(market.get("pipeline_latency_ms", 100.0), 0, 2000),
    ], dtype=np.float64)


def extract_pacing_features(market: Dict[str, Any]) -> np.ndarray:
    return np.array([
        _norm(market.get("intraday_progress", 0.5), 0, 1),
        _norm(market.get("vol_progress", 0.5), 0, 1),
        _norm(market.get("range_progress", 0.5), 0, 1),
        _norm(market.get("trade_progress", 0.5), 0, 1),
    ], dtype=np.float64)
