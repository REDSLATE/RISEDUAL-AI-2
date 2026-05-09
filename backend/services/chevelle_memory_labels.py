"""Enums + constants for the Chevelle memory labeler.

Pure data — no logic, no imports beyond ``enum``. Both
``services.chevelle_memory_labeler`` (the public API) and
``services._chevelle_resolvers`` (the pure helpers) import from here
to break a circular import that would otherwise form.
"""
from __future__ import annotations

from enum import Enum


class EventEra(str, Enum):
    """The 9 operator-mandated era buckets.

    These are stable identifiers — never rename without a migration
    path. The mapping from ``event_aware_regime_labeler`` output to
    these tokens is intentionally lossy (multiple AV-OVERVIEW events
    can collapse into a single era).
    """
    GFC_2008 = "2008_FINANCIAL_CRISIS"
    FLASH_CRASH_2010 = "2010_FLASH_CRASH"
    CHINA_DEVAL_2015 = "2015_CHINA_DEVALUATION"
    VOL_SPIKE_2018 = "2018_VOL_SPIKE"
    COVID_2020 = "2020_COVID_CRASH"
    RATE_HIKE_2022 = "2022_RATE_HIKE_CYCLE"
    AI_BUBBLE_2024_2025 = "2024_2025_AI_BUBBLE"
    CURRENT_REGIME = "CURRENT_REGIME"
    UNKNOWN_ERA = "UNKNOWN_ERA"


class OutcomeLabel(str, Enum):
    WIN = "win"
    LOSS = "loss"
    NEUTRAL = "neutral"
    UNRESOLVED = "unresolved"


class FailureMode(str, Enum):
    """Coarse-grained failure tags — finer-grained classification
    is still produced by ``services.failure_mode_classifier`` for
    the live decision stack."""
    NONE = "none"
    BLOWUP = "blowup"
    REGIME_MISMATCH = "regime_mismatch"
    TOXIC_HIGH_CONFIDENCE = "toxic_high_confidence"
    DATA_INTEGRITY = "data_integrity"
    UNKNOWN = "unknown"


class DataQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    REJECTED = "rejected"


# ── Trust ladder (operator-mandated values) ─────────────────────────


# These constants are the SOURCE OF TRUTH for the trust-weight
# ladder. Any change here must be paired with the corresponding test
# in ``tests/test_chevelle_memory_labeler.py``.
TRUST_LIVE_REAL_FILL: float = 1.00
TRUST_RECENT_PAPER_TRADE: float = 0.50
TRUST_HISTORICAL_PAPER_TRADE: float = 0.25
TRUST_CURRENT_MACRO_PROXY: float = 0.15
TRUST_TOXIC_MEMORY: float = 0.10
TRUST_SYNTHETIC_BACKTEST: float = 0.05
TRUST_QUARANTINED: float = 0.00

# Recency cutoff that splits ``recent_paper_trade`` from
# ``historical_paper_trade``. 30 days is the operator default —
# matches the v1/v2 retrain default window.
RECENT_PAPER_DAYS: int = 30

# Public-launch cutover — anything before this is synthetic /
# backtest. Mirrors ``services.data_source_labeler._DEFAULT_FLOOR``.
PUBLIC_LAUNCH_FLOOR_ISO: str = "2026-04-23"
