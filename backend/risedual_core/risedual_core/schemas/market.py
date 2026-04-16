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
