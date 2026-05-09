"""Bulk Replay — pre-ingest CSV scanner for historical paper-trade
exports.

────────────────────────────────────────────────────────────────────
AUTHORITY-BOUNDARY CONTRACT
────────────────────────────────────────────────────────────────────

Operator drag-drops a CSV. Every row is passed through the existing
``services.chevelle_memory_labeler`` firewall and the per-row
verdict + an aggregate summary are returned for inspection. NOTHING
is written to Mongo, NOTHING is trained on, NOTHING is promoted, and
NO broker / executor calls are made.

Hard rules (pinned by tests)
----------------------------
  * Owner-only (`role == "owner"`).
  * Row cap: ``MAX_ROWS_PER_UPLOAD`` (default 500).
  * Byte cap: ``MAX_FILE_BYTES`` (default 2 MiB).
  * Malformed rows return per-row errors — endpoint NEVER 500s on
    a single bad row.
  * Endpoint performs ZERO Mongo writes (static check + runtime).
  * No `import_into_memory`, `train_all`, or `force_train` actions
    exposed (endpoint is read-only by construction).
  * Returns the same ``ChevelleMemoryRecord`` shape the labeler
    emits, so the frontend renders verdicts identical to what
    Chevelle would see at training time.
"""
from __future__ import annotations

import csv
import io
import logging
from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from routes.auth import get_current_user
from services.chevelle_memory_labeler import (
    OBSERVATION_POLICY_ALL,
    label_memory,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/bulk-replay",
    tags=["admin", "chevelle", "bulk-replay"],
)


# ── Caps (operator-tunable invariants) ──────────────────────────────


MAX_ROWS_PER_UPLOAD: int = 500
MAX_FILE_BYTES: int = 2 * 1024 * 1024  # 2 MiB


# ── Auth helper ─────────────────────────────────────────────────────


async def _require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return user


# ── CSV parsing ─────────────────────────────────────────────────────


def _normalize_header(h: str) -> str:
    return (h or "").strip().lower().replace(" ", "_").replace("-", "_")


def _parse_csv_payload(raw_bytes: bytes) -> tuple[list[dict], list[dict]]:
    """Parse the uploaded CSV into ``(rows, errors)``.

    * ``rows`` — list of dicts (headers normalised to snake_case).
    * ``errors`` — list of ``{"row_index": int, "error": str}`` for
      lines that couldn't be parsed at all (kept SEPARATE from
      labeler quarantines so the operator can tell the difference
      between "bad CSV row" and "bad data semantics").
    """
    try:
        decoded = raw_bytes.decode("utf-8-sig", errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=f"unable to decode CSV as UTF-8: {exc}",
        ) from exc

    if not decoded.strip():
        return [], []

    # Use a permissive parser — quoting may be inconsistent across
    # historical paper-trade exports.
    reader = csv.DictReader(io.StringIO(decoded))
    if reader.fieldnames is None:
        raise HTTPException(
            status_code=400, detail="CSV has no header row",
        )

    fieldnames = [_normalize_header(f) for f in reader.fieldnames]

    rows: list[dict] = []
    errors: list[dict] = []
    for idx, raw_row in enumerate(reader, start=2):  # row 1 is header
        if idx - 1 > MAX_ROWS_PER_UPLOAD:
            errors.append({
                "row_index": idx,
                "error": (
                    f"row cap exceeded — only the first "
                    f"{MAX_ROWS_PER_UPLOAD} rows were scanned"
                ),
            })
            break
        try:
            normalised = {
                _normalize_header(k): (v.strip() if isinstance(v, str) else v)
                for k, v in raw_row.items()
            }
            # Drop the synthetic ``None`` key DictReader may emit
            # for trailing commas.
            normalised.pop("", None)
            normalised.pop(None, None)
            normalised["_csv_row_index"] = idx
            rows.append(normalised)
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "row_index": idx, "error": f"parse_failed: {exc}",
            })

    if len(rows) == 0 and not fieldnames:
        raise HTTPException(
            status_code=400, detail="CSV produced zero parseable rows",
        )

    return rows, errors


