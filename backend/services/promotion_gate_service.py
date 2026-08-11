"""Promotion Gate service — MongoDB-backed epoch + config + sourcing layer.

This service is intentionally isolated from the per-intent execution hot
path. It grades **system readiness**, never individual trade decisions.

Collections
-----------
* ``promotion_gate_config`` — single doc (``_id="active"``) holding the current
  operator-editable configuration seeded with the module defaults.
* ``promotion_gate_config_audit`` — one row per config change with
  ``field, old_value, new_value, operator, active_epoch_id, reason, ts``.
* ``promotion_gate_epochs`` — one row per operator-declared epoch. The
  most recent one is treated as active. Lifetime history is preserved.
* ``broker_fills`` — read-only source for real execution fills. Populated by
  broker webhooks/fill receipts (wiring TBD; empty collection is expected
  and keeps the gate on assumed cost per module design).
* ``equity_live_trades`` — read-only source for resolved observations.

No individual-trade veto path lives in this module.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any

from services.promotion_gate import (
    EvaluationObservation,
    ExecutionFill,
    PromotionConfig,
    PromotionGate,
    begin_evaluation_epoch,
)

logger = logging.getLogger(__name__)

_CONFIG_ID = "active"
_DEFAULT_CONFIG_DOC = {
    "_id": _CONFIG_ID,
    "recent_window_observations": 300,
    "min_observations": 100,
    "min_elapsed_hours": 24.0,
    "assumed_round_trip_cost_pct": 0.30,
    "min_measured_fills": 30,
    "min_net_expectancy_pct": 0.0,
    "min_profit_factor": 1.05,
    "max_drawdown_per_100_obs_pct": 10.0,
    "near_expectancy_margin_pct": 0.05,
    "near_profit_factor_margin": 0.10,
    "near_drawdown_multiplier": 1.25,
    "recalibration_min_observations": 200,
    "recalibration_positive_expectancy_pct": 0.0,
    "recalibration_drawdown_multiple": 2.0,
    "recalibration_pf_floor": 1.0,
    "max_risk_violations": 0,
    "max_execution_failures": 0,
    "max_data_integrity_failures": 0,
    "max_invalid_order_behaviors": 0,
}

# Fields the operator can PATCH via the admin UI. Hard-safety count limits
# are intentionally included: an operator may raise them (with an audit
# trail) but the gate itself keeps hard-safety violations as HARD_STOP.
_EDITABLE_FIELDS = frozenset(_DEFAULT_CONFIG_DOC.keys()) - {"_id"}


def _git_sha() -> str:
    """Best-effort short git SHA used as the code-revision stamp on a new epoch."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd="/app",
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace").strip() or "unknown"
    except Exception:  # noqa: BLE001
        return os.environ.get("RISEDUAL_CODE_REVISION", "unknown")


async def ensure_seeded(db: Any) -> None:
    """Idempotently seed the config document. Safe to call on every startup."""
    if db is None:
        return
    existing = await db.promotion_gate_config.find_one({"_id": _CONFIG_ID})
    if not existing:
        doc = dict(_DEFAULT_CONFIG_DOC)
        doc["_seeded_at"] = datetime.now(timezone.utc)
        await db.promotion_gate_config.insert_one(doc)
        logger.info("[promotion_gate] seeded default config")


async def get_config(db: Any) -> PromotionConfig:
    """Return the active PromotionConfig, seeded on first read."""
    if db is None:
        return PromotionConfig()
    doc = await db.promotion_gate_config.find_one({"_id": _CONFIG_ID})
    if not doc:
        await ensure_seeded(db)
        doc = dict(_DEFAULT_CONFIG_DOC)
    kwargs = {k: doc[k] for k in _EDITABLE_FIELDS if k in doc}
    return PromotionConfig(**kwargs)


async def get_config_dict(db: Any) -> dict:
    """Return the raw config document as a plain dict (for admin UI)."""
    if db is None:
        return dict(_DEFAULT_CONFIG_DOC)
    doc = await db.promotion_gate_config.find_one({"_id": _CONFIG_ID})
    if not doc:
        await ensure_seeded(db)
        doc = await db.promotion_gate_config.find_one({"_id": _CONFIG_ID}) or {}
    out = {k: doc.get(k, _DEFAULT_CONFIG_DOC[k]) for k in _EDITABLE_FIELDS}
    return out


