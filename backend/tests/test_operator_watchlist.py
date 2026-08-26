"""Tests for the Operator Watchlist parser + Mongo CRUD.

Also covers the doctrine invariants:
* Parser never returns known-non-ticker tokens
* Pennies are auto-unchecked
* Coverage report is read-only (never writes synthetic predictions)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import operator_watchlist as ow


# ── Parser ────────────────────────────────────────────────────────────


class TestParseText:
    def test_extracts_uppercase_tickers(self):
        text = "Buy BE and AAOI; NVDA rejected."
        picks = ow.parse_text(text)
        syms = {p["symbol"] for p in picks}
        assert {"AAOI", "NVDA"}.issubset(syms)

    def test_filters_common_non_tickers(self):
        text = "TLDR: USD is up. PE ratio matters. IPO buzz."
        picks = ow.parse_text(text)
        syms = {p["symbol"] for p in picks}
        # All these are in the stoplist with no nearby price:
        assert not (syms & {"TLDR", "USD", "PE", "IPO"})

    def test_detects_trigger_and_invalidation(self):
        text = "BE remains just below $217.50 with invalidation at $200.10."
        picks = ow.parse_text(text)
        be = next(p for p in picks if p["symbol"] == "BE")
        assert be["trigger"] == 217.5
        assert be["invalidation"] == 200.1

    def test_auto_uncheck_pennies(self):
        text = "SWVL at $2.07 — WATCH-ONLY. NVDA remains strong."
        picks = ow.parse_text(text)
        swvl = next(p for p in picks if p["symbol"] == "SWVL")
        nvda = next(p for p in picks if p["symbol"] == "NVDA")
        assert swvl["is_penny"] is True
        assert swvl["auto_checked"] is False
        assert nvda["is_penny"] is False
        assert nvda["auto_checked"] is True

    def test_empty_input_returns_empty(self):
        assert ow.parse_text("") == []
        assert ow.parse_text(None) == []

    def test_stoplist_token_with_price_keeps_it(self):
        # "BE" is a stoplisted common word AND a real ticker — the
        # parser keeps it only when a nearby price is present.
        picks = ow.parse_text("BE trigger $217.50")
        syms = {p["symbol"] for p in picks}
        assert "BE" in syms

    def test_dedupes_repeated_symbols(self):
        text = "NVDA NVDA NVDA at $500 and $520"
        picks = ow.parse_text(text)
        nvda = [p for p in picks if p["symbol"] == "NVDA"]
        assert len(nvda) == 1
        # Prices from all snippet windows merge.
        assert 500.0 in nvda[0]["prices"]

    def test_realistic_paste(self):
        text = (
            "Current rankings — Liquid: BE > AAOI > NVDA > MU > SNDK | "
            "Penny: BTCT > SWVL > TNMG > OFAL > AIXI. "
            "SWVL — $2.07 at 11:05 a.m. CT — WATCH-ONLY. "
            "BE remains just below $217.50; AAOI remains below $113.50."
        )
        picks = ow.parse_text(text)
        syms = {p["symbol"] for p in picks}
        for want in ("BE", "AAOI", "NVDA", "MU", "SNDK", "SWVL", "OFAL", "AIXI"):
            assert want in syms, f"expected {want} in {syms}"
        # Liquid picks are auto-checked; pennies are not.
        m = {p["symbol"]: p for p in picks}
        assert m["NVDA"]["auto_checked"] is True
        assert m["SWVL"]["auto_checked"] is False


# ── Mongo CRUD (with a tiny fake) ─────────────────────────────────────


class _FakeCursor:
    def __init__(self, docs): self._docs = list(docs)
    def sort(self, *_a, **_kw): return self
    async def to_list(self, _n): return self._docs
    def __aiter__(self):
        self._i = 0
        return self
    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _FakeColl:
    def __init__(self):
        self._docs: list[dict] = []
    async def update_one(self, filt, update, upsert=False):
        sym = filt.get("symbol")
        set_ = update.get("$set", {})
        for d in self._docs:
            if d.get("symbol") == sym:
                d.update(set_)
                return type("R", (), {"upserted_id": None})()
        if upsert:
            self._docs.append(dict(set_))
        return type("R", (), {"upserted_id": "x"})()
    async def delete_one(self, filt):
        sym = filt.get("symbol")
        before = len(self._docs)
        self._docs = [d for d in self._docs if d.get("symbol") != sym]
        return type("R", (), {"deleted_count": before - len(self._docs)})()
    def find(self, filt=None, projection=None):
        docs = self._docs
        if filt and "expires_at" in filt:
            cutoff = filt["expires_at"].get("$gt")
            if cutoff is not None:
                docs = [d for d in docs if d.get("expires_at", cutoff) > cutoff]
        if filt and "symbol" in filt and isinstance(filt["symbol"], dict):
            allowed = set(filt["symbol"].get("$in") or [])
            docs = [d for d in docs if d.get("symbol") in allowed]
        return _FakeCursor(docs)


class _FakeDB:
    def __init__(self):
        self._colls: dict[str, _FakeColl] = {}
    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())
    def __getattr__(self, name):
        return self[name]


@pytest.mark.asyncio
async def test_add_picks_upserts():
    db = _FakeDB()
    n = await ow.add_picks(
        db,
        [{"symbol": "BE", "trigger": 217.5, "note_snippet": "trigger $217.50"}],
        actor="admin@test",
    )
    assert n == 1
    docs = db[ow.WATCHLIST_COLLECTION]._docs
    assert len(docs) == 1
    assert docs[0]["symbol"] == "BE"
    assert docs[0]["trigger"] == 217.5

    # Idempotent re-add updates the same row rather than creating a
    # duplicate.
    await ow.add_picks(db, [{"symbol": "BE", "trigger": 220.0}], actor="a")
    assert len(db[ow.WATCHLIST_COLLECTION]._docs) == 1
    assert db[ow.WATCHLIST_COLLECTION]._docs[0]["trigger"] == 220.0


@pytest.mark.asyncio
async def test_list_active_filters_expired():
    db = _FakeDB()
    now = datetime.now(timezone.utc)
    db[ow.WATCHLIST_COLLECTION]._docs = [
        {"symbol": "AAA", "expires_at": now + timedelta(hours=1)},
        {"symbol": "BBB", "expires_at": now - timedelta(hours=1)},  # expired
    ]
    out = await ow.list_active(db)
    syms = {r["symbol"] for r in out}
    assert "AAA" in syms
    assert "BBB" not in syms


@pytest.mark.asyncio
async def test_coverage_report_never_writes_predictions():
    """Regression: the coverage report is a READ-ONLY join over
    ``predictions``. It must never write a synthetic row."""
    db = _FakeDB()
    now = datetime.now(timezone.utc)
    db[ow.WATCHLIST_COLLECTION]._docs = [
        {"symbol": "NVDA", "expires_at": now + timedelta(hours=1)},
    ]
    before = len(db.predictions._docs)
    out = await ow.coverage_report(db)
    after = len(db.predictions._docs)
    assert before == after == 0, "coverage_report wrote synthetic predictions"
    assert len(out) == 1
    assert out[0]["symbol"] == "NVDA"
    assert out[0]["has_prediction"] is False


@pytest.mark.asyncio
async def test_coverage_report_reads_timestamp_field():
    """Regression: prediction docs are written with ``timestamp``
    (not ``created_at``). Coverage must join on the real field name
    or every pick shows as an uncovered gap."""
    db = _FakeDB()
    now = datetime.now(timezone.utc)
    recent_iso = now.isoformat()
    db[ow.WATCHLIST_COLLECTION]._docs = [
        {"symbol": "AAPL", "expires_at": now + timedelta(hours=1)},
    ]
    db.predictions._docs = [{
        "symbol": "AAPL",
        "feature": "signal_dispatcher",
        "direction": "up",
        "confidence": 0.75,
        "timestamp": recent_iso,
    }]
    out = await ow.coverage_report(db)
    assert len(out) == 1
    assert out[0]["symbol"] == "AAPL"
    assert out[0]["has_prediction"] is True, \
        "coverage_report must find predictions stored under 'timestamp'"
    assert out[0]["prediction"]["direction"] == "up"


class TestStopwordAdmissionGate:
    """Regression: a $price near a stoplisted stopword must not
    re-admit that stopword as a ticker. Only tokens on the SOFT
    allowlist may be re-admitted when a price is nearby."""

    def test_priced_stopword_not_soft_allowed_is_filtered(self):
        # FED is stoplisted and NOT on _SOFT_ALLOWLIST — must stay out
        # even though a $price is in the same clause.
        picks = ow.parse_text("FED cut rates; SPY closed at $450.10")
        syms = {p["symbol"] for p in picks}
        assert "FED" not in syms
        assert "SPY" not in syms

    def test_priced_soft_allowlist_stopword_is_admitted(self):
        # BE is stoplisted (common English) but on _SOFT_ALLOWLIST; a
        # $price signals intent — admit.
        picks = ow.parse_text("BE target $217.50")
        syms = {p["symbol"] for p in picks}
        assert "BE" in syms