# ── Aggregate helpers ───────────────────────────────────────────────


def _build_aggregate(records: list[dict]) -> dict:
    """Tally the labeled records into the operator-facing summary.

    All counters are over the BUCKET tokens emitted by the labeler
    so the rollups stay stable across trust-ladder edits.
    """
    if not records:
        return {
            "total_rows": 0,
            "trainable_count": 0,
            "quarantined_count": 0,
            "toxic_count": 0,
            "avg_trust_weight": 0.0,
            "by_source": {},
            "by_lane": {},
            "by_event_era": {},
            "by_failure_mode": {},
            "by_data_quality": {},
        }

    by_source: Counter = Counter()
    by_lane: Counter = Counter()
    by_event_era: Counter = Counter()
    by_failure_mode: Counter = Counter()
    by_data_quality: Counter = Counter()

    trainable = 0
    quarantined = 0
    toxic = 0
    trust_sum = 0.0

    for r in records:
        by_source[r["source"]] += 1
        by_lane[r["lane"]] += 1
        by_event_era[r["event_era"]] += 1
        by_failure_mode[r["failure_mode"]] += 1
        by_data_quality[r["data_quality"]] += 1
        trust_sum += float(r.get("trust_weight") or 0.0)
        if r.get("trainable"):
            trainable += 1
        if r.get("rejection_reason") is not None or float(
            r.get("trust_weight") or 0.0
        ) == 0.0:
            quarantined += 1
        # Toxic = labeled with a toxic failure mode (trust=0.10).
        if r["failure_mode"] in {
            "blowup", "toxic_high_confidence",
            "regime_mismatch", "data_integrity",
        }:
            toxic += 1

    return {
        "total_rows": len(records),
        "trainable_count": trainable,
        "quarantined_count": quarantined,
        "toxic_count": toxic,
        "avg_trust_weight": round(trust_sum / len(records), 4),
        "by_source": dict(by_source),
        "by_lane": dict(by_lane),
        "by_event_era": dict(by_event_era),
        "by_failure_mode": dict(by_failure_mode),
        "by_data_quality": dict(by_data_quality),
    }


def _record_to_dict(rec, *, csv_row_index: int) -> dict:
    """Project a ``ChevelleMemoryRecord`` into the JSON shape the
    frontend renders. Includes the original CSV row index so the
    operator can map verdicts back to source lines."""
    return {
        "csv_row_index": csv_row_index,
        "raw_id": rec.raw_id,
        "symbol": rec.symbol,
        "opened_at": (
            rec.opened_at.isoformat() if rec.opened_at else None
        ),
        "closed_at": (
            rec.closed_at.isoformat() if rec.closed_at else None
        ),
        "event_era": rec.event_era.value,
        "lane": rec.lane,
        "source": rec.source,
        "outcome_label": rec.outcome_label.value,
        "failure_mode": rec.failure_mode.value,
        "data_quality": rec.data_quality.value,
        "trust_weight": rec.trust_weight,
        "trainable": rec.trainable,
        "chevelle_can_observe": rec.chevelle_can_observe,
        "rejection_reason": rec.rejection_reason,
        "rule_trace": list(rec.reasons),
        "grade": _verdict_grade(rec),
    }


def _verdict_grade(rec) -> str:
    """Coarse grade tag the frontend uses for color coding.

    GREEN  — trainable, no failure mode
    AMBER  — trainable but flagged toxic / quarantined-toxic
    RED    — quarantined (missing required fields, etc.)
    """
    if rec.rejection_reason is not None or rec.trust_weight == 0.0:
        return "RED"
    if rec.failure_mode.value != "none":
        return "AMBER"
    return "GREEN"


# ── Endpoint ────────────────────────────────────────────────────────


