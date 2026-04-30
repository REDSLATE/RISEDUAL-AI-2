"""Tests for the 3 secondary Quiver ETL jobs.

* ``QuiverInsidersJob`` — Form 4 trades
* ``QuiverLobbyingJob`` — corporate lobbying
* ``QuiverGovContractsJob`` — federal contracts

Each surface tests:
1. ``transform()`` normalises mixed-case Quiver keys
2. Rows missing unique-key components are dropped
3. Subclass attributes are pinned
4. Cached reader returns the canonical caller-visible shape
"""
from __future__ import annotations

import pytest


# ── Shared fake DB ─────────────────────────────────────────────────


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
        r = self._rows[self._idx]
        self._idx += 1
        return r


class _FakeColl:
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
        return _FakeColl(self._collections.get(name, []))


# ── Insiders ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insiders_transform_normalises_payload():
    from services.etl_jobs.quiver_insiders import QuiverInsidersJob
    job = QuiverInsidersJob()
    out = await job.transform([{
        "Ticker": "nvda",
        "Date": "2026-04-21T00:00:00Z",
        "Name": "  Jensen Huang  ",
        "Title": "CEO",
        "Transaction": "Sale",
        "Shares": "10000",
        "Price": "950.00",
    }])
    assert len(out) == 1
    row = out[0]
    assert row["ticker"] == "NVDA"
    assert row["filed_date"] == "2026-04-21"
    assert row["insider_name"] == "Jensen Huang"
    assert row["trade_type"] == "Sale"
    assert row["qty"] == "10000"
    assert row["insider_title"] == "CEO"
    assert row["price"] == "950.00"


@pytest.mark.asyncio
async def test_insiders_drops_rows_missing_unique_key():
    from services.etl_jobs.quiver_insiders import QuiverInsidersJob
    job = QuiverInsidersJob()
    out = await job.transform([
        {"Date": "2026-04-21", "Name": "X"},  # missing ticker
        {"Ticker": "AAPL", "Name": "X"},      # missing date
        {"Ticker": "AAPL", "Date": "2026-04-21"},  # missing name
        {"Ticker": "AAPL", "Date": "2026-04-21", "Name": "X"},  # GOOD
    ])
    assert len(out) == 1
    assert out[0]["ticker"] == "AAPL"


def test_insiders_job_attributes_pinned():
    from services.etl_jobs.quiver_insiders import QuiverInsidersJob
    job = QuiverInsidersJob()
    assert job.source_name == "quiver_insiders"
    assert job.cadence == {"day_of_week": "mon", "hour": 4, "minute": 35}
    assert job.retention_days == 180
    assert job.unique_key_fields == (
        "ticker", "filed_date", "insider_name", "trade_type", "qty",
    )


@pytest.mark.asyncio
async def test_insiders_cached_reader_canonical_shape():
    from services.etl_jobs.quiver_insiders import get_insider_trades_cached
    db = _FakeDB({"quiver_insiders": [{
        "ticker": "NVDA", "filed_date": "2026-04-21",
        "insider_name": "Jensen Huang", "insider_title": "CEO",
        "trade_type": "Sale", "qty": "10000", "price": "950.00",
    }]})
    out = await get_insider_trades_cached(db, ticker="NVDA")
    assert len(out) == 1
    row = out[0]
    assert set(row.keys()) == {
        "form_type", "filed_date", "ticker", "insider_name",
        "insider_title", "trade_type", "price", "qty",
        "description", "source",
    }
    assert row["form_type"] == "Form 4"
    assert row["source"] == "quiverquant_etl"
    assert row["description"] == (
        "Insider Sale by Jensen Huang (CEO) — NVDA 10000 shares @ $950.00"
    )


# ── Lobbying ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lobbying_transform_normalises_payload():
    from services.etl_jobs.quiver_lobbying import QuiverLobbyingJob
    job = QuiverLobbyingJob()
    out = await job.transform([{
        "Ticker": "msft",
        "Date": "2026-04-15",
        "Client": "Microsoft Corp",
        "Issue": "Cybersecurity",
        "Amount": 250000,
    }])
    assert len(out) == 1
    row = out[0]
    assert row["ticker"] == "MSFT"
    assert row["date"] == "2026-04-15"
    assert row["client"] == "Microsoft Corp"
    assert row["issue"] == "Cybersecurity"
    assert row["amount"] == 250000.0


@pytest.mark.asyncio
async def test_lobbying_amount_coerces_string_to_float():
    """Quiver sometimes returns ``Amount`` as a string. Transform
    must coerce or default to 0 — never raise."""
    from services.etl_jobs.quiver_lobbying import QuiverLobbyingJob
    job = QuiverLobbyingJob()
    out = await job.transform([
        {"Ticker": "X", "Date": "2026-01-01", "Client": "C", "Issue": "I",
         "Amount": "150000.50"},
        {"Ticker": "Y", "Date": "2026-01-02", "Client": "C", "Issue": "I",
         "Amount": "not a number"},
        {"Ticker": "Z", "Date": "2026-01-03", "Client": "C", "Issue": "I"},
    ])
    assert out[0]["amount"] == 150000.5
    assert out[1]["amount"] == 0.0
    assert out[2]["amount"] == 0.0


