"""Market Regime — Gaussian HMM over daily market features.

Design principle
----------------
The regime layer must be **non-blocking**. If the model is missing,
undertrained, or uncertain, callers get ``regime="UNKNOWN"`` and
Alpha continues to trade under the normal rules — regime is a
confidence *modifier* through the Edge Engine, never a hard gate.

Features (per daily bar, over ~180 days of SPY)
-----------------------------------------------
1. log-return
2. rolling 5-day realized volatility
3. volume z-score (bar volume vs 20-day mean, in std units)
4. body-to-range ratio ((close - open) / (high - low)) — trendiness
5. gap size ((open - prev_close) / prev_close)

Feeding a small set of well-behaved features into a 4-state Gaussian
HMM lets the model discover statistical regimes (rather than us
hard-coding "momentum / choppy / risk-off"). Labels are assigned
post-hoc based on each state's mean feature vector.

Storage
-------
* Feature vectors + regime observations → SQLite hot store (never Mongo).
* Fitted model (pickled bytes) → SQLite ``regime_models`` table
  keyed by ``model_id`` so restarts don't refit.
* Compact "current regime" doc → ``alpha_regime_state`` in Mongo
  (one row, ``_id="current"``) so the admin UI can read a snapshot
  without touching SQLite.
"""
from __future__ import annotations

import logging
import os
import pickle
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_ID = "spy_daily_v1"
_MARKET_PROXY = "SPY"
_N_STATES = 4
_LOOKBACK_DAYS = 180
_MIN_TRAIN_DAYS = 90
_HOT_STORE_TABLE_INIT = False


# ─── SQLite tables (extend the existing alpha_hot_store DB) ──────


def _hot_store_path() -> str:
    from services import alpha_hot_store
    alpha_hot_store.init()
    return alpha_hot_store._path()  # type: ignore[attr-defined]


