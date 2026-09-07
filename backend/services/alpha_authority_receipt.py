"""alpha_authority_receipt — compact end-to-end trade receipt.

Purpose
-------
Every live trade in RISEDUAL passes through a chain of authorities:
brain vote → seat authority → RoadGuard → broker capability →
broker submit → broker ack → fill → outcome. The raw
observations are already written to the SQLite hot store's
``lifecycle_events`` table by the day-trader tick. This module
distills that chain into a SINGLE compact document per trade
(``alpha_authority_receipts`` in Mongo) so the operator can audit
"did every gate pass, and if not, which one blocked" without
scanning hundreds of lifecycle rows.

Design
------
* Details stay in SQLite (unbounded volume, cheap to prune).
* Mongo gets one summary doc per resolved trade. Bounded volume,
  indexed on ``setup_id`` + ``symbol`` + ``created_at``.
* The assembler is idempotent — re-running for the same
  ``setup_id`` overwrites the receipt via ``upsert`` so backfills
  and retries never double-write.
* Missing stages (e.g. no explicit ``seat_authority`` event in
  the current codebase) surface as ``"not_recorded"`` rather than
  being fabricated. The receipt reports what actually happened.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Ordered pipeline stages. Each stage maps to zero or more
# lifecycle events; the assembler picks the FIRST matching event
# per stage so the timing reflects when authority was granted (or
# refused). Rejection-only events (e.g. ``chasing_filter``) show
# up on the stage they abort so the operator sees WHERE the chain
# broke.
_STAGE_EVENT_MAP: dict[str, tuple[str, ...]] = {
    "brain_vote": (
        "setup_detected",
        "no_pattern_match",
        "opportunity_score_rejected",
    ),
    "wave_intelligence": (
        "wave_danger_pause",
    ),
    "trigger": (
        "triggered",
        "invalidated",
    ),
    "intent": (
        "intent_created",
        "intent_deduplicated",
    ),
    "seat_authority": (
        "exec_lock_conflict",
    ),
    "roadguard": (
        "executor_rejected",
    ),
    "broker_capability": (
        "execution_quote_confirmed",
        "execution_quote_blocked",
    ),
    "broker_submit": (
        "broker_submitted",
    ),
}

# Events that indicate a stage passed (rather than blocked).
_PASS_EVENTS: frozenset[str] = frozenset({
    "setup_detected",
    "triggered",
    "intent_created",
    "execution_quote_confirmed",
    "broker_submitted",
})

# Events that indicate a stage explicitly blocked the chain.
_BLOCK_EVENTS: frozenset[str] = frozenset({
    "no_pattern_match",
    "opportunity_score_rejected",
    "wave_danger_pause",
    "invalidated",
    "intent_deduplicated",
    "exec_lock_conflict",
    "executor_rejected",
    "execution_quote_blocked",
})


def _load_events(setup_id: str) -> list[dict]:
    """Read all lifecycle rows for a setup, oldest first."""
    from services import alpha_hot_store
    alpha_hot_store.init()
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        rows = con.execute(
            "SELECT event, stage, symbol, payload, ts_ns "
            "FROM lifecycle_events WHERE setup_id=? ORDER BY ts_ns ASC",
            (setup_id,),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        out.append({
            "event": r["event"],
            "stage": r["stage"],
            "symbol": r["symbol"],
            "payload": payload,
            "ts_ns": int(r["ts_ns"]),
        })
    return out


def _ns_to_iso(ns: Optional[int]) -> Optional[str]:
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


def _build_chain(events: list[dict]) -> tuple[list[dict], Optional[str]]:
    """Map lifecycle events to the ordered authority chain.

    Returns (chain, failure_stage). ``failure_stage`` is the first
    stage that blocked the trade; None when every recorded stage
    passed (i.e. the trade made it to broker_submit).
    """
    chain: list[dict] = []
    failure_stage: Optional[str] = None
    for stage_name, event_names in _STAGE_EVENT_MAP.items():
        # First matching event wins so the timing reflects the
        # authority moment. If a stage never fired, mark it
        # "not_recorded" — do NOT fabricate a pass.
        match = next(
            (e for e in events if e["event"] in event_names),
            None,
        )
        if match is None:
            chain.append({
                "stage": stage_name,
                "status": "not_recorded",
                "ts": None,
                "event": None,
                "reason": None,
            })
            continue
        ev = match["event"]
        if ev in _PASS_EVENTS:
            status = "passed"
            reason = None
        elif ev in _BLOCK_EVENTS:
            status = "blocked"
            reason = (
                match["payload"].get("reason")
                or match["payload"].get("reject_reason")
                or match["stage"]
                or ev
            )
            if failure_stage is None:
                failure_stage = stage_name
        else:
            status = "unknown"
            reason = ev
        chain.append({
            "stage": stage_name,
            "status": status,
            "ts": _ns_to_iso(match["ts_ns"]),
            "event": ev,
            "reason": reason,
        })
    return chain, failure_stage


async def build_receipt(db: Any, *, setup_id: str) -> Optional[dict]:
    """Assemble the compact receipt for one setup. Returns ``None``
    when there are no lifecycle events (invalid setup_id).

    Idempotent — call as many times as you want; the last call
    wins on the Mongo side via upsert.
    """
    if not setup_id:
        return None
    events = _load_events(setup_id)
    if not events:
        return None

    chain, failure_stage = _build_chain(events)
    first = events[0]
    last = events[-1]
    symbol = first.get("symbol") or first["payload"].get("symbol") or ""
    setup_type = None
    for e in events:
        st = e["payload"].get("setup_type")
        if st:
            setup_type = st
            break

    # Join outcome from Mongo. Best-effort — missing outcomes are
    # noted but do not block the receipt itself.
    outcome_summary: dict[str, Any] = {}
    if db is not None:
        try:
            doc = await db.alpha_outcomes.find_one({"setup_id": setup_id})
            if doc:
                outcome_summary = {
                    "submitted": bool(doc.get("order_submitted")),
                    "filled": bool(doc.get("filled")),
                    "reject_reason": doc.get("reject_reason"),
                    "realized_r": doc.get("realized_r"),
                    "mfe": doc.get("mfe"),
                    "mae": doc.get("mae"),
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("[authority_receipt] outcome join failed: %s", exc)

    # Determine final status.
    if outcome_summary.get("filled"):
        final_status = "filled"
    elif outcome_summary.get("submitted"):
        final_status = "submitted"
    elif failure_stage:
        final_status = "blocked_at_" + failure_stage
    else:
        final_status = "unresolved"

    authority_verified = (
        failure_stage is None
        and any(s["stage"] == "broker_submit" and s["status"] == "passed"
                for s in chain)
    )

    receipt = {
        "setup_id": setup_id,
        "symbol": (symbol or "").upper(),
        "setup_type": setup_type,
        "ts_started": _ns_to_iso(first["ts_ns"]),
        "ts_completed": _ns_to_iso(last["ts_ns"]),
        "chain": chain,
        "final_status": final_status,
        "authority_verified": authority_verified,
        "failure_stage": failure_stage,
        "outcome": outcome_summary,
        "hot_store_ref": setup_id,
        "created_at": datetime.now(timezone.utc),
        "schema_version": 1,
    }
    return receipt


async def persist_receipt(db: Any, receipt: dict) -> None:
    """Upsert the receipt into ``alpha_authority_receipts``.
    Non-raising — receipt generation shouldn't kill callers on
    Mongo hiccups."""
    if db is None or not receipt:
        return
    try:
        await db.alpha_authority_receipts.update_one(
            {"setup_id": receipt["setup_id"]},
            {"$set": receipt},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[authority_receipt] persist failed: %s", exc)


async def build_and_persist(db: Any, *, setup_id: str) -> Optional[dict]:
    """Convenience wrapper — build + persist in one call. Returns
    the receipt (or None when unbuildable). Safe to call
    fire-and-forget from the trade path."""
    receipt = await build_receipt(db, setup_id=setup_id)
    if receipt is None:
        return None
    await persist_receipt(db, receipt)
    return receipt


async def get_receipt(db: Any, *, setup_id: str) -> Optional[dict]:
    """Read helper for the admin endpoint."""
    if db is None:
        return None
    try:
        doc = await db.alpha_authority_receipts.find_one({"setup_id": setup_id})
    except Exception:  # noqa: BLE001
        return None
    if not doc:
        return None
    doc.pop("_id", None)
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc


async def list_receipts(db: Any, *, limit: int = 50) -> list[dict]:
    """Read helper — most recent receipts first."""
    if db is None:
        return []
    out: list[dict] = []
    try:
        cursor = (
            db.alpha_authority_receipts
              .find({}, {"_id": 0})
              .sort("created_at", -1)
              .limit(int(limit))
        )
        async for row in cursor:
            if isinstance(row.get("created_at"), datetime):
                row["created_at"] = row["created_at"].isoformat()
            out.append(row)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[authority_receipt] list failed: %s", exc)
    return out


__all__ = [
    "build_receipt",
    "persist_receipt",
    "build_and_persist",
    "get_receipt",
    "list_receipts",
]
