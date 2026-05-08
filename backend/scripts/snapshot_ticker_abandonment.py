"""
One-off / cron-friendly: snapshot today's ticker-abandonment gate
decisions to ``ticker_abandonment_history``.

Why this is a separate script
─────────────────────────────
The admin overview endpoint already opportunistically snapshots on
read. This script gives the operator a guaranteed-once-a-day
trigger so the Δ-since-yesterday column has yesterday's data even
if no admin opened the page yesterday.

Idempotent. Safe to re-run.

Usage
─────
::

    cd /app/backend
    python -m scripts.snapshot_ticker_abandonment

Exit codes
──────────
* 0 — snapshot completed (whether or not any rows were written)
* 1 — Mongo I/O setup failure
"""
from __future__ import annotations

import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient


async def _amain() -> int:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("error: MONGO_URL / DB_NAME not set", file=sys.stderr)
        return 1

    from services.ticker_abandonment_history import snapshot_today

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    counts = await snapshot_today(db)
    print(
        f"snapshot complete: abandon={counts['abandon']} "
        f"cooldown={counts['cooldown']} keep={counts['keep']} "
        f"errors={counts['errors']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
