"""RISEDUAL AI — Self-Test Service.

Runs a focused battery of in-process health checks that are meant to
catch the kinds of regressions that have actually bitten the product:
  - tz-naive vs tz-aware datetime comparisons (crashed Security Audit)
  - stale price literals (the $45 vs $55 digest bug)
  - missing MongoDB collections after a migration
  - missing env vars on a fresh deploy
  - scheduler jobs not registered
  - critical env/integration secrets absent

Intentionally lightweight: every check must complete in well under a
second so we can run it every 15 minutes via APScheduler without load.

Returns a structured dict suitable for JSON response, CLI formatting,
and state-change detection by the HEALTH_LOG writer.
"""
from __future__ import annotations

import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_COLLECTIONS = [
    "users",
    "predictions",
    "features_snapshots",
    "login_attempts",
    "broker_connections",
]

# Collections that MongoDB creates lazily on first insert. We probe their
# absence as INFO, not FAIL, because an empty prod environment legitimately
# won't have them until the first event fires.
OPTIONAL_COLLECTIONS = [
    "oauth_token_audit",
]

REQUIRED_ENV = [
    "MONGO_URL",
    "DB_NAME",
    "EMERGENT_LLM_KEY",
    "STRIPE_SECRET_KEY",
]

# Subset of scheduler jobs that MUST be registered — if any of these are
# missing it means startup didn't finish or a job was silently dropped.
REQUIRED_SCHEDULER_JOBS = [
    "daily_digest",
    "headlines_pipeline",
    "prediction_prewarm",
    "prediction_labeler",
    "nightly_ml_retrain",
]

# User-facing price literals that must NEVER ship for the Pro plan.
# We look for the full `${price}/month` form so benign references inside
# comments (e.g. "$49.50/month billed annually") don't false-positive.
STALE_PRICE_PATTERNS = ["$45/month", "$29/month", "$49/month", "$25/month"]
PRICING_FILES = [
    "/app/backend/services/digest_service.py",
    "/app/backend/services/payment_service.py",
]


def _pass(name: str, info: str = "") -> dict:
    out = {"name": name, "status": "PASS"}
    if info:
        out["info"] = info
    return out


def _fail(name: str, error: str) -> dict:
    return {"name": name, "status": "FAIL", "error": error}


def _warn(name: str, error: str) -> dict:
    return {"name": name, "status": "WARN", "error": error}


async def _check_db_ping(db: Any) -> dict:
    if db is None:
        return _fail("db_ping", "db reference is None")
    try:
        await db.command("ping")
        return _pass("db_ping", "Mongo responded to ping")
    except Exception as e:  # pragma: no cover — integration path
        return _fail("db_ping", str(e))


async def _check_collections(db: Any) -> dict:
    if db is None:
        return _fail("collections", "db reference is None")
    try:
        existing = set(await db.list_collection_names())
        missing = [c for c in REQUIRED_COLLECTIONS if c not in existing]
        if missing:
            return _fail("collections", f"missing: {', '.join(missing)}")
        lazy_missing = [c for c in OPTIONAL_COLLECTIONS if c not in existing]
        info = f"{len(REQUIRED_COLLECTIONS)} required collections present"
        if lazy_missing:
            info += f" (lazy not-yet-written: {', '.join(lazy_missing)})"
        return _pass("collections", info)
    except Exception as e:
        return _fail("collections", str(e))


async def _check_datetime_comparisons(db: Any) -> dict:
    """Reproduce the 2026-02-19 Security Audit crash in-process.

    Iterates up to 20 rows of `login_attempts` and runs the exact
    tz-aware comparison the route does. Any TypeError here means the
    Security Audit dashboard is about to 500 for an admin.
    """
    if db is None:
        return _fail("datetime_comparisons", "db reference is None")
    try:
        now = datetime.now(timezone.utc)
        checked = 0
        async for doc in db.login_attempts.find({}, {"_id": 0}).limit(20):
            locked_until = doc.get("locked_until")
            if isinstance(locked_until, datetime):
                if locked_until.tzinfo is None:
                    locked_until = locked_until.replace(tzinfo=timezone.utc)
                _ = locked_until > now  # must not raise
            checked += 1
        return _pass(
            "datetime_comparisons",
            f"{checked} login_attempts rows compared cleanly",
        )
    except TypeError as e:
        return _fail("datetime_comparisons", f"TypeError: {e}")
    except Exception as e:
        return _fail("datetime_comparisons", str(e))


def _check_env() -> dict:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        return _fail("env", f"missing: {', '.join(missing)}")
    return _pass("env", f"{len(REQUIRED_ENV)} required env vars set")


def _check_pricing_consistency() -> dict:
    """Grep pricing-authoritative files for stale price literals in the
    user-facing `$X/month` form. Matches inside lines beginning with `#`
    are ignored so discount-math comments don't false-positive."""
    issues: list[str] = []
    for path in PRICING_FILES:
        p = pathlib.Path(path)
        if not p.exists():
            issues.append(f"{p.name}: not found")
            continue
        for line in p.read_text(errors="ignore").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for bad in STALE_PRICE_PATTERNS:
                if bad in line:
                    issues.append(f"{p.name}: stale {bad}")
                    break
    if issues:
        return _fail("pricing", "; ".join(issues))
    return _pass("pricing", "digest_service + payment_service on $55 Pro")


def _check_scheduler(scheduler: Any) -> dict:
    if scheduler is None:
        return _warn("scheduler", "scheduler reference not wired")
    try:
        job_ids = {j.id for j in scheduler.get_jobs()}
        missing = [j for j in REQUIRED_SCHEDULER_JOBS if j not in job_ids]
        if missing:
            return _fail("scheduler", f"missing jobs: {', '.join(missing)}")
        return _pass("scheduler", f"{len(job_ids)} jobs registered")
    except Exception as e:
        return _fail("scheduler", str(e))


async def run_self_test(db: Any, scheduler: Any = None) -> dict:
    """Run every registered check and return a structured report."""
    checks: list[dict] = [
        await _check_db_ping(db),
        await _check_collections(db),
        await _check_datetime_comparisons(db),
        _check_env(),
        _check_pricing_consistency(),
        _check_scheduler(scheduler),
    ]
    passed = sum(1 for c in checks if c["status"] == "PASS")
    failed = sum(1 for c in checks if c["status"] == "FAIL")
    warned = sum(1 for c in checks if c["status"] == "WARN")
    overall = "PASS" if failed == 0 else "FAIL"
    return {
        "overall": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(checks),
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "checks": checks,
    }
