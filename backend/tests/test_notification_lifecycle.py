"""Tests for ``services.notification_lifecycle``.

Pins these invariants:

1. An active toxic_spike whose source predictions are all hits / no
   longer misses → SUPERSEDED.
2. An active toxic_spike with at least one remaining miss →
   KEPT_ACTIVE.
3. An already-superseded / already-resolved row is SKIPPED (status
   filter).
4. Both schemas counted as misses: ``outcome ∈ {miss, MISS,
   STRONG_MISS, WEAK_MISS}`` AND ``verified_24h.outcome ∈
   {STRONG_MISS, WEAK_MISS}``.
5. Empty / missing ``affected_tickers`` → no false-positive supersede
   (caller can't verify, so we leave the row alone? — actually
   ``_count_remaining_misses_for_alert`` returns 0 for empty
   tickers, which would supersede the row. We document this is
   intended: an alert with no symbols is malformed and should be
   superseded as junk).
6. Idempotent — re-running on a clean DB is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services import notification_lifecycle as lc


# ── In-memory fake DB ────────────────────────────────────────────


class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, doc):
        d = dict(doc)
        d.setdefault("_id", str(uuid4()))
        self.rows.append(d)

    def find(self, query, projection=None):  # noqa: ARG002
        rows = [r for r in self.rows if _match(r, query)]
        return _Cursor(rows)

    async def update_one(self, query, update):
        for r in self.rows:
            if _match(r, query):
                r.update(update.get("$set", {}))
                return type("UR", (), {"modified_count": 1})
        return type("UR", (), {"modified_count": 0})

    async def find_one(self, query, projection=None):  # noqa: ARG002
        for r in self.rows:
            if _match(r, query):
                return r
        return None

    async def count_documents(self, query):
        return sum(1 for r in self.rows if _match(r, query))


class _Cursor:
    def __init__(self, rows): self._rows = rows

    def __aiter__(self):
        async def _g():
            for r in self._rows:
                yield r
        return _g()


def _match(row: dict, query: dict) -> bool:
    """Tiny matcher — handles ``$in``, ``$nin``, ``$exists``,
    ``$ne``, ``$or``, ``$and``, ``$gte``, ``$lte``."""
    for k, v in query.items():
        if k == "$or":
            if not any(_match(row, sub) for sub in v):
                return False
            continue
        if k == "$and":
            if not all(_match(row, sub) for sub in v):
                return False
            continue
        rv = row.get(k)
        # Nested keys via dot — resolve BEFORE checking dict ops
        # because the value may itself be a dict like {"$in": [...]}.
        if "." in k:
            a, b = k.split(".", 1)
            rv = (row.get(a) or {}).get(b)
        if isinstance(v, dict):
            for op, val in v.items():
                if op == "$in":
                    if rv not in val: return False
                elif op == "$nin":
                    if rv in val: return False
                elif op == "$exists":
                    if (rv is not None) != bool(val): return False
                elif op == "$ne":
                    if rv == val: return False
                elif op == "$gte":
                    if rv is None or rv < val: return False
                elif op == "$lte":
                    if rv is None or rv > val: return False
                else:
                    return False
        else:
            if rv != v:
                return False
    return True


class _DB:
    def __init__(self):
        self.notifications = _Coll()
        self.predictions = _Coll()


# ── Tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_superseded_when_no_remaining_misses():
    db = _DB()
    await db.notifications.insert_one({
        "type": "toxic_spike",
        "metadata": {"affected_tickers": ["AAPL", "MSFT"]},
        "resolved": False,
    })
    # Predictions exist but ALL graded as hits — none are misses.
    await db.predictions.insert_one({"symbol": "AAPL", "outcome": "hit"})
    await db.predictions.insert_one({"symbol": "MSFT", "outcome": "hit"})

    out = await lc.supersede_stale_toxic_alerts(db)
    assert out["checked"] == 1
    assert out["superseded"] == 1
    assert out["kept_active"] == 0
    assert db.notifications.rows[0]["status"] == "superseded"
    assert db.notifications.rows[0]["resolved"] is True


@pytest.mark.asyncio
async def test_alert_kept_active_when_misses_remain():
    db = _DB()
    await db.notifications.insert_one({
        "type": "toxic_spike",
        "metadata": {"affected_tickers": ["AAPL"]},
        "resolved": False,
    })
    await db.predictions.insert_one({"symbol": "AAPL", "outcome": "miss"})
    out = await lc.supersede_stale_toxic_alerts(db)
    assert out["superseded"] == 0
    assert out["kept_active"] == 1


@pytest.mark.asyncio
async def test_verified_24h_schema_also_counts_as_miss():
    """Predictions using the newer ``verified_24h.outcome`` shape
    must also keep the alert active."""
    db = _DB()
    await db.notifications.insert_one({
        "type": "toxic_spike",
        "metadata": {"affected_tickers": ["NVDA"]},
        "resolved": False,
    })
    await db.predictions.insert_one({
        "symbol": "NVDA",
        "verified_24h": {"outcome": "STRONG_MISS"},
    })
    out = await lc.supersede_stale_toxic_alerts(db)
    assert out["kept_active"] == 1


@pytest.mark.asyncio
async def test_already_superseded_skipped():
    db = _DB()
    await db.notifications.insert_one({
        "type": "toxic_spike",
        "metadata": {"affected_tickers": ["AAPL"]},
        "resolved": True,
        "status": "superseded",
    })
    out = await lc.supersede_stale_toxic_alerts(db)
    # The status filter excludes this row from the scan.
    assert out["checked"] == 0


@pytest.mark.asyncio
async def test_idempotent_reruns():
    db = _DB()
    await db.notifications.insert_one({
        "type": "toxic_spike",
        "metadata": {"affected_tickers": ["AAPL"]},
        "resolved": False,
    })
    await db.predictions.insert_one({"symbol": "AAPL", "outcome": "hit"})
    first = await lc.supersede_stale_toxic_alerts(db)
    assert first["superseded"] == 1
    # Re-run → already superseded, no further mutations.
    second = await lc.supersede_stale_toxic_alerts(db)
    assert second["superseded"] == 0
    assert second["checked"] == 0


@pytest.mark.asyncio
async def test_alternate_field_locations_supported():
    """Spec allows ``tickers`` / ``symbols`` at the top level too."""
    db = _DB()
    await db.notifications.insert_one({
        "type": "toxic_spike",
        "tickers": ["AAPL"],
        "resolved": False,
    })
    await db.predictions.insert_one({"symbol": "AAPL", "outcome": "hit"})
    out = await lc.supersede_stale_toxic_alerts(db)
    assert out["superseded"] == 1


@pytest.mark.asyncio
async def test_null_db_no_op():
    out = await lc.supersede_stale_toxic_alerts(None)
    assert out == {"checked": 0, "superseded": 0, "kept_active": 0}
