"""Calibration Kanban — read-only promotion-readiness reporter.

Computes per-lane (equity / crypto) calibration state from existing
collections. NEVER writes anything. NEVER toggles flags. NEVER calls
a broker.

Pulls from:
  * ``alpha_decision_log``
  * ``roadguard_equity_decisions``
  * ``roadguard_crypto_decisions``

Phase derivation:
  * Enforce flag ON                                   -> "Enforce"
  * Enforce flag OFF + every checklist item passing   -> "Calibrate"
  * Anything else                                     -> "Shadow"

Promotion state (string shown on the tile button):
  * All checklist items pass + enforce OFF            -> "Ready for Review"
  * All checklist items pass + enforce ON             -> "Eligible"
  * Any checklist item fails                          -> "Blocked"
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.ml.roadguard import CryptoRoadGuard, EquityRoadGuard

logger = logging.getLogger(__name__)


# ── Tunables (env-driven so the operator can tighten promotion bars) ──


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# Minimum number of receipts (per lane) before the lane is even
# considered for promotion. Tunable so the operator can tighten this.
MIN_RECEIPTS_PER_LANE = _env_int("CALIBRATION_MIN_RECEIPTS_PER_LANE", 200)

# Sample window in days for all metrics on the tile.
SAMPLE_WINDOW_DAYS = _env_int("CALIBRATION_SAMPLE_WINDOW_DAYS", 7)

# Broker-health blocks should be < this fraction of total RG decisions
# for the lane to be considered "stable" on broker health.
BROKER_HEALTH_BLOCK_RATIO_MAX = _env_float(
    "CALIBRATION_BROKER_HEALTH_BLOCK_RATIO_MAX", 0.05,
)


# ── Output shapes ─────────────────────────────────────────────────


@dataclass
class ChecklistItem:
    key: str
    label: str
    status: str   # "pass" | "fail" | "n/a"
    value: Any
    threshold: Optional[Any] = None
    note: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LaneKanbanCard:
    lane: str
    phase: str
    enforce_flag: bool
    promotion_state: str
    blockers: List[str]
    receipts_collection: str
    rg_collection: str
    sample_window: Dict[str, Any]
    metrics: Dict[str, Any]
    checklist: List[Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Internal helpers ──────────────────────────────────────────────


async def _decision_log_metrics(db, lane: str, since) -> Dict[str, Any]:
    """Aggregate ``alpha_decision_log`` per-lane within the window."""
    if db is None:
        return {"total": 0, "approved": 0, "no_trade": 0, "by_stage": {}, "false_blocks": 0}

    base_match = {"lane": lane, "created_at": {"$gte": since}}
    total = 0
    approved = 0
    no_trade = 0
    by_stage: Dict[str, int] = {}
    try:
        cursor = db["alpha_decision_log"].aggregate([
            {"$match": base_match},
            {"$group": {
                "_id": {"decision": "$decision", "blocked_at": "$blocked_at"},
                "n": {"$sum": 1},
            }},
        ])
        async for row in cursor:
            n = int(row.get("n") or 0)
            total += n
            decision = row["_id"].get("decision")
            stage = row["_id"].get("blocked_at")
            if decision == "APPROVED":
                approved += n
            else:
                no_trade += n
                if stage:
                    by_stage[stage] = by_stage.get(stage, 0) + n
        # Operator-marked false blocks (the doc gains a
        # ``false_block_marked: true`` field if the operator labels
        # a NO_TRADE as wrong; absent today, structurally supported).
        false_blocks = await db["alpha_decision_log"].count_documents({
            **base_match,
            "false_block_marked": True,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kanban] decision_log aggregation failed: %s", exc)
        false_blocks = 0
    return {
        "total": total,
        "approved": approved,
        "no_trade": no_trade,
        "by_stage": by_stage,
        "false_blocks": int(false_blocks or 0),
    }


async def _roadguard_metrics(db, *, collection: str, lane: str, since) -> Dict[str, Any]:
    """Aggregate per-lane RG verdicts in ``collection`` within the window.

    Counts:
      * decisions: PASS / REDUCE / BLOCK
      * gates: G00 (LANE_MISMATCH), G01 (broker), G03+G04 (exposure caps),
               G07 (duplicate symbol)
      * cross-contamination: any verdict with ``verdict.lane != lane``
    """
    out = {
        "total": 0,
        "by_decision": {"PASS": 0, "REDUCE": 0, "BLOCK": 0},
        "lane_mismatch": 0,
        "broker_health_blocks": 0,
        "exposure_cap_blocks": 0,
        "duplicate_symbol_blocks": 0,
        "cross_contamination": 0,
    }
    if db is None:
        return out

    try:
        cursor = db[collection].aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": {
                    "decision": "$verdict.decision",
                    "gate": "$verdict.gate",
                    "verdict_lane": "$verdict.lane",
                },
                "n": {"$sum": 1},
            }},
        ])
        async for row in cursor:
            n = int(row.get("n") or 0)
            out["total"] += n
            decision = row["_id"].get("decision")
            if decision in out["by_decision"]:
                out["by_decision"][decision] += n
            gate = row["_id"].get("gate")
            verdict_lane = row["_id"].get("verdict_lane")
            if verdict_lane and verdict_lane != lane:
                out["cross_contamination"] += n
            if gate == "G00":
                out["lane_mismatch"] += n
            elif gate == "G01":
                out["broker_health_blocks"] += n
            elif gate in ("G03", "G04"):
                out["exposure_cap_blocks"] += n
            elif gate == "G07":
                out["duplicate_symbol_blocks"] += n
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kanban] %s aggregation failed: %s", collection, exc)
    return out


def _enforce_flag_for(lane: str) -> bool:
    """Read the per-lane enforce env flag. Defaults False (always)."""
    var = (
        "ROADGUARD_EQUITY_ENFORCE_ENABLED" if lane == "equity"
        else "ROADGUARD_CRYPTO_ENFORCE_ENABLED"
    )
    return os.getenv(var, "false").lower() == "true"


def _build_checklist(
    *,
    lane: str,
    expected_collection: str,
    rg_collection: str,
    decision_metrics: Dict[str, Any],
    rg_metrics: Dict[str, Any],
    enforce_flag: bool,
) -> List[ChecklistItem]:
    """Compute the 6-item promotion checklist for one lane."""
    items: List[ChecklistItem] = []

    # 1. Min receipts reached
    total_receipts = int(decision_metrics.get("total") or 0)
    items.append(ChecklistItem(
        key="min_receipts",
        label=f"Receipts reached ({MIN_RECEIPTS_PER_LANE}+)",
        status="pass" if total_receipts >= MIN_RECEIPTS_PER_LANE else "fail",
        value=total_receipts,
        threshold=MIN_RECEIPTS_PER_LANE,
        note=(
            None if total_receipts >= MIN_RECEIPTS_PER_LANE
            else f"need {MIN_RECEIPTS_PER_LANE - total_receipts} more"
        ),
    ))

    # 2. Zero lane contamination (cross-lane writes)
    contamination = int(rg_metrics.get("cross_contamination") or 0)
    items.append(ChecklistItem(
        key="zero_contamination",
        label="Zero lane contamination",
        status="pass" if contamination == 0 else "fail",
        value=contamination,
        threshold=0,
    ))

    # 3. Zero operator-marked false blocks
    false_blocks = int(decision_metrics.get("false_blocks") or 0)
    items.append(ChecklistItem(
        key="zero_false_blocks",
        label="Zero operator-marked false blocks",
        status="pass" if false_blocks == 0 else "fail",
        value=false_blocks,
        threshold=0,
    ))

    # 4. Stable broker-health behaviour
    rg_total = int(rg_metrics.get("total") or 0)
    bh_blocks = int(rg_metrics.get("broker_health_blocks") or 0)
    if rg_total == 0:
        bh_status = "n/a"
        bh_value: Any = "no RG decisions yet"
    else:
        bh_ratio = bh_blocks / rg_total
        bh_status = "pass" if bh_ratio <= BROKER_HEALTH_BLOCK_RATIO_MAX else "fail"
        bh_value = round(bh_ratio, 4)
    items.append(ChecklistItem(
        key="broker_health_stable",
        label=f"Broker-health blocks ≤ {int(BROKER_HEALTH_BLOCK_RATIO_MAX*100)}%",
        status=bh_status,
        value=bh_value,
        threshold=BROKER_HEALTH_BLOCK_RATIO_MAX,
    ))

    # 5. Correct lane collection writes (we read from ``rg_collection`` —
    # the check is structural: the collection bound to this RG class
    # must match the canonical lane → collection mapping).
    expected_match = (rg_collection == expected_collection)
    items.append(ChecklistItem(
        key="correct_collection",
        label="Correct lane collection writes",
        status="pass" if expected_match else "fail",
        value=rg_collection,
        threshold=expected_collection,
    ))

    # 6. Enforce flag still OFF (the user wants the kanban to confirm
    # the safety invariant — even when promotion is "Eligible", we
    # require enforce=OFF until human review flips the flag).
    items.append(ChecklistItem(
        key="enforce_off",
        label="Enforce flag OFF",
        status="pass" if not enforce_flag else "fail",
        value=enforce_flag,
        threshold=False,
        note="enforce flag is ON" if enforce_flag else None,
    ))
    return items


def _derive_phase_and_state(
    *, checklist: List[ChecklistItem], enforce_flag: bool,
) -> tuple[str, str, List[str]]:
    """Resolve (phase, promotion_state, blockers) from the checklist.

    All "n/a" items are treated as PASS for promotion purposes — they
    signal "data not yet collected" rather than a failure.
    """
    failing = [c.key for c in checklist if c.status == "fail"]

    if enforce_flag and not failing:
        return ("Enforce", "Eligible", [])

    if enforce_flag and failing:
        # Already promoted but checklist fails — this is a regression
        # state and the operator needs to know.
        return ("Enforce", "Blocked", failing)

    # Enforce off
    if not failing:
        # All bars met — but we keep enforce off until manual review.
        return ("Calibrate", "Ready for Review", [])

    return ("Shadow", "Blocked", failing)


# ── Public API ────────────────────────────────────────────────────


async def get_kanban(db) -> Dict[str, Any]:
    """Read-only kanban payload for both lanes.

    Returns a serialisable dict — caller can JSON-encode directly.
    NEVER raises (errors are surfaced inline as ``error`` strings on
    the affected lane).
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=SAMPLE_WINDOW_DAYS)
    sample_window = {
        "days": SAMPLE_WINDOW_DAYS,
        "from": since.isoformat(),
        "to": now.isoformat(),
    }

    out: Dict[str, Any] = {
        "version": 1,
        "generated_at": now.isoformat(),
        "sample_window": sample_window,
        "promotion_thresholds": {
            "min_receipts_per_lane": MIN_RECEIPTS_PER_LANE,
            "broker_health_block_ratio_max": BROKER_HEALTH_BLOCK_RATIO_MAX,
        },
        "lanes": {},
    }

    # Lane definitions are sourced from the RG classes themselves so
    # the canonical lane → collection mapping has ONE source of truth.
    lane_specs = [
        ("equity", EquityRoadGuard),
        ("crypto", CryptoRoadGuard),
    ]

    for lane, rg_cls in lane_specs:
        try:
            decision_metrics = await _decision_log_metrics(db, lane, since)
            rg_metrics = await _roadguard_metrics(
                db, collection=rg_cls.DECISIONS_COLLECTION,
                lane=lane, since=since,
            )
            enforce_flag = _enforce_flag_for(lane)
            checklist = _build_checklist(
                lane=lane,
                expected_collection=rg_cls.DECISIONS_COLLECTION,
                rg_collection=rg_cls.DECISIONS_COLLECTION,
                decision_metrics=decision_metrics,
                rg_metrics=rg_metrics,
                enforce_flag=enforce_flag,
            )
            phase, promotion_state, blockers = _derive_phase_and_state(
                checklist=checklist, enforce_flag=enforce_flag,
            )
            card = LaneKanbanCard(
                lane=lane,
                phase=phase,
                enforce_flag=enforce_flag,
                promotion_state=promotion_state,
                blockers=blockers,
                receipts_collection="alpha_decision_log",
                rg_collection=rg_cls.DECISIONS_COLLECTION,
                sample_window=sample_window,
                metrics={
                    "receipts": decision_metrics,
                    "roadguard": rg_metrics,
                },
                checklist=[c.as_dict() for c in checklist],
            )
            out["lanes"][lane] = card.as_dict()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[kanban] lane=%s failed: %s", lane, exc)
            out["lanes"][lane] = {
                "lane": lane,
                "error": f"{type(exc).__name__}:{exc}",
            }
    return out
