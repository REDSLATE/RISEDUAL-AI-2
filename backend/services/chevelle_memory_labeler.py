"""Chevelle Memory Labeler — read-side firewall before any memory
flows into the Chevelle training/observation engine.

────────────────────────────────────────────────────────────────────
AUTHORITY-BOUNDARY CONTRACT
────────────────────────────────────────────────────────────────────

This module is a PURE LABELER. It:

  * Reads candidate memories (Mongo dicts / dataclasses / generic
    mappings) and returns a structured ``ChevelleMemoryRecord``
    with every label Chevelle needs.
  * NEVER writes to any collection.
  * NEVER imports broker / executor / paper-trader / Strategist /
    Auditor / Commander / RoadGuard / FastVeto / kill-switch.
  * NEVER emits a BUY / SELL / NO_TRADE verdict.
  * NEVER deletes or mutates the source row.

The 10 operator-decreed hard rules
----------------------------------
  1. Every memory MUST have ``source``.
  2. Every memory MUST have ``opened_at`` / ``closed_at`` or be
     rejected.
  3. Every memory MUST have ``lane``.
  4. Every memory MUST have a normalized symbol.
  5. Old trades MUST get ``event_era``.
  6. Bad / ambiguous rows MUST be ``QUARANTINED``, not learned from.
  7. Toxic / failure labels MUST reduce trust, not delete the memory.
  8. Chevelle may observe quarantined memories but cannot train on
     them.
  9. NO direct BUY / SELL decisions from labels.
 10. NO broker / executor imports.

Module layout (authority-boundary split — 2026-05-09)
-----------------------------------------------------
  * ``chevelle_memory_labels.py``   — enums + trust-ladder constants
  * ``_chevelle_resolvers.py``      — pure label-resolution helpers
  * ``chevelle_memory_labeler.py``  — dataclass + public API (this)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Iterator, List, Optional

from services.chevelle_memory_labels import (
    PUBLIC_LAUNCH_FLOOR_ISO,
    RECENT_PAPER_DAYS,
    TRUST_CURRENT_MACRO_PROXY,
    TRUST_HISTORICAL_PAPER_TRADE,
    TRUST_LIVE_REAL_FILL,
    TRUST_QUARANTINED,
    TRUST_RECENT_PAPER_TRADE,
    TRUST_SYNTHETIC_BACKTEST,
    TRUST_TOXIC_MEMORY,
    DataQuality,
    EventEra,
    FailureMode,
    OutcomeLabel,
)
from services._chevelle_resolvers import (
    coerce_dt,
    compute_data_quality,
    compute_trust_weight,
    normalize_symbol,
    resolve_event_era,
    resolve_failure_mode,
    resolve_lane,
    resolve_outcome_label,
    resolve_source,
)

# Re-export trust-ladder constants + enums so existing call sites
# (and tests) can keep importing them from this module.
__all__ = [
    "ChevelleMemoryRecord",
    "DataQuality",
    "EventEra",
    "FailureMode",
    "OutcomeLabel",
    "PUBLIC_LAUNCH_FLOOR_ISO",
    "RECENT_PAPER_DAYS",
    "TRUST_CURRENT_MACRO_PROXY",
    "TRUST_HISTORICAL_PAPER_TRADE",
    "TRUST_LIVE_REAL_FILL",
    "TRUST_QUARANTINED",
    "TRUST_RECENT_PAPER_TRADE",
    "TRUST_SYNTHETIC_BACKTEST",
    "TRUST_TOXIC_MEMORY",
    "label_memories",
    "label_memory",
    "quarantined_only",
    "trainable_only",
]

logger = logging.getLogger(__name__)


# ── Data class ──────────────────────────────────────────────────────


@dataclass
class ChevelleMemoryRecord:
    """Labeled memory record consumed by the future Chevelle training
    layer. All eight required labels are present on EVERY record —
    rejected rows fill them with safe defaults so downstream consumers
    never need a try/except.
    """
    # Identity / normalization
    raw_id: Optional[str]
    symbol: str
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]

    # Required labels (operator-mandated)
    event_era: EventEra
    lane: str
    source: str
    outcome_label: OutcomeLabel
    failure_mode: FailureMode
    data_quality: DataQuality
    trust_weight: float
    trainable: bool

    # Observation gate (always True for non-malformed rows; set False
    # only when the row is so broken Chevelle's observation loop
    # itself can't reason over it).
    chevelle_can_observe: bool = True

    # Reasons (audit trail)
    reasons: List[str] = field(default_factory=list)
    rejection_reason: Optional[str] = None


# ── Main entry points ───────────────────────────────────────────────


def label_memory(raw_row: Any) -> ChevelleMemoryRecord:
    """Label a single candidate memory.

    Never raises — malformed input returns a quarantined record with
    a populated ``rejection_reason``.

    Required-field rejection (per rules 1-4):
      * source missing
      * opened_at AND closed_at both missing
      * symbol unresolvable
    """
    reasons: List[str] = []
    rejection: Optional[str] = None

    if not isinstance(raw_row, dict):
        return _quarantine_record(
            raw_id=None, rejection="non_dict_input",
        )

    # Required: source.
    source = resolve_source(raw_row)
    if not source:
        reasons.append("missing_source")
        rejection = "missing_source"

    # Required: at least one of opened_at / closed_at.
    opened_at = coerce_dt(
        raw_row.get("opened_at")
        or raw_row.get("predicted_at")
        or raw_row.get("created_at")
        or raw_row.get("timestamp")
    )
    closed_at = coerce_dt(raw_row.get("closed_at"))
    if opened_at is None and closed_at is None:
        reasons.append("missing_both_open_and_close_timestamps")
        rejection = rejection or "missing_timestamps"

    # Lane (rule 3) — "unknown" lane memories are still observable.
    lane = resolve_lane(raw_row)
    if lane == "unknown":
        reasons.append("lane_unresolvable")

    # Required: normalized symbol.
    sym = normalize_symbol(
        raw_row.get("symbol")
        or raw_row.get("ticker")
        or raw_row.get("pair")
    )
    if not sym:
        reasons.append("missing_symbol")
        rejection = rejection or "missing_symbol"

    has_required = (
        bool(source)
        and (opened_at is not None or closed_at is not None)
        and bool(sym)
    )
    quarantined = not has_required

    # Compose every label even on rejection — downstream consumers
    # see consistent shape.
    outcome = (
        resolve_outcome_label(raw_row)
        if has_required else OutcomeLabel.UNRESOLVED
    )
    failure = resolve_failure_mode(raw_row, outcome)
    era = (
        resolve_event_era(opened_at)
        if has_required else EventEra.UNKNOWN_ERA
    )
    quality = compute_data_quality(raw_row, has_required=has_required)
    trust = compute_trust_weight(
        source=source,
        opened_at=opened_at,
        failure_mode=failure,
        quarantined=quarantined,
        lane=lane,
    )
    trainable = (
        not quarantined
        and quality is not DataQuality.REJECTED
        and trust > 0.0
    )

    if quarantined:
        reasons.append("quarantined_missing_required_fields")

    raw_id = (
        raw_row.get("trade_id")
        or raw_row.get("prediction_id")
        or (
            str(raw_row.get("_id"))
            if raw_row.get("_id") is not None else None
        )
    )

    return ChevelleMemoryRecord(
        raw_id=raw_id,
        symbol=sym,
        opened_at=opened_at,
        closed_at=closed_at,
        event_era=era,
        lane=lane,
        source=source or "unknown",
        outcome_label=outcome,
        failure_mode=failure,
        data_quality=quality,
        trust_weight=trust,
        trainable=trainable,
        chevelle_can_observe=True,
        reasons=reasons,
        rejection_reason=rejection,
    )


def label_memories(rows: Iterable[Any]) -> Iterator[ChevelleMemoryRecord]:
    """Bulk-label an iterable of candidate rows.

    Pure generator — no I/O, no aggregation. Callers that need to
    materialise the results into a list should call ``list(...)``.
    """
    for r in rows:
        yield label_memory(r)


def trainable_only(
    records: Iterable[ChevelleMemoryRecord],
) -> Iterator[ChevelleMemoryRecord]:
    """Filter generator: only records Chevelle MAY train on."""
    for rec in records:
        if rec.trainable and rec.trust_weight > 0.0:
            yield rec


def quarantined_only(
    records: Iterable[ChevelleMemoryRecord],
) -> Iterator[ChevelleMemoryRecord]:
    """Filter generator: ONLY quarantined memories. Useful for the
    operator-facing dashboard that surfaces rows needing review."""
    for rec in records:
        if rec.rejection_reason is not None or rec.trust_weight == 0.0:
            yield rec


# ── Internal helpers ────────────────────────────────────────────────


def _quarantine_record(
    *, raw_id: Optional[str], rejection: str,
) -> ChevelleMemoryRecord:
    return ChevelleMemoryRecord(
        raw_id=raw_id,
        symbol="",
        opened_at=None,
        closed_at=None,
        event_era=EventEra.UNKNOWN_ERA,
        lane="unknown",
        source="unknown",
        outcome_label=OutcomeLabel.UNRESOLVED,
        failure_mode=FailureMode.UNKNOWN,
        data_quality=DataQuality.REJECTED,
        trust_weight=TRUST_QUARANTINED,
        trainable=False,
        chevelle_can_observe=True,
        reasons=[rejection],
        rejection_reason=rejection,
    )