@router.post("/scan")
async def scan_csv(
    request: Request,
    file: UploadFile = File(...),
) -> dict:
    """Pre-ingest scan of a historical paper-trade CSV.

    NOTHING is written to Mongo. NOTHING is queued for training.
    Returns per-row labeler verdicts + an aggregate summary so the
    operator can review before any future ingest pipeline is built.
    """
    await _require_owner(request)

    # Byte cap — read the body up to MAX_FILE_BYTES + 1 to detect
    # over-cap uploads without buffering an arbitrarily-large file.
    raw_bytes = await file.read(MAX_FILE_BYTES + 1)
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"file exceeds size cap of "
                f"{MAX_FILE_BYTES // 1024} KiB"
            ),
        )

    rows, parse_errors = _parse_csv_payload(raw_bytes)

    # Label every row. ``label_memory`` never raises — malformed
    # rows quarantine themselves with a populated rejection_reason.
    labeled: list[dict] = []
    for r in rows:
        csv_idx = r.pop("_csv_row_index", -1)
        rec = label_memory(r, observation_policy=OBSERVATION_POLICY_ALL)
        labeled.append(_record_to_dict(rec, csv_row_index=csv_idx))

    # Sidecar mirror — observation only. Aggregate verdict per upload
    # is more useful than per-row spam to the monorepo.
    try:
        from services.risedual_monorepo_client import (
            emit_memory_label,
            fire_and_forget,
        )
        agg_preview = _build_aggregate(labeled)
        quarantined = int(agg_preview.get("quarantined_count", 0))
        toxic = int(agg_preview.get("toxic_count", 0))
        total = int(agg_preview.get("total_rows", 0))
        if quarantined > 0:
            label = "quarantine"
        elif toxic > 0:
            label = "review"
        else:
            label = "safe"
        fire_and_forget(emit_memory_label(
            label=label,
            reason="bulk_replay_csv",
            payload_summary=(
                f"file={file.filename} total={total} "
                f"quarantined={quarantined} toxic={toxic}"
            ),
        ))
    except Exception:  # noqa: BLE001
        pass

    aggregate = _build_aggregate(labeled)

    return {
        "filename": file.filename,
        "rows_uploaded": len(rows) + len(parse_errors),
        "rows_scanned": len(labeled),
        "max_rows_per_upload": MAX_ROWS_PER_UPLOAD,
        "max_file_bytes": MAX_FILE_BYTES,
        "aggregate": aggregate,
        "rows": labeled,
        "parse_errors": parse_errors,
        "warnings": _build_warnings(aggregate, len(parse_errors)),
    }


def _build_warnings(aggregate: dict, parse_error_count: int) -> list[str]:
    """Surface common operator-facing concerns. Pure presentation —
    does not change the verdicts."""
    warnings: list[str] = []
    total = aggregate.get("total_rows", 0)
    if total == 0:
        warnings.append("zero_rows_after_parsing")
        return warnings
    if parse_error_count > 0:
        warnings.append(
            f"{parse_error_count} row(s) failed to parse and were "
            f"excluded from the labeling pass"
        )
    quarantined = aggregate.get("quarantined_count", 0)
    if quarantined > 0 and total > 0 and quarantined / total > 0.3:
        warnings.append(
            f"{quarantined}/{total} rows quarantined "
            f"({(quarantined / total) * 100:.1f}%) — most rows are "
            f"missing source / timestamps / symbol; check the "
            f"export schema before treating this corpus as "
            f"trainable"
        )
    toxic = aggregate.get("toxic_count", 0)
    if toxic > 0 and total > 0 and toxic / total > 0.2:
        warnings.append(
            f"{toxic}/{total} rows flagged toxic — Chevelle would "
            f"learn from these at low trust (0.10), not delete them"
        )
    return warnings


# Suppress unused-import warning — re-exported for ergonomic test
# import.
_ = defaultdict