def _ensure_tables() -> None:
    global _HOT_STORE_TABLE_INIT
    if _HOT_STORE_TABLE_INIT:
        return
    with sqlite3.connect(_hot_store_path(), timeout=5.0) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS regime_features (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_date    TEXT NOT NULL,       -- YYYY-MM-DD
                features    BLOB NOT NULL,       -- pickled np.array
                ts_ns       INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_regime_feat_date ON regime_features(bar_date);

            CREATE TABLE IF NOT EXISTS regime_models (
                model_id    TEXT PRIMARY KEY,
                model_blob  BLOB NOT NULL,
                trained_at  INTEGER NOT NULL,
                n_samples   INTEGER NOT NULL,
                state_meta  TEXT
            );

            CREATE TABLE IF NOT EXISTS regime_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_date    TEXT NOT NULL,
                regime_id   INTEGER NOT NULL,
                label       TEXT NOT NULL,
                probability REAL NOT NULL,
                ts_ns       INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_regime_evt_date ON regime_events(bar_date);
            """
        )
    _HOT_STORE_TABLE_INIT = True


# ─── feature engineering ─────────────────────────────────────────


@dataclass
class RegimeSnapshot:
    label: str                    # "UNKNOWN" | "trend_up" | "momentum_expansion" | ...
    regime_id: int                # -1 when unknown
    probability: float            # posterior for the winning state
    posteriors: dict              # {label: prob}
    trained_samples: int
    computed_at: datetime
    model_id: str = _MODEL_ID

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "regime_id": self.regime_id,
            "probability": round(self.probability, 4),
            "posteriors": {k: round(float(v), 4) for k, v in self.posteriors.items()
                            if isinstance(v, (int, float))},
            "trained_samples": self.trained_samples,
            "model_id": self.model_id,
            "computed_at": self.computed_at.isoformat(),
        }


def _unknown_snapshot(reason: str) -> RegimeSnapshot:
    snap = RegimeSnapshot(
        label="UNKNOWN",
        regime_id=-1,
        probability=0.0,
        posteriors={"UNKNOWN": 1.0},
        trained_samples=0,
        computed_at=datetime.now(timezone.utc),
    )
    # Reason is metadata for logs, kept off the numeric posteriors map.
    snap.__dict__["_reason"] = reason
    return snap


async def _fetch_market_bars() -> Optional[list[dict]]:
    try:
        from services.market_data_pool import market_daily
    except Exception:  # noqa: BLE001
        return None
    try:
        bars = await market_daily(_MARKET_PROXY, outputsize="full")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(bars, list) or len(bars) < _MIN_TRAIN_DAYS:
        return None
    return bars[-_LOOKBACK_DAYS:]


def _features_from_bars(bars: list[dict]) -> tuple[np.ndarray, list[str]]:
    """Return (features_matrix, dates). Drops the first 20 bars (warm-up)."""
    rows: list[list[float]] = []
    dates: list[str] = []
    closes = np.array([float(b.get("close") or 0.0) for b in bars], dtype=float)
    volumes = np.array([float(b.get("volume") or 0.0) for b in bars], dtype=float)
    opens = np.array([float(b.get("open") or 0.0) for b in bars], dtype=float)
    highs = np.array([float(b.get("high") or 0.0) for b in bars], dtype=float)
    lows = np.array([float(b.get("low") or 0.0) for b in bars], dtype=float)
    log_ret = np.zeros_like(closes)
    log_ret[1:] = np.diff(np.log(np.where(closes > 0, closes, 1.0)))
    for i in range(20, len(bars)):
        if closes[i] <= 0 or opens[i] <= 0:
            continue
        window = log_ret[i - 5: i]
        vol5 = float(np.std(window)) if len(window) > 1 else 0.0
        vol_mean_20 = float(np.mean(volumes[i - 20: i]))
        vol_std_20 = float(np.std(volumes[i - 20: i])) or 1.0
        vol_z = (float(volumes[i]) - vol_mean_20) / vol_std_20
        rng = float(highs[i] - lows[i])
        body_ratio = float(closes[i] - opens[i]) / rng if rng > 0 else 0.0
        prev_close = float(closes[i - 1]) if closes[i - 1] > 0 else float(opens[i])
        gap = (float(opens[i]) - prev_close) / prev_close if prev_close > 0 else 0.0
        rows.append([float(log_ret[i]), vol5, vol_z, body_ratio, gap])
        d = bars[i].get("date") or bars[i].get("timestamp") or str(i)
        dates.append(str(d))
    return np.array(rows, dtype=float), dates


def _label_states(model, feature_matrix: np.ndarray) -> dict[int, str]:
    """Assign human-readable labels post-hoc from each state's mean vector.

    Labels are heuristic: the model discovers the statistical states,
    we just pick a readable name from each state's centroid.
    """
    means = model.means_  # (n_states, n_features) — log_ret, vol5, vol_z, body, gap
    labels: dict[int, str] = {}
    for i, m in enumerate(means):
        log_ret, vol5, vol_z, body, gap = m
        if vol5 > np.median(means[:, 1]) and vol_z > 0:
            labels[i] = "momentum_expansion"
        elif log_ret > 0 and body > 0.2:
            labels[i] = "trend_up"
        elif log_ret < 0 and vol5 > np.median(means[:, 1]):
            labels[i] = "risk_off"
        else:
            labels[i] = "choppy_meanrevert"
    # Deduplicate labels (add suffix if two states collide)
    seen: dict[str, int] = {}
    for i, lab in list(labels.items()):
        seen[lab] = seen.get(lab, 0) + 1
        if seen[lab] > 1:
            labels[i] = f"{lab}_{seen[lab]}"
    return labels


# ─── fit / score ─────────────────────────────────────────────────


def _fit(feature_matrix: np.ndarray):
    from hmmlearn.hmm import GaussianHMM
    model = GaussianHMM(
        n_components=_N_STATES,
        covariance_type="diag",
        n_iter=100,
        tol=1e-3,
        random_state=42,
    )
    model.fit(feature_matrix)
    return model


def _persist_model(model, n_samples: int, state_labels: dict[int, str]) -> None:
    _ensure_tables()
    blob = pickle.dumps(model)
    with sqlite3.connect(_hot_store_path(), timeout=5.0) as con:
        con.execute(
            "INSERT OR REPLACE INTO regime_models(model_id, model_blob, trained_at, n_samples, state_meta) "
            "VALUES (?, ?, ?, ?, ?)",
            (_MODEL_ID, blob, time.time_ns(), int(n_samples),
             ",".join(f"{k}:{v}" for k, v in state_labels.items())),
        )


def _load_model():
    _ensure_tables()
    with sqlite3.connect(_hot_store_path(), timeout=5.0) as con:
        row = con.execute(
            "SELECT model_blob, n_samples, state_meta FROM regime_models WHERE model_id=?",
            (_MODEL_ID,),
        ).fetchone()
    if not row:
        return None, 0, {}
    try:
        model = pickle.loads(row[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[market_regime] model unpickle failed: %s", exc)
        return None, 0, {}
    labels: dict[int, str] = {}
    for item in (row[2] or "").split(","):
        if ":" in item:
            k, v = item.split(":", 1)
            try:
                labels[int(k)] = v
            except ValueError:
                pass
    return model, int(row[1] or 0), labels


async def refit(db: Any) -> RegimeSnapshot:
    """Train a fresh model on the latest SPY history. Returns the new
    snapshot (current-day regime) computed against the freshly fit model.
    Falls back to UNKNOWN if data or training fails."""
    bars = await _fetch_market_bars()
    if bars is None:
        return _unknown_snapshot("no_market_data")
    features, _ = _features_from_bars(bars)
    if features.shape[0] < _MIN_TRAIN_DAYS:
        return _unknown_snapshot("insufficient_samples")
    try:
        model = _fit(features)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[market_regime] fit failed: %s", exc)
        return _unknown_snapshot(f"fit_failed:{exc.__class__.__name__}")
    labels = _label_states(model, features)
    _persist_model(model, features.shape[0], labels)
    snap = _score_current(model, features, labels)
    await _write_current_state(db, snap)
    return snap


def _score_current(model, features: np.ndarray, labels: dict[int, str]) -> RegimeSnapshot:
    """Compute posterior probabilities for the most-recent observation."""
    try:
        posteriors = model.predict_proba(features)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[market_regime] scoring failed: %s", exc)
        return _unknown_snapshot(f"score_failed:{exc.__class__.__name__}")
    last = posteriors[-1]
    winner = int(np.argmax(last))
    prob = float(last[winner])
    posteriors_dict = {labels.get(i, f"state_{i}"): float(last[i]) for i in range(len(last))}
    return RegimeSnapshot(
        label=labels.get(winner, f"state_{winner}"),
        regime_id=winner,
        probability=prob,
        posteriors=posteriors_dict,
        trained_samples=features.shape[0],
        computed_at=datetime.now(timezone.utc),
    )


async def snapshot(db: Any) -> RegimeSnapshot:
    """Cheap read: load persisted model + latest bars, score today's row.

    If the model is missing, features unavailable, or model is stale
    (more than 24h since compute), returns UNKNOWN so callers can safely
    treat this as "no regime information" and continue trading.
    """
    model, n_samples, labels = _load_model()
    if model is None:
        return _unknown_snapshot("model_not_trained")
    bars = await _fetch_market_bars()
    if bars is None:
        return _unknown_snapshot("no_market_data")
    features, _ = _features_from_bars(bars)
    if features.shape[0] < 1:
        return _unknown_snapshot("no_features")
    snap = _score_current(model, features, labels)
    snap.trained_samples = n_samples
    await _write_current_state(db, snap)
    return snap


async def _write_current_state(db: Any, snap: RegimeSnapshot) -> None:
    if db is None:
        return
    try:
        await db.alpha_regime_state.update_one(
            {"_id": "current"},
            {"$set": {**snap.to_dict(), "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[market_regime] state write failed: %s", exc)


async def get_current(db: Any) -> dict:
    """Read the compact current-regime doc from Mongo without recomputing."""
    if db is None:
        return _unknown_snapshot("no_db").to_dict()
    try:
        doc = await db.alpha_regime_state.find_one({"_id": "current"})
    except Exception:  # noqa: BLE001
        doc = None
    if not doc:
        return _unknown_snapshot("not_computed_yet").to_dict()
    doc.pop("_id", None)
    if isinstance(doc.get("updated_at"), datetime):
        doc["updated_at"] = doc["updated_at"].isoformat()
    return doc


__all__ = ["RegimeSnapshot", "refit", "snapshot", "get_current"]
