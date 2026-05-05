"""RISEDUAL AI — Self-Test admin route.

Exposes `/api/admin/self-test` so the CLI script and the Admin UI panel
can both trigger the same check battery on demand.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from services.self_test_service import run_self_test

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["self-test"])

db = None
_scheduler = None


def set_db(database) -> None:
    global db
    db = database


def set_scheduler(scheduler) -> None:
    """Called from server.py once the APScheduler instance is started."""
    global _scheduler
    _scheduler = scheduler


async def _require_admin(request: Request):
    from routes.auth import get_current_user

    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/self-test")
async def get_self_test(request: Request):
    """Run the self-test battery and return a JSON report."""
    await _require_admin(request)
    return await run_self_test(db, _scheduler)


@router.post("/self-test")
async def trigger_self_test(request: Request):
    """Alias of GET — kept so the CLI script and UI button can POST."""
    await _require_admin(request)
    return await run_self_test(db, _scheduler)


@router.get("/scheduler/status")
async def scheduler_status(request: Request):
    """Diagnostic snapshot of the in-process APScheduler.

    Returns:
        * ``running`` — whether ``scheduler.start()`` was called and the
          job loop is alive. ``False`` here is the smoking gun for
          "scheduler hasn't fired in N hours" reports — it usually
          means startup raised inside the ``_start_schedulers`` block
          and the process kept serving HTTP without any cron attached.
        * ``now`` — current server time (UTC ISO).
        * ``jobs`` — every registered job with id, trigger spec, next
          scheduled run (UTC ISO or ``None`` if the job is paused),
          and a ``minutes_until_next`` convenience field.
        * ``paused_jobs`` / ``overdue_jobs`` — quick triage counters.

    Owner/admin only. Read-only.
    """
    await _require_admin(request)

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    if _scheduler is None:
        return {
            "running": False,
            "reason": (
                "scheduler handle was never wired — _start_schedulers "
                "either raised or hasn't run yet"
            ),
            "now": now.isoformat(),
            "jobs": [],
            "paused_jobs": 0,
            "overdue_jobs": 0,
        }

    jobs_out = []
    paused = 0
    overdue = 0

    try:
        jobs = _scheduler.get_jobs()
    except Exception as exc:  # noqa: BLE001
        return {
            "running": False,
            "reason": f"scheduler.get_jobs() raised: {exc}",
            "now": now.isoformat(),
            "jobs": [],
            "paused_jobs": 0,
            "overdue_jobs": 0,
        }

    for job in jobs:
        next_run = getattr(job, "next_run_time", None)
        is_paused = next_run is None
        if is_paused:
            paused += 1
            minutes_until = None
            next_run_iso = None
        else:
            # APScheduler stores tz-aware datetimes; subtract directly.
            delta = (next_run - now).total_seconds() / 60.0
            minutes_until = round(delta, 2)
            next_run_iso = next_run.astimezone(timezone.utc).isoformat()
            # An overdue job (next-run timestamp is in the past) means
            # the executor is wedged or the loop never started.
            if delta < -1.0:
                overdue += 1

        jobs_out.append({
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_utc": next_run_iso,
            "minutes_until_next": minutes_until,
            "paused": is_paused,
        })

    # Stable sort: overdue first, then soonest, paused last.
    def _sort_key(j):
        m = j["minutes_until_next"]
        if j["paused"]:
            return (2, 0)
        if m is not None and m < 0:
            return (0, m)
        return (1, m if m is not None else 0)

    jobs_out.sort(key=_sort_key)

    return {
        "running": getattr(_scheduler, "running", False),
        "now": now.isoformat(),
        "jobs": jobs_out,
        "total_jobs": len(jobs_out),
        "paused_jobs": paused,
        "overdue_jobs": overdue,
    }
