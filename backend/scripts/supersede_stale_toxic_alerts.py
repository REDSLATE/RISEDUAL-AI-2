"""One-shot cleanup: supersede stale toxic-spike notifications.

Run after backfilling / regrading source predictions::

    python /app/backend/scripts/supersede_stale_toxic_alerts.py
    sudo supervisorctl restart backend

What it does
────────────
For every active ``type=toxic_spike`` row in ``db.notifications``,
re-counts the source predictions that triggered it. If zero are
still miss-graded (i.e. they all got regraded after the alert
fired), marks the notification as ``superseded`` so the drawer
API filters it out.

Reads the same lifecycle service the nightly cleanup + drawer API
use, so behaviour is consistent across all three callers.

Safe to re-run — already-superseded rows are skipped via the
status filter.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Project layout: ``scripts/`` is one level below ``backend/`` so
# we add the backend dir to sys.path before importing services.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.notification_lifecycle import supersede_stale_alerts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


async def main() -> None:
    # Use the same Mongo client the live backend uses so we hit the
    # same DB. ``server.py`` exposes ``db`` at import time.
    from server import db  # noqa: PLC0415
    if db is None:
        log.error("DB unavailable — abort.")
        sys.exit(1)
    result = await supersede_stale_alerts(db)
    totals = result["totals"]
    log.info(
        "Notification lifecycle cleanup — totals: checked=%d "
        "superseded=%d kept_active=%d",
        totals["checked"], totals["superseded"], totals["kept_active"],
    )
    for t, r in result["by_type"].items():
        log.info(
            "  %-20s checked=%d superseded=%d kept_active=%d",
            t, r["checked"], r["superseded"], r["kept_active"],
        )


if __name__ == "__main__":
    asyncio.run(main())
