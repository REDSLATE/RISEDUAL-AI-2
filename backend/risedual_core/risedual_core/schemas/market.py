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
    # All default to None so pre-Phase-2 snapshots remain valid for inference.
    # The imputation layer (ml/features.py) fills None → False before XGBoost.
    pattern_double_bottom: bool | None = Field(
        default=None,
        description="True if a double-bottom reversal pattern was detected.",
    )
    pattern_bullish_engulfing: bool | None = Field(
        default=None,
        description="True if a bullish engulfing candlestick pattern was detected.",
    )
    pattern_bearish_engulfing: bool | None = Field(
        default=None,
        description="True if a bearish engulfing candlestick pattern was detected.",
    )
    pattern_bull_flag: bool | None = Field(
        default=None,
        description="True if a bull flag continuation pattern was detected.",
    )
    pattern_rsi_divergence: bool | None = Field(
        default=None,
        description="True if bullish RSI divergence was detected (price lower-low, RSI higher-low).",
    )
    pattern_macd_crossover: bool | None = Field(
        default=None,
        description="True if a bullish MACD crossover (line crosses above signal) was detected.",
    )
    pattern_volume_surge: bool | None = Field(
        default=None,
        description="True if current volume exceeds 2x the 20-day average.",
    )
    pattern_head_and_shoulders: bool | None = Field(
        default=None,
        description="True if a head-and-shoulders (bearish reversal) pattern was detected.",
    )

    # ── Outcome labels (backfill + live labeling) ─────────────────────────────
    # Stored directly on the snapshot so training scripts can read a single
    # collection without joining against a separate outcomes collection.
    # None = not yet labeled (live snapshots) or not applicable.
    outcome_1d: str | None = Field(
        default=None,
        description="Forward-1-day outcome: 'up', 'down', or 'flat' (±0.5% band).",
    )
    outcome_5d: str | None = Field(
        default=None,
        description="Forward-5-day outcome: 'up', 'down', or 'flat' (±1.0% band).",
    )
    return_1d: float | None = Field(
        default=None,
        description="Raw forward-1-day log return (e.g. 0.012 = +1.2%).",
    )
    return_5d: float | None = Field(
        default=None,
        description="Raw forward-5-day log return.",
    )
    # Backfill metadata
    source: str | None = Field(
        default=None,
        description="Data source used: 'yfinance', 'finnhub', 'live', etc.",
    )
    schema_version: int = Field(
        default=2,
        description="Schema version. 1=Phase 1, 2=Phase 2+patterns, 3=backfill with outcomes.",
    )


# ── Pattern detection result ───────────────────────────────────────────────


class PatternResult(BaseModel):
    """Result of a single technical pattern detection pass.

    Produced by each detector function in :mod:`risedual_core.ml.patterns`
    and aggregated by :func:`~risedual_core.ml.patterns.detect_all_patterns`.
    ``confidence`` is always populated (0.0 when ``detected=False``) so that
    calling code never needs to guard against ``None``.
    """

    name: str = Field(
        description="Snake-case pattern identifier, e.g. 'double_bottom'."
    )
    detected: bool = Field(
        description="True if the pattern was found in the most recent bars."
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Strength of the pattern in [0.0, 1.0]; 0.0 when not detected.",
    )
    bar_index: int = Field(
        default=-1,
        description="Index of the bar where the pattern completed; -1 when not detected.",
    )
    description: str = Field(
        default="",
        description="Human-readable summary, e.g. 'Double bottom at $182.50, 4.2% separation'.",
    )


# ── Signal output ─────────────────────────────────────────────────────────────


class SignalResult(BaseModel):
    """Output from the signal model for a single ticker.

    ``confidence`` is always Platt-calibrated so it reflects a true
    probability of correctness rather than a raw model score.
    """

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
        description="LLM-generated natural-language explanation (filled by the agent layer).",
    )
