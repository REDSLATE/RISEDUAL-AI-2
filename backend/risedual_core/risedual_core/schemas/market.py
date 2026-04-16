"""Shared domain models for risedual_core.

These are the canonical Pydantic v2 schemas for predictions, features, and
market signals. Both the CLI surface and the FastAPI backend import from here
so there is a single source of truth for every shared type.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────────────


class PredictionDirection(str, Enum):
    """The directional prediction produced by the signal model."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class OutcomeLabel(str, Enum):
    """The realised outcome label applied during prediction tracking."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    PENDING = "pending"
    ERROR = "error"


# ── Feature schema ────────────────────────────────────────────────────────────


class FeaturesSnapshot(BaseModel):
    """Point-in-time feature vector captured at prediction time.

    All numeric fields are optional so the snapshot can be persisted
    before all data sources have returned, with missing values imputed
    downstream by the feature-engineering layer.
    """

    ticker: str = Field(description="Ticker symbol (e.g. 'AAPL').")
    timestamp: datetime = Field(description="UTC datetime when the snapshot was taken.")

    # ── Technical indicators ──────────────────────────────────────────────────
    rsi_14: float | None = Field(default=None, description="14-period Relative Strength Index.")
    macd: float | None = Field(default=None, description="MACD line value.")
    macd_signal: float | None = Field(default=None, description="MACD signal line value.")
    sma_20: float | None = Field(default=None, description="20-period Simple Moving Average.")
    sma_50: float | None = Field(default=None, description="50-period Simple Moving Average.")

    # ── Volume & alternative data ─────────────────────────────────────────────
    volume_ratio: float | None = Field(
        default=None,
        description="Current volume divided by 20-day average volume.",
    )
    sentiment_score: float | None = Field(
        default=None,
        description="Aggregated news/social sentiment in [-1.0, 1.0].",
    )
    insider_activity: float | None = Field(
        default=None,
        description="Normalised insider-buying signal (positive = net buying).",
    )
    sector_momentum: float | None = Field(
        default=None,
        description="20-day return of the ticker's GICS sector ETF.",
    )

    # ── Regime & price ────────────────────────────────────────────────────────
    regime_label: str | None = Field(
        default=None,
        description="Current market regime: 'bull', 'bear', or 'sideways'.",
    )
    price: float | None = Field(
        default=None,
        description="Closing or mid price at snapshot time.",
    )

    # ── Phase 2 — pattern detection booleans ─────────────────────────────────
    pattern_double_bottom: bool | None = Field(default=None)
    pattern_bullish_engulfing: bool | None = Field(default=None)
    pattern_bearish_engulfing: bool | None = Field(default=None)
    pattern_bull_flag: bool | None = Field(default=None)
    pattern_rsi_divergence: bool | None = Field(default=None)
    pattern_macd_crossover: bool | None = Field(default=None)
    pattern_volume_surge: bool | None = Field(default=None)
    pattern_head_and_shoulders: bool | None = Field(default=None)

    # ── Convenience aliases for v6 services ────────────────────────────────────
    @property
    def rsi(self) -> float | None:
        return self.rsi_14

    @property
    def atr(self) -> float | None:
        return None

    @property
    def close_price(self) -> float | None:
        return self.price


# ── Pattern detection result ───────────────────────────────────────────────


class PatternResult(BaseModel):
    """Result of a single technical pattern detection pass."""

    name: str = Field(description="Snake-case pattern identifier.")
    detected: bool = Field(description="True if the pattern was found.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bar_index: int = Field(default=-1)
    description: str = Field(default="")


# ── Signal output ─────────────────────────────────────────────────────────────


class SignalResult(BaseModel):
    """Output from the signal model for a single ticker."""

    ticker: str = Field(description="Ticker symbol.")
    direction: PredictionDirection = Field(description="Predicted price direction.")
    confidence: float = Field(
        description="Platt-calibrated probability of correctness in [0.0, 1.0].",
        ge=0.0,
        le=1.0,
    )
    raw_probability: float = Field(
        description="Uncalibrated model output probability.",
        ge=0.0,
        le=1.0,
    )
    regime: str | None = Field(
        default=None,
        description="Market regime at signal time ('bull', 'bear', 'sideways').",
    )
    feature_importance: dict[str, float] = Field(
        default_factory=dict,
        description="Top contributing features and their importance scores.",
    )
    model_version: str = Field(
        default="0.1.0",
        description="Version string of the model that produced this result.",
    )
    timestamp: datetime = Field(description="UTC datetime when the signal was generated.")
    explanation: str | None = Field(
        default=None,
        description="LLM-generated natural-language explanation.",
    )
    prediction_id: str | None = Field(
        default=None,
        description="UUID for this prediction cycle.",
    )
    patterns_detected: list[str] = Field(
        default_factory=list,
        description="Names of patterns detected on this signal.",
    )


# ── Backward-compat: CalibrationStats (canonical version in signal_model.py) ─


class CalibrationStats(BaseModel):
    """Calibration metrics stored after SignalModel.fit()."""

    accuracy: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    ece: float = Field(ge=0.0, le=1.0)
    n_predictions: int = Field(ge=0)
    positive_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    mean_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model_version: str = Field(default="0.1.0")
    evaluated_at: datetime | None = Field(default=None)


# ── Backward-compat: Backtest schemas ─────────────────────────────────────────


class RegimeMetrics(BaseModel):
    """Win rate breakdown for a single regime."""

    regime: str
    win_rate: float
    n_trades: int


class BacktestResult(BaseModel):
    """Output of walk-forward backtest on labeled feature snapshots."""

    sharpe_ratio: float
    max_drawdown: float = Field(ge=0.0, le=1.0)
    win_rate_overall: float = Field(ge=0.0, le=1.0)
    win_rate_by_regime: list[RegimeMetrics] = Field(default_factory=list)
    n_trades: int
    train_size: int
    test_size: int
    model_version: str
    computed_at: str
