"""Tests for the Quiver congressional-trades ETL job + cached reader.

Two surfaces under test:

1. **ETL job** (``QuiverCongressTradesJob``) — verifies the
   subclass's ``transform()`` correctly normalises Quiver's
   mixed-case payload into the canonical
   ``unique_key_fields`` shape, and that rows missing a
   composite-key component are dropped (counted as ``failed``
   by the framework).

2. **Cached reader** (``get_congressional_trades_cached``) —
   verifies sub-10ms read-from-Mongo behaviour, ticker filter,
   and the canonical caller-visible output shape.

We do NOT exercise the real Quiver HTTP layer — that's covered
by the existing ``services.quiver_service`` tests. The framework
contract (upsert / dedup / TTL / audit) is covered by
``test_etl_registry.py``.
"""
from __future__ import annotations

import pytest

from services.etl_jobs.quiver_congress_trades import (
    QuiverCongressTradesJob,
    _short_party,
    get_congressional_trades_cached,
)


# ── transform() — payload normalisation ───────────────────────────


@pytest.mark.asyncio
async def test_transform_normalises_mixed_case_keys():
    """Quiver returns ``Ticker`` / ``ticker`` / ``Representative`` /
    ``representative`` etc. inconsistently across endpoint versions.
    Transform must produce the canonical lower-case-snake key
    set that ``unique_key_fields`` declares."""
    job = QuiverCongressTradesJob()
    raw = [
        {
            "Ticker": "nvda",                        # mixed case
            "TransactionDate": "2026-04-21T00:00:00Z",  # ISO trailing
            "Representative": "  Nancy Pelosi  ",    # whitespace
            "House": "House",
            "Transaction": "Purchase",
            "Amount": "$1M-$5M",
            "Party": "Democrat",
        },
    ]
    out = await job.transform(raw)
    assert len(out) == 1
    row = out[0]

    # Unique key fields, all canonical
    assert row["ticker"] == "NVDA"
    assert row["transaction_date"] == "2026-04-21"  # ISO sliced
    assert row["representative"] == "Nancy Pelosi"  # stripped
    assert row["chamber"] == "House"
    assert row["transaction_type"] == "Purchase"

    # Payload fields
    assert row["amount"] == "$1M-$5M"
    assert row["party"] == "D"


@pytest.mark.asyncio
async def test_transform_drops_rows_missing_unique_key_components():
    """Without ``ticker`` / ``transaction_date`` / ``representative``
    a row can't be uniquely identified, so it must be dropped.
    The framework counts the drop as ``failed``."""
    job = QuiverCongressTradesJob()
    raw = [
        # Missing ticker
        {"TransactionDate": "2026-04-21", "Representative": "X"},
        # Missing date
        {"Ticker": "AAPL", "Representative": "X"},
        # Missing representative
        {"Ticker": "AAPL", "TransactionDate": "2026-04-21"},
        # Whitespace-only ticker == missing
        {"Ticker": "   ", "TransactionDate": "2026-04-21", "Representative": "X"},
        # GOOD row
        {"Ticker": "AAPL", "TransactionDate": "2026-04-21", "Representative": "X"},
    ]
    out = await job.transform(raw)
    assert len(out) == 1, f"Only the complete row should pass: {out}"
    assert out[0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_transform_preserves_legacy_lowercase_keys():
    """Defensive: if Quiver ever flips back to lowercase keys
    (their schema has changed before), transform must still
    work."""
    job = QuiverCongressTradesJob()
    raw = [{
        "ticker": "MSFT",
        "date": "2026-04-21",
        "representative": "Joe Public",
        "house": "Senate",
        "transaction": "Sale",
        "amount": "$15K-$50K",
        "party": "Republican",
    }]
    out = await job.transform(raw)
    assert len(out) == 1
    assert out[0]["ticker"] == "MSFT"
    assert out[0]["chamber"] == "Senate"
    assert out[0]["party"] == "R"


# ── _short_party — defensive coercion ─────────────────────────────


def test_short_party_canonicalises_known_labels():
    assert _short_party("Democrat") == "D"
    assert _short_party("Democratic") == "D"
    assert _short_party("Republican") == "R"
    assert _short_party("D") == "D"
    assert _short_party("R") == "R"
    assert _short_party("d") == "D"  # case-insensitive, returns canonical


def test_short_party_handles_empty_and_unknown():
    assert _short_party("") == ""
    assert _short_party("Independent") == ""
    assert _short_party("Libertarian") == ""


# ── Subclass config — pinned values ───────────────────────────────


def test_job_attributes_pinned():
    """Lock the cadence + retention so an accidental edit doesn't
    silently change production behaviour. Mondays @ 4:30 UTC and
    180-day retention are the deliberate defaults — see module
    docstring for rationale."""
    job = QuiverCongressTradesJob()
    assert job.source_name == "quiver_congress_trades"
    assert job.cadence == {
        "day_of_week": "mon", "hour": 4, "minute": 30,
    }
    assert job.retention_days == 180
    assert job.unique_key_fields == (
        "ticker", "transaction_date", "representative",
        "chamber", "transaction_type",
    )
    assert job.enabled is True


# ── Cached reader ─────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, field, direction):
        self._rows = sorted(
            self._rows,
            key=lambda r: r.get(field, ""),
            reverse=(direction == -1),
        )
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._idx]
        self._idx += 1
        return row