def test_lobbying_job_attributes_pinned():
    from services.etl_jobs.quiver_lobbying import QuiverLobbyingJob
    job = QuiverLobbyingJob()
    assert job.source_name == "quiver_lobbying"
    assert job.cadence == {"day_of_week": "mon", "hour": 4, "minute": 40}
    assert job.unique_key_fields == ("ticker", "date", "client", "issue")


@pytest.mark.asyncio
async def test_lobbying_cached_reader_canonical_shape():
    from services.etl_jobs.quiver_lobbying import get_lobbying_cached
    db = _FakeDB({"quiver_lobbying": [{
        "ticker": "MSFT", "date": "2026-04-15",
        "client": "Microsoft Corp", "issue": "Cybersecurity",
        "amount": 250000.0,
    }]})
    out = await get_lobbying_cached(db, ticker="MSFT")
    assert len(out) == 1
    row = out[0]
    assert set(row.keys()) == {
        "ticker", "client", "amount", "issue", "date",
        "description", "source",
    }
    assert row["source"] == "quiverquant_etl"
    assert row["description"] == "Microsoft Corp lobbied $250,000 on Cybersecurity"


# ── Gov Contracts ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gov_contracts_transform_normalises_payload():
    from services.etl_jobs.quiver_gov_contracts import QuiverGovContractsJob
    job = QuiverGovContractsJob()
    out = await job.transform([{
        "Ticker": "lmt",
        "Date": "2026-04-10",
        "Agency": "DEPT OF DEFENSE",
        "Amount": 50000000.0,
        "Description": "F-35 spare parts replenishment contract",
    }])
    assert len(out) == 1
    row = out[0]
    assert row["ticker"] == "LMT"
    assert row["date"] == "2026-04-10"
    assert row["agency"] == "DEPT OF DEFENSE"
    assert row["amount"] == 50000000.0
    assert "F-35" in row["description"]


@pytest.mark.asyncio
async def test_gov_contracts_clips_long_descriptions():
    """Defence against a freak 10kB description bloating the cache."""
    from services.etl_jobs.quiver_gov_contracts import QuiverGovContractsJob
    job = QuiverGovContractsJob()
    out = await job.transform([{
        "Ticker": "X", "Date": "2026-04-10", "Agency": "A",
        "Amount": 100,
        "Description": "x" * 10_000,
    }])
    assert len(out) == 1
    assert len(out[0]["description"]) == 200


@pytest.mark.asyncio
async def test_gov_contracts_drops_rows_missing_ticker_or_date():
    from services.etl_jobs.quiver_gov_contracts import QuiverGovContractsJob
    job = QuiverGovContractsJob()
    out = await job.transform([
        {"Date": "2026-04-10", "Agency": "A", "Amount": 1},     # no ticker
        {"Ticker": "X", "Agency": "A", "Amount": 1},            # no date
        {"Ticker": "X", "Date": "2026-04-10", "Agency": "A", "Amount": 1},  # GOOD
    ])
    assert len(out) == 1
    assert out[0]["ticker"] == "X"


def test_gov_contracts_job_attributes_pinned():
    from services.etl_jobs.quiver_gov_contracts import QuiverGovContractsJob
    job = QuiverGovContractsJob()
    assert job.source_name == "quiver_gov_contracts"
    assert job.cadence == {"day_of_week": "mon", "hour": 4, "minute": 45}
    assert job.unique_key_fields == ("ticker", "date", "agency", "amount")


@pytest.mark.asyncio
async def test_gov_contracts_cached_reader_canonical_shape():
    from services.etl_jobs.quiver_gov_contracts import get_gov_contracts_cached
    db = _FakeDB({"quiver_gov_contracts": [{
        "ticker": "LMT", "date": "2026-04-10",
        "agency": "DEPT OF DEFENSE", "amount": 50000000.0,
        "description": "F-35 contract",
    }]})
    out = await get_gov_contracts_cached(db, ticker="LMT")
    assert len(out) == 1
    row = out[0]
    assert set(row.keys()) == {
        "ticker", "agency", "amount", "description", "date", "source",
    }
    assert row["source"] == "quiverquant_etl"
    assert row["amount"] == 50000000.0


# ── Cross-cutting: all 3 jobs registered and discoverable ──────────


def test_all_three_secondary_jobs_registered():
    """Importing the package must register all four Quiver ETL
    jobs (congresstrading + insiders + lobbying + gov_contracts).
    Verifies the ``__init__.py`` import-for-side-effects pattern
    didn't drift.

    Note: ``test_etl_registry`` has an autouse fixture that wipes
    the registry between cases. To verify our registrations from
    a clean slate, we re-import the modules here so the
    ``@register_etl_job`` decorators fire fresh.
    """
    import importlib
    from services.etl_registry import _reset_registry_for_tests, get_job
    _reset_registry_for_tests()
    from services.etl_jobs import (
        quiver_congress_trades, quiver_insiders,
        quiver_lobbying, quiver_gov_contracts,
    )
    importlib.reload(quiver_congress_trades)
    importlib.reload(quiver_insiders)
    importlib.reload(quiver_lobbying)
    importlib.reload(quiver_gov_contracts)

    for name in ("quiver_congress_trades", "quiver_insiders",
                 "quiver_lobbying", "quiver_gov_contracts"):
        assert get_job(name) is not None, (
            f"ETL job '{name}' not registered — check "
            f"services/etl_jobs/__init__.py imports."
        )
