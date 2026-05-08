"""Promotion checklist — read-only Phase 6 readiness aggregator.

Pulls from existing surfaces (artifact_inventory + executor_heartbeat +
boot_receipts + alpha_decision_log) and emits an 8-check status
report with GREEN / YELLOW / RED statuses.

Hard-rule invariants:
  * Read-only. Never mutates env, artifacts, broker state, or
    pipeline state.
  * Never loads joblib files.
  * Never calls a broker / executor / pipeline.
  * The report may say "Ready for review" — it MUST NOT say
    "Promote now" anywhere.

The checks intentionally mirror the wedge alerter / heartbeat /
artifact tile thresholds so an operator who passes them all can
trust the dashboard's existing read-outs match this aggregator.
"""
from __future__ import annotations

__domain__ = "PRD"

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.ml import artifact_inventory, executor_heartbeat, boot_receipts


STATUS_GREEN = "GREEN"
STATUS_YELLOW = "YELLOW"
STATUS_RED = "RED"

# Mirror the existing thresholds elsewhere in the codebase so the
# checklist stays in lockstep with the actual safety code.
def _stale_age_hours() -> float:
    try:
        return float(os.environ.get("MAX_MODEL_AGE_HOURS", "72"))
    except (TypeError, ValueError):
        return 72.0


def _flood_threshold() -> int:
    try:
        return int(os.environ.get("WEDGE_FEATURE_HEALTH_THRESHOLD", "50"))
    except (TypeError, ValueError):
        return 50


def _flood_reason() -> str:
    return "STRATEGIST_FEATURE_HEALTH_LOW"


def _shadow_review_window_hours() -> float:
    try:
        return float(os.environ.get("PROMOTION_SHADOW_WINDOW_HOURS", "24"))
    except (TypeError, ValueError):
        return 24.0


def _shadow_min_rows() -> int:
    try:
        return int(os.environ.get("PROMOTION_SHADOW_MIN_ROWS", "1"))
    except (TypeError, ValueError):
        return 1


_ENV_TO_TYPE = {
    "STRATEGIST_ARTIFACT": "strategist",
    "AUDITOR_ARTIFACT":    "auditor",
}


# ── Pure check helpers ─────────────────────────────────────────