async def patch_config(
    db: Any,
    patch: dict,
    *,
    operator: str,
    reason: str | None = None,
    active_epoch_id: str | None = None,
) -> dict:
    """Apply an operator-initiated config patch and audit every field change.

    Only whitelisted fields in ``_EDITABLE_FIELDS`` are accepted. Values are
    coerced to the same type as the default (int/float). Unknown / rejected
    fields are returned in the response so the UI can surface them.
    """
    if db is None:
        raise RuntimeError("promotion_gate: db not wired")
    current = await get_config_dict(db)
    now = datetime.now(timezone.utc)
    accepted: dict = {}
    rejected: list[str] = []
    audit_rows: list[dict] = []
    for k, v in (patch or {}).items():
        if k not in _EDITABLE_FIELDS:
            rejected.append(k)
            continue
        default_val = _DEFAULT_CONFIG_DOC[k]
        try:
            coerced = int(v) if isinstance(default_val, int) and not isinstance(default_val, bool) else float(v)
        except (TypeError, ValueError):
            rejected.append(k)
            continue
        if coerced == current[k]:
            continue
        accepted[k] = coerced
        audit_rows.append({
            "ts": now,
            "field": k,
            "old_value": current[k],
            "new_value": coerced,
            "operator": operator,
            "active_epoch_id": active_epoch_id,
            "reason": reason,
        })
    if accepted:
        await db.promotion_gate_config.update_one(
            {"_id": _CONFIG_ID},
            {"$set": {**accepted, "_updated_at": now, "_updated_by": operator}},
            upsert=True,
        )
        if audit_rows:
            await db.promotion_gate_config_audit.insert_many(audit_rows)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "audit_count": len(audit_rows),
    }


async def get_config_audit(db: Any, limit: int = 50) -> list[dict]:
    """Return the most recent config change events for display."""
    if db is None:
        return []
    out: list[dict] = []
    cursor = db.promotion_gate_config_audit.find({}, {"_id": 0}).sort("ts", -1).limit(int(limit))
    async for row in cursor:
        if isinstance(row.get("ts"), datetime):
            row["ts"] = row["ts"].isoformat()
        out.append(row)
    return out


async def get_active_epoch(db: Any) -> dict | None:
    """Return the most-recent epoch doc (or None if none declared yet)."""
    if db is None:
        return None
    return await db.promotion_gate_epochs.find_one(
        {}, sort=[("started_at", -1)], projection={"_id": 0},
    )


async def list_epochs(db: Any, limit: int = 20) -> list[dict]:
    if db is None:
        return []
    out: list[dict] = []
    cursor = db.promotion_gate_epochs.find({}, {"_id": 0}).sort("started_at", -1).limit(int(limit))
    async for row in cursor:
        if isinstance(row.get("started_at"), datetime):
            row["started_at"] = row["started_at"].isoformat()
        out.append(row)
    return out


async def begin_epoch(
    db: Any,
    *,
    reason: str,
    operator: str,
    execution_policy_hash: str = "",
    code_revision: str | None = None,
) -> dict:
    """Declare a new evaluation epoch. Never deletes historical observations."""
    if db is None:
        raise RuntimeError("promotion_gate: db not wired")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("reason is required to begin a new epoch")
    rev = code_revision or _git_sha()
    epoch = begin_evaluation_epoch(
        code_revision=rev,
        reason=reason,
        execution_policy_hash=execution_policy_hash or "",
    )
    doc = {
        "epoch_id": epoch.epoch_id,
        "started_at": epoch.started_at,
        "code_revision": epoch.code_revision,
        "reason": epoch.reason,
        "execution_policy_hash": epoch.execution_policy_hash,
        "operator": operator,
    }
    await db.promotion_gate_epochs.insert_one(dict(doc))
    logger.info(
        "[promotion_gate] new epoch=%s revision=%s operator=%s reason=%r",
        epoch.epoch_id, epoch.code_revision, operator, reason,
    )
    doc["started_at"] = epoch.started_at.isoformat()
    return doc


def _return_pct(entry: Any, exit_: Any) -> float | None:
    try:
        e = float(entry)
        x = float(exit_)
    except (TypeError, ValueError):
        return None
    if e <= 0.0:
        return None
    return (x - e) / e * 100.0


def _coerce_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