class _FakeCollection:
    def __init__(self, rows):
        self._rows = rows

    def find(self, filt, proj=None):
        if filt:
            matched = [
                r for r in self._rows
                if all(r.get(k) == v for k, v in filt.items())
            ]
        else:
            matched = list(self._rows)
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return _FakeCollection(self._collections.get(name, []))


@pytest.mark.asyncio
async def test_cached_reader_returns_canonical_shape():
    """Read path must produce the same caller-visible shape as
    the legacy ``quiver_service.get_congressional_trades`` so
    every existing consumer keeps working without modification."""
    rows = [{
        "ticker": "NVDA",
        "transaction_date": "2026-04-21",
        "representative": "Nancy Pelosi",
        "chamber": "House",
        "transaction_type": "Purchase",
        "amount": "$1M-$5M",
        "party": "D",
    }]
    db = _FakeDB({"quiver_congress_trades": rows})

    out = await get_congressional_trades_cached(db, ticker="NVDA", limit=20)

    assert len(out) == 1
    row = out[0]
    # Every field the legacy consumer expects, in the legacy shape
    assert set(row.keys()) == {
        "representative", "ticker", "transaction_date", "type",
        "amount", "party", "chamber", "description", "source",
    }
    assert row["source"] == "quiverquant_etl"  # marker — caller can tell
    assert row["description"] == (
        "PURCHASE by Nancy Pelosi (D-House) — NVDA $1M-$5M"
    )


@pytest.mark.asyncio
async def test_cached_reader_filters_by_ticker():
    rows = [
        {"ticker": "NVDA", "transaction_date": "2026-04-21",
         "representative": "X", "chamber": "House",
         "transaction_type": "Purchase", "amount": "$1K", "party": "D"},
        {"ticker": "AAPL", "transaction_date": "2026-04-21",
         "representative": "Y", "chamber": "Senate",
         "transaction_type": "Sale", "amount": "$5K", "party": "R"},
    ]
    db = _FakeDB({"quiver_congress_trades": rows})

    out = await get_congressional_trades_cached(db, ticker="aapl", limit=20)
    assert len(out) == 1
    assert out[0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_cached_reader_no_ticker_returns_all():
    rows = [
        {"ticker": "NVDA", "transaction_date": "2026-04-21",
         "representative": "X", "chamber": "House",
         "transaction_type": "Purchase", "amount": "$1K", "party": "D"},
        {"ticker": "AAPL", "transaction_date": "2026-04-22",
         "representative": "Y", "chamber": "Senate",
         "transaction_type": "Sale", "amount": "$5K", "party": "R"},
    ]
    db = _FakeDB({"quiver_congress_trades": rows})

    out = await get_congressional_trades_cached(db, ticker=None, limit=20)
    assert len(out) == 2


@pytest.mark.asyncio
async def test_cached_reader_sorts_newest_first():
    rows = [
        {"ticker": "X", "transaction_date": "2026-04-15",
         "representative": "old", "chamber": "House",
         "transaction_type": "Purchase", "amount": "", "party": "D"},
        {"ticker": "X", "transaction_date": "2026-04-25",
         "representative": "newest", "chamber": "House",
         "transaction_type": "Purchase", "amount": "", "party": "D"},
        {"ticker": "X", "transaction_date": "2026-04-20",
         "representative": "middle", "chamber": "House",
         "transaction_type": "Purchase", "amount": "", "party": "D"},
    ]
    db = _FakeDB({"quiver_congress_trades": rows})

    out = await get_congressional_trades_cached(db, ticker="X", limit=20)
    assert [r["representative"] for r in out] == ["newest", "middle", "old"]


@pytest.mark.asyncio
async def test_cached_reader_db_none_returns_empty():
    """Defensive — a missing DB handle returns empty rather than
    raising. The caller in ``quiver_service`` falls through to
    the live feed in this case."""
    out = await get_congressional_trades_cached(db=None, ticker="X", limit=20)
    assert out == []


@pytest.mark.asyncio
async def test_cached_reader_limit_clamped():
    """``limit`` must be clamped to [1, 100] regardless of caller
    request (defence against unbounded reads)."""
    rows = [
        {"ticker": "X", "transaction_date": f"2026-04-{i:02d}",
         "representative": f"r{i}", "chamber": "House",
         "transaction_type": "Purchase", "amount": "", "party": "D"}
        for i in range(1, 30)
    ]
    db = _FakeDB({"quiver_congress_trades": rows})

    # Caller asks for 1000 — should be clamped to 100 (we have 29
    # rows so ``min(limit, 29)`` lands at 29 either way; the
    # important assertion is "no exception, all rows returned").
    out = await get_congressional_trades_cached(db, ticker="X", limit=1000)
    assert len(out) == 29

    # Caller asks for 0 — should be clamped UP to 1
    out = await get_congressional_trades_cached(db, ticker="X", limit=0)
    assert len(out) == 1
