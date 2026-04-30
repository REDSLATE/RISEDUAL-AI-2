"""Admin routes for the ETL framework.

Three endpoints:

* ``GET  /api/admin/etl/jobs`` — list every registered job
  with its last-run summary.
* ``GET  /api/admin/etl/jobs/{source_name}`` — job detail +
  recent history (up to 20 runs).
* ``POST /api/admin/etl/jobs/{source_name}/run`` — manual
  trigger. Returns 202 with the run summary when the job
  completes; the framework's concurrent-run guard prevents
  double-fires if a scheduled run is already in flight.

All endpoints are owner-gated. The manual-trigger endpoint
matters for incident response (e.g. "the source just fixed
an outage, pull now instead of waiting for Monday 4 AM").
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.etl_registry import (
    all_jobs,
    get_job,
    get_last_run,
    get_run_history,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/etl", tags=["admin", "etl"])

_db: Any = None


def set_db(database: Any) -> None:
    """Hook for ``route_registry`` to wire the Mongo handle."""
    global _db
    _db = database


async def _require_owner(request: Request) -> dict:
    """Owner-only gate — matches the pattern used by other admin
    routes (``admin_memory_drift``, ``admin_position_reconciler``)."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if (user.get("role") or "").lower() not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _job_summary(job) -> dict[str, Any]:
    """Compact JSON shape for the list endpoint."""
    return {
        "source_name": job.source_name,
        "description": job.description,
        "enabled": job.enabled,
        "retention_days": job.retention_days,
        "unique_key_fields": list(job.unique_key_fields),
        "cadence": job.cadence,
    }


@router.get("/jobs")
async def list_etl_jobs(request: Request) -> dict:
    """Every registered job + its last-run summary."""
    await _require_owner(request)
    jobs = []
    for job in all_jobs():
        row = _job_summary(job)
        row["last_run"] = await get_last_run(_db, job.source_name)
        jobs.append(row)
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/jobs/{source_name}")
async def get_etl_job(request: Request, source_name: str) -> dict:
    """Detail view for a single job — full config + recent history."""
    await _require_owner(request)
    job = get_job(source_name)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"No ETL job registered with source_name='{source_name}'",
        )
    return {
        **_job_summary(job),
        "history": await get_run_history(_db, source_name, limit=20),
    }


@router.post("/jobs/{source_name}/run")
async def run_etl_job_manually(
    request: Request, source_name: str,
) -> dict:
    """Manually trigger a run.

    Synchronous: waits for the job to complete and returns the
    run summary. Most jobs finish in under a minute; if a
    particular subclass is slow enough to warrant fire-and-forget
    mode, we can swap this to a FastAPI ``BackgroundTasks`` when
    the need appears.
    """
    user = await _require_owner(request)
    job = get_job(source_name)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"No ETL job registered with source_name='{source_name}'",
        )
    if _db is None:
        raise HTTPException(status_code=503, detail="db unavailable")

    logger.info(
        "[etl] manual trigger: source=%s by=%s",
        source_name, user.get("email") or user.get("_id"),
    )
    summary = await job.run(_db, trigger="manual")
    return summary