async def source_observations(
    db: Any,
    active_epoch_id: str,
    epoch_started_at: datetime | None,
) -> list[EvaluationObservation]:
    """Convert closed ``equity_live_trades`` rows into EvaluationObservations.

    Observations are assigned to the active epoch when they were RESOLVED
    (``closed_at``) at or after the epoch started. Older resolutions remain
    stamped with epoch_id="pre_" + first-epoch, so lifetime counts are honest
    without poisoning the current epoch.
    """
    if db is None:
        return []
    cursor = db.equity_live_trades.find(
        {"status": "closed", "close_price": {"$exists": True}},
        {
            "trade_id": 1, "symbol": 1, "strategy_id": 1, "closed_at": 1,
            "opened_at": 1, "entry_price": 1, "close_price": 1,
        },
    )
    obs: list[EvaluationObservation] = []
    async for t in cursor:
        resolved = _coerce_dt(t.get("closed_at")) or _coerce_dt(t.get("opened_at"))
        if resolved is None:
            continue
        gross_pct = _return_pct(t.get("entry_price"), t.get("close_price"))
        if gross_pct is None:
            continue
        if epoch_started_at and resolved >= epoch_started_at:
            epoch_id = active_epoch_id
        else:
            epoch_id = "pre_epoch"
        obs.append(EvaluationObservation(
            observation_id=str(t.get("trade_id") or t.get("_id") or ""),
            resolved_at=resolved,
            gross_return_pct=gross_pct,
            symbol=str(t.get("symbol") or ""),
            strategy=str(t.get("strategy_id") or "unknown"),
            epoch_id=epoch_id,
        ))
    return obs


async def source_fills(
    db: Any,
    active_epoch_id: str,
    epoch_started_at: datetime | None,
) -> list[ExecutionFill]:
    """Read real broker fills from ``broker_fills`` (empty collection is fine).

    Empty is expected until a broker-fill webhook is wired; the gate falls
    back to the configured ``assumed_round_trip_cost_pct`` per module design.
    """
    if db is None:
        return []
    fills: list[ExecutionFill] = []
    try:
        cursor = db.broker_fills.find({}, {"_id": 0})
    except AttributeError:
        return []
    async for f in cursor:
        ts = _coerce_dt(f.get("timestamp"))
        if ts is None:
            continue
        if epoch_started_at and ts >= epoch_started_at:
            epoch_id = active_epoch_id
        else:
            epoch_id = "pre_epoch"
        try:
            fills.append(ExecutionFill(
                fill_id=str(f.get("fill_id") or ""),
                trade_id=str(f.get("trade_id") or ""),
                timestamp=ts,
                side=str(f.get("side") or "BUY"),
                fill_price=float(f.get("fill_price") or 0.0),
                quantity=float(f.get("quantity") or 0.0),
                fee_pct=float(f.get("fee_pct") or 0.0),
                reference_price=(float(f["reference_price"])
                                 if f.get("reference_price") is not None else None),
                liquidity=str(f.get("liquidity") or "unknown"),
                epoch_id=epoch_id,
            ))
        except (TypeError, ValueError):
            continue
    return fills


async def evaluate_status(db: Any) -> dict:
    """Assemble the full promotion status payload for the admin UI/API."""
    await ensure_seeded(db)
    cfg = await get_config(db)
    epoch = await get_active_epoch(db)
    active_epoch_id = (epoch or {}).get("epoch_id") or "no_epoch_declared"
    epoch_started_at = (epoch or {}).get("started_at")
    if isinstance(epoch_started_at, str):
        epoch_started_at = _coerce_dt(epoch_started_at)
    observations = await source_observations(db, active_epoch_id, epoch_started_at)
    fills = await source_fills(db, active_epoch_id, epoch_started_at)
    decision = PromotionGate(cfg).evaluate(observations, fills, active_epoch_id)
    payload = decision.to_dict()
    payload["config"] = await get_config_dict(db)
    payload["epoch"] = epoch if epoch else {
        "epoch_id": active_epoch_id,
        "started_at": None,
        "code_revision": None,
        "reason": None,
        "execution_policy_hash": "",
        "operator": None,
        "note": "No epoch has been declared yet. Call POST /api/admin/promotion-gate/begin-epoch to open one.",
    }
    payload["notes"] = {
        "role": (
            "System readiness grader ONLY. This module never vetoes individual "
            "trades and is not wired into the execution hot path."
        ),
        "cost_source_hint": (
            "Cost source is 'assumed' until broker fills are recorded in the "
            "broker_fills collection and the min_measured_fills threshold is met."
        ),
    }
    return payload


__all__ = [
    "ensure_seeded",
    "get_config",
    "get_config_dict",
    "patch_config",
    "get_config_audit",
    "get_active_epoch",
    "list_epochs",
    "begin_epoch",
    "source_observations",
    "source_fills",
    "evaluate_status",
]