def _newest_per_type(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """artifact_inventory.list_artifacts is already sorted desc by
    mtime, so the first occurrence per model_type is the newest."""
    out: Dict[str, Dict[str, Any]] = {}
    for it in items:
        t = it.get("model_type")
        if t in ("strategist", "auditor") and t not in out:
            out[t] = it
    return out


def _active_per_type(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for it in items:
        if it.get("currently_pointed_to_by_env"):
            t = it.get("model_type")
            if t in ("strategist", "auditor"):
                out[t] = it
    return out


def _check_manifest_for_latest(
    newest: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if not newest:
        return _check(
            "manifest_present", "Latest artifact has manifest",
            STATUS_YELLOW,
            "No strategist/auditor artifacts found in models dir.",
        )
    missing = [t for t, it in newest.items() if not it.get("manifest_present")]
    if missing:
        return _check(
            "manifest_present", "Latest artifact has manifest",
            STATUS_YELLOW,
            f"Latest {','.join(sorted(missing))} artifact has no sibling manifest.json.",
        )
    return _check(
        "manifest_present", "Latest artifact has manifest",
        STATUS_GREEN,
        f"Latest artifacts have manifests ({', '.join(sorted(newest.keys()))}).",
    )


def _check_latest_newer_than_active(
    newest: Dict[str, Dict[str, Any]],
    active: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if not newest:
        return _check(
            "latest_newer_than_active", "Latest artifact ≥ active artifact",
            STATUS_YELLOW, "No artifacts to compare.",
        )
    if not active:
        return _check(
            "latest_newer_than_active", "Latest artifact ≥ active artifact",
            STATUS_YELLOW,
            "No active artifact pointed to by env — nothing to compare against.",
        )
    older = []
    for t, latest in newest.items():
        cur = active.get(t)
        if cur is None:
            continue
        # Both rows came from the same listing with the same age_hours
        # delta semantics; compare directly.
        if (latest.get("mtime") or "") < (cur.get("mtime") or ""):
            older.append(t)
    if older:
        return _check(
            "latest_newer_than_active", "Latest artifact ≥ active artifact",
            STATUS_YELLOW,
            f"Latest {','.join(older)} artifact is OLDER than active — operator already promoted ahead of training.",
        )
    return _check(
        "latest_newer_than_active", "Latest artifact ≥ active artifact",
        STATUS_GREEN, "Latest artifacts are newer or equal to active.",
    )


def _check_active_exists_on_disk() -> Dict[str, Any]:
    pointed: Dict[str, Optional[str]] = {}
    missing: List[str] = []
    for env_var in _ENV_TO_TYPE:
        path = (os.environ.get(env_var) or "").strip()
        pointed[env_var] = path or None
        if path and not Path(path).exists():
            missing.append(env_var)
    set_count = sum(1 for v in pointed.values() if v)
    if set_count == 0:
        return _check(
            "active_on_disk", "Active env artifact exists on disk",
            STATUS_YELLOW,
            "Neither STRATEGIST_ARTIFACT nor AUDITOR_ARTIFACT env vars are set.",
        )
    if missing:
        return _check(
            "active_on_disk", "Active env artifact exists on disk",
            STATUS_RED,
            f"Env points at missing file(s): {', '.join(missing)}.",
        )
    return _check(
        "active_on_disk", "Active env artifact exists on disk",
        STATUS_GREEN, f"All set env paths exist ({set_count} active).",
    )


def _check_env_type_matches(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """For each env var that's set, the file it points at must have
    the matching model_type tag in its filename. Cross-type
    misconfigurations are silently treated as 'not active' by the
    artifact tile (defensive), but the checklist surfaces them as
    RED so the operator knows."""
    # Map full_path -> item.
    by_path = {it.get("full_path"): it for it in items}
    mismatches: List[str] = []
    for env_var, expected_type in _ENV_TO_TYPE.items():
        path = (os.environ.get(env_var) or "").strip()
        if not path:
            continue
        try:
            resolved = str(Path(path).resolve())
        except Exception:  # noqa: BLE001
            mismatches.append(f"{env_var} (unresolvable path)")
            continue
        item = by_path.get(resolved) or by_path.get(path)
        if item is None:
            # Already covered by _check_active_exists_on_disk; don't
            # double-RED here.
            continue
        actual_type = item.get("model_type")
        if actual_type != expected_type:
            mismatches.append(
                f"{env_var}=type:{actual_type or 'unknown'} (expected {expected_type})"
            )
    if mismatches:
        return _check(
            "env_type_matches", "Active env artifact type matches env var",
            STATUS_RED,
            "Cross-type env mismatch: " + "; ".join(mismatches),
        )
    return _check(
        "env_type_matches", "Active env artifact type matches env var",
        STATUS_GREEN, "All env-pointed artifacts match their declared type.",
    )


def _check_heartbeat_not_frozen(snap: Dict[str, Any]) -> Dict[str, Any]:
    if snap.get("any_frozen"):
        frozen_lanes = [
            l for l, info in (snap.get("lanes") or {}).items()
            if info.get("frozen")
        ]
        return _check(
            "heartbeat_not_frozen", "Heartbeat lanes are not frozen",
            STATUS_RED,
            f"Frozen lanes: {', '.join(frozen_lanes)}.",
        )
    return _check(
        "heartbeat_not_frozen", "Heartbeat lanes are not frozen",
        STATUS_GREEN, "All lanes alive.",
    )


def _check_no_flood(snap: Dict[str, Any]) -> Dict[str, Any]:
    threshold = _flood_threshold()
    reason = _flood_reason()
    flooded: List[str] = []
    for lane, info in (snap.get("lanes") or {}).items():
        if (info.get("top_clamp_block_reason") == reason and
                int(info.get("top_clamp_block_count") or 0) > threshold):
            flooded.append(
                f"{lane}={info.get('top_clamp_block_count')}× "
                f"{reason}"
            )
    if flooded:
        return _check(
            "no_flood_1h", "No clamp/block flood in last 1h",
            STATUS_RED,
            "Clamp/block flood: " + "; ".join(flooded) +
            f" (threshold {threshold}).",
        )
    return _check(
        "no_flood_1h", "No clamp/block flood in last 1h",
        STATUS_GREEN, "No flood detected.",
    )


def _check_active_age(active: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    threshold = _stale_age_hours()
    if not active:
        return _check(
            "active_age", "Active model age under stale threshold",
            STATUS_YELLOW,
            "No active artifact — nothing to age-check.",
        )
    stale = [
        f"{t}={float(it.get('age_hours') or 0):.1f}h"
        for t, it in active.items()
        if float(it.get("age_hours") or 0) > threshold
    ]
    if stale:
        return _check(
            "active_age", "Active model age under stale threshold",
            STATUS_RED,
            f"Stale active artifact(s): {', '.join(stale)} (threshold {threshold:.0f}h).",
        )
    return _check(
        "active_age", "Active model age under stale threshold",
        STATUS_GREEN,
        "All active artifacts under stale threshold.",
    )


async def _check_shadow_receipts(db: Any) -> Dict[str, Any]:
    window_h = _shadow_review_window_hours()
    min_rows = _shadow_min_rows()
    if db is None:
        return _check(
            "shadow_receipts", "Shadow receipts available for review",
            STATUS_YELLOW, "DB unavailable — cannot count shadow receipts.",
        )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_h)
    try:
        # Count is the cheapest read; never modifies the collection.
        count = await db["alpha_decision_log"].count_documents(
            {"created_at": {"$gte": cutoff}},
        )
    except Exception as exc:  # noqa: BLE001
        return _check(
            "shadow_receipts", "Shadow receipts available for review",
            STATUS_YELLOW, f"Decision-log read failed: {exc}",
        )
    if count < min_rows:
        return _check(
            "shadow_receipts", "Shadow receipts available for review",
            STATUS_YELLOW,
            f"Only {count} shadow receipt(s) in last {window_h:.0f}h "
            f"(min {min_rows}).",
        )
    return _check(
        "shadow_receipts", "Shadow receipts available for review",
        STATUS_GREEN,
        f"{count} shadow receipt(s) in last {window_h:.0f}h.",
    )


def _check(check_id: str, label: str, status: str, detail: str) -> Dict[str, Any]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _overall(checks: List[Dict[str, Any]]) -> Tuple[str, bool, str]:
    statuses = [c["status"] for c in checks]
    if STATUS_RED in statuses:
        return STATUS_RED, False, "Blockers present — review the RED checks."
    if STATUS_YELLOW in statuses:
        return STATUS_YELLOW, False, "Items need review."
    return STATUS_GREEN, True, "Ready for review."


# ── Public entry ────────────────────────────────────────────────


async def build_checklist(*, db: Any = None) -> Dict[str, Any]:
    """Aggregate the 8 checks into a stable JSON shape.

    Strict read-only: never mutates env/artifacts/state. Never
    loads joblib. Never calls broker/executor/pipeline.

    The hard-rule invariant — no "Promote now" — is enforced by
    only emitting ``ready_for_review`` (boolean) and ``message``
    (informational text). No promotion action key.
    """
    items = artifact_inventory.list_artifacts()
    newest = _newest_per_type(items)
    active = _active_per_type(items)

    snap = executor_heartbeat.get_snapshot()

    checks: List[Dict[str, Any]] = []
    checks.append(_check_manifest_for_latest(newest))
    checks.append(_check_latest_newer_than_active(newest, active))
    checks.append(_check_active_exists_on_disk())
    checks.append(_check_env_type_matches(items))
    checks.append(_check_heartbeat_not_frozen(snap))
    checks.append(_check_no_flood(snap))
    checks.append(_check_active_age(active))
    checks.append(await _check_shadow_receipts(db))

    overall, ready_for_review, message = _overall(checks)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "ready_for_review": ready_for_review,
        "message": message,
        "checks": checks,
        # Helpful context (read-only):
        "active_artifacts": {t: it.get("filename") for t, it in active.items()},
        "latest_artifacts": {t: it.get("filename") for t, it in newest.items()},
        "any_frozen": bool(snap.get("any_frozen")),
        # Mirror the env-driven thresholds so the tile can show what
        # bar each check is being held to without re-reading env.
        "thresholds": {
            "stale_age_hours": _stale_age_hours(),
            "flood_count_1h": _flood_threshold(),
            "flood_reason": _flood_reason(),
            "shadow_window_hours": _shadow_review_window_hours(),
            "shadow_min_rows": _shadow_min_rows(),
        },
    }
