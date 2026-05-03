"""
Tests for `services.kraken_equity_shadow_service`.

Pins Phase-0 invariants of the Kraken xStock market-data shadow lane:

1. Universe loader honours top-N + S&P500 toggle.
2. AssetPairs resolution finds the canonical pair via fast-path,
   altname match, and base/quote/aclass match — and returns None for
   absent symbols.
3. Pair metadata cache: fresh rows skip upstream; stale rows refresh;
   "not_listed" sentinel persists so we don't refetch every tick.
4. Ticker row parser handles malformed payloads, computes mid/spread
   bps correctly, and refuses zero-mid rows.
5. Divergence math is deterministic and bounded against zero/missing
   Alpaca quotes.
6. ``run_kraken_shadow_compare_once`` is a no-op when disabled or the
   db handle is None — never raises.
7. End-to-end tick (enabled, fake http_get + alpaca stubs, fake db)
   writes the expected number of comparison rows and tags each with
   the active market_session.
8. ``summarize_today`` returns a stable shape with both empty-day and
   populated-day fixtures, including correct p95/max bps, divergent
   count vs the alert threshold, and session_counts.
9. Slack alert is only fired during ``regular`` session AND when bps
   >= threshold. Pre/post/closed sessions never alert even at high
   divergence (xStocks legitimately diverge against staled Alpaca
   close in off-hours).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest


# ── In-memory async Mongo stub (re-used pattern) ────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:  # noqa: PERF203
            raise StopAsyncIteration


class _FakeCollection:
    def __init__(self):
        self._docs: list[dict] = []
        self._next_id = 1

    async def insert_one(self, doc):
        doc.setdefault("_id", self._next_id)
        self._next_id += 1
        self._docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    async def find_one(self, query, projection=None):  # noqa: ARG002
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                out = dict(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    async def update_one(self, query, update, upsert=False):
        set_ = update.get("$set") or {}
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                for k, v in set_.items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "upserted_id": None})()
        if upsert:
            new_doc = {**query, **set_}
            await self.insert_one(new_doc)
            return type("R", (), {"modified_count": 0, "upserted_id": new_doc.get("_id")})()
        return type("R", (), {"modified_count": 0, "upserted_id": None})()

    def find(self, query, projection=None):  # noqa: ARG002
        # Support {field: {"$in": [...]}} and {field: {"$gte": ...}}
        rows = []
        for d in self._docs:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict):
                    if "$in" in v and d.get(k) not in v["$in"]:
                        ok = False
                        break
                    if "$gte" in v and not (d.get(k) is not None and d.get(k) >= v["$gte"]):
                        ok = False
                        break
                else:
                    if d.get(k) != v:
                        ok = False
                        break
            if ok:
                rows.append(d)
        return _FakeCursor(rows)

    async def create_index(self, *args, **kwargs):  # noqa: ARG002
        return "idx_ok"


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]


@pytest.fixture
def db():
    return _FakeDB()


# ── Universe ────────────────────────────────────────────────────────


def test_universe_top_n_only(monkeypatch):
    from services import kraken_equity_shadow_service as svc

    monkeypatch.setenv(svc.ENV_TOP_N, "5")
    monkeypatch.setenv(svc.ENV_INCLUDE_SP500, "false")

    out = svc.load_universe()
    # First 5 from sp500_constituents.json: AAPL, MSFT, NVDA, AMZN, GOOGL.
    assert out[:5] == ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]
    assert len(out) == 5


def test_universe_includes_sp500(monkeypatch):
    from services import kraken_equity_shadow_service as svc

    monkeypatch.setenv(svc.ENV_TOP_N, "20")
    monkeypatch.setenv(svc.ENV_INCLUDE_SP500, "true")

    out = svc.load_universe()
    # Full S&P500 seed list is ~500 tickers.
    assert len(out) > 100
    assert "AAPL" in out
    assert "MSFT" in out


# ── Ticker parser ──────────────────────────────────────────────────


def test_parse_ticker_row_normal_case():
    from services.kraken_equity_shadow_service import parse_ticker_row

    raw = {
        "a": ["150.25", "100", "100"],
        "b": ["150.20", "200", "200"],
        "c": ["150.22", "10"],
        "v": ["1000", "150220"],
        "p": ["150.21", "150.21"],
        "t": [50, 100],
        "l": ["149.50", "149.50"],
        "h": ["151.00", "151.00"],
        "o": "150.00",
    }
    parsed = parse_ticker_row(raw)
    assert parsed is not None
    assert parsed["ask"] == 150.25
    assert parsed["bid"] == 150.20
    assert parsed["last"] == 150.22
    # mid = (150.25 + 150.20)/2 = 150.225
    assert abs(parsed["mid"] - 150.225) < 0.01
    # spread = 0.05 / 150.225 * 10_000 ≈ 3.33 bps
    assert abs(parsed["spread_bps"] - 3.33) < 0.1


def test_parse_ticker_row_malformed_returns_none():
    from services.kraken_equity_shadow_service import parse_ticker_row

    assert parse_ticker_row(None) is None
    assert parse_ticker_row({}) is None
    assert parse_ticker_row({"a": ["x"]}) is None  # missing b/c
    # Zero ask/bid AND zero last → mid <=0 → None
    assert parse_ticker_row(
        {"a": ["0", "0", "0"], "b": ["0", "0", "0"], "c": ["0", "0"]}
    ) is None


# ── Divergence math ────────────────────────────────────────────────


def test_compute_divergence_bps():
    from services.kraken_equity_shadow_service import compute_divergence_bps

    # 1% upward Kraken vs Alpaca = 100 bps
    assert abs(compute_divergence_bps(100.0, 101.0) - 100.0) < 0.01
    # Symmetric: downward 1% also 100 bps
    assert abs(compute_divergence_bps(100.0, 99.0) - 100.0) < 0.01
    # Zero alpaca → 0 bps (no division)
    assert compute_divergence_bps(0.0, 100.0) == 0.0


# ── Market session ─────────────────────────────────────────────────


def test_market_session_classification():
    from services.kraken_equity_shadow_service import market_session

    # Saturday 10:30 ET = closed
    sat = datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc)  # Sat 9:30 ET
    assert market_session(sat) == "closed"

    # Holiday — Independence Day observed July 3 2026 (Friday) 10:00 ET
    holiday = datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc)
    assert market_session(holiday) == "closed"

    # Weekday RTH — Tuesday 11:00 ET = 16:00 UTC
    rth = datetime(2026, 5, 12, 16, 0, tzinfo=timezone.utc)
    assert market_session(rth) == "regular"

    # Weekday pre-market — 5:00 ET = 10:00 UTC
    pre = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    assert market_session(pre) == "pre"

    # Weekday post-market — 5:00 PM ET = 22:00 UTC
    post = datetime(2026, 5, 12, 22, 0, tzinfo=timezone.utc)
    assert market_session(post) == "post"


# ── Pair resolution ────────────────────────────────────────────────


def test_resolve_pair_fast_path():
    from services.kraken_equity_shadow_service import _resolve_pair_from_assetpairs

    payload = {
        "AAPLUSD": {
            "altname": "AAPL/USD",
            "wsname": "AAPL/USD",
            "base": "AAPL",
            "quote": "USD",
            "aclass_base": "tokenized_asset",
            "status": "online",
        },
    }
    out = _resolve_pair_from_assetpairs("AAPL", payload)
    assert out is not None
    assert out["kraken_pair"] == "AAPLUSD"
    assert out["canonical_symbol"] == "AAPL"


def test_resolve_pair_altname_fallback():
    from services.kraken_equity_shadow_service import _resolve_pair_from_assetpairs

    payload = {
        "WEIRDPAIR123": {
            "altname": "MSFT/USD",
            "base": "MSFT",
            "quote": "USD",
            "aclass_base": "tokenized_asset",
            "status": "online",
        },
    }
    out = _resolve_pair_from_assetpairs("MSFT", payload)
    assert out is not None
    assert out["kraken_pair"] == "WEIRDPAIR123"


def test_resolve_pair_returns_none_when_absent():
    from services.kraken_equity_shadow_service import _resolve_pair_from_assetpairs

    assert _resolve_pair_from_assetpairs("ZZZZ", {}) is None
    assert _resolve_pair_from_assetpairs("ZZZZ", {"AAPLUSD": {"base": "AAPL"}}) is None


# ── Pair metadata cache ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_pair_metadata_uses_cache_when_fresh(db, monkeypatch):
    from services import kraken_equity_shadow_service as svc

    monkeypatch.setenv(svc.ENV_PAIR_CACHE_TTL, "24")
    now = datetime.now(timezone.utc)
    # Pre-seed a fresh cache row.
    await db[svc.PAIR_METADATA_COLL].insert_one({
        "canonical_symbol": "AAPL",
        "kraken_pair": "AAPLUSD",
        "status": "online",
        "last_seen": now - timedelta(hours=1),
    })

    calls = []

    async def fake_http_get(url, params=None):
        calls.append((url, params))
        return {"result": {}}

    out = await svc.discover_pair_metadata(
        db, ["AAPL"], http_get=fake_http_get, now=now,
    )
    assert "AAPL" in out
    assert out["AAPL"]["kraken_pair"] == "AAPLUSD"
    # Fresh cache → no upstream call.
    assert len(calls) == 0


@pytest.mark.asyncio
async def test_discover_pair_metadata_persists_not_listed(db):
    from services import kraken_equity_shadow_service as svc

    async def fake_http_get(url, params=None):  # noqa: ARG001
        return {"result": {}}  # AssetPairs returns nothing for our symbol

    now = datetime.now(timezone.utc)
    out = await svc.discover_pair_metadata(
        db, ["NOTAREAL"], http_get=fake_http_get, now=now,
    )
    assert out["NOTAREAL"]["status"] == "not_listed"
    persisted = await db[svc.PAIR_METADATA_COLL].find_one(
        {"canonical_symbol": "NOTAREAL"},
    )
    assert persisted["status"] == "not_listed"


# ── Disabled / no-db guards ────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_disabled_short_circuits(db, monkeypatch):
    from services import kraken_equity_shadow_service as svc

    monkeypatch.delenv(svc.ENV_ENABLED, raising=False)
    out = await svc.run_kraken_shadow_compare_once(db)
    assert out == {"ok": False, "reason": "disabled"}


@pytest.mark.asyncio
async def test_run_no_db_short_circuits(monkeypatch):
    from services import kraken_equity_shadow_service as svc

    monkeypatch.setenv(svc.ENV_ENABLED, "1")
    out = await svc.run_kraken_shadow_compare_once(None)
    assert out == {"ok": False, "reason": "no_db"}


# ── End-to-end tick ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_end_to_end_writes_compare_rows(db, monkeypatch):
    from services import kraken_equity_shadow_service as svc

    monkeypatch.setenv(svc.ENV_ENABLED, "1")
    monkeypatch.setenv(svc.ENV_TOP_N, "3")
    monkeypatch.setenv(svc.ENV_INCLUDE_SP500, "false")
    monkeypatch.setenv(svc.ENV_BATCH_SIZE, "10")
    monkeypatch.setenv(svc.ENV_BATCH_DELAY, "0")

    # Stub Kraken HTTP — both AssetPairs and Ticker.
    async def fake_http_get(url, params=None):
        if "AssetPairs" in url:
            return {
                "result": {
                    "AAPLUSD": {"altname": "AAPL/USD", "base": "AAPL",
                                "quote": "USD", "aclass_base": "tokenized_asset",
                                "status": "online"},
                    "MSFTUSD": {"altname": "MSFT/USD", "base": "MSFT",
                                "quote": "USD", "aclass_base": "tokenized_asset",
                                "status": "online"},
                    "NVDAUSD": {"altname": "NVDA/USD", "base": "NVDA",
                                "quote": "USD", "aclass_base": "tokenized_asset",
                                "status": "online"},
                }
            }
        if "Ticker" in url:
            return {
                "result": {
                    "AAPLUSD": {
                        "a": ["150.10", "1", "1"], "b": ["150.00", "1", "1"],
                        "c": ["150.05", "1"],
                    },
                    "MSFTUSD": {
                        "a": ["400.10", "1", "1"], "b": ["400.00", "1", "1"],
                        "c": ["400.05", "1"],
                    },
                    "NVDAUSD": {
                        "a": ["900.10", "1", "1"], "b": ["900.00", "1", "1"],
                        "c": ["900.05", "1"],
                    },
                }
            }
        return {}

    # Stub Alpaca quote provider — return a clean mid that's exactly
    # 50 bps below Kraken's mid for AAPL (under default 50 threshold ⇒ no alert),
    # 200 bps off for MSFT (above ⇒ would alert if regular session),
    # exact match for NVDA.
    async def fake_alpaca(symbol):
        return {
            "AAPL": {"price": 149.325},  # ~50 bps below 150.05 mid
            "MSFT": {"price": 392.0},    # ~200 bps below 400.05 mid
            "NVDA": {"price": 900.05},   # 0 bps
        }.get(symbol)

    # Force "regular" session (Tuesday 11 ET = 16:00 UTC).
    fixed_now = datetime(2026, 5, 12, 16, 0, tzinfo=timezone.utc)

    # Suppress real Slack so test stays hermetic.
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    out = await svc.run_kraken_shadow_compare_once(
        db,
        http_get=fake_http_get,
        alpaca_quote_fn=fake_alpaca,
        now=fixed_now,
    )

    assert out["ok"] is True
    assert out["session"] == "regular"
    assert out["rows_written"] == 3
    docs = db[svc.COMPARE_COLL]._docs
    assert len(docs) == 3

    by_symbol = {d["symbol"]: d for d in docs}
    # Sanity-check divergences.
    assert by_symbol["NVDA"]["divergence_bps"] == 0.0
    assert 30 < by_symbol["AAPL"]["divergence_bps"] < 70
    assert by_symbol["MSFT"]["divergence_bps"] > 100
    # No webhook configured → MSFT alert wasn't fired even though
    # divergence is above threshold.
    assert all(d["alert_fired"] is False for d in docs)


@pytest.mark.asyncio
async def test_run_skips_zero_alpaca_quotes(db, monkeypatch):
    from services import kraken_equity_shadow_service as svc

    monkeypatch.setenv(svc.ENV_ENABLED, "1")
    monkeypatch.setenv(svc.ENV_TOP_N, "1")
    monkeypatch.setenv(svc.ENV_INCLUDE_SP500, "false")
    monkeypatch.setenv(svc.ENV_BATCH_DELAY, "0")

    async def fake_http_get(url, params=None):  # noqa: ARG001
        if "AssetPairs" in url:
            return {"result": {"AAPLUSD": {
                "altname": "AAPL/USD", "base": "AAPL",
                "quote": "USD", "aclass_base": "tokenized_asset",
                "status": "online",
            }}}
        return {"result": {"AAPLUSD": {
            "a": ["100", "1", "1"], "b": ["99.9", "1", "1"], "c": ["99.95", "1"],
        }}}

    async def fake_alpaca(_symbol):
        return {"price": 0}  # AV down / yfinance 0 — must be skipped

    out = await svc.run_kraken_shadow_compare_once(
        db,
        http_get=fake_http_get,
        alpaca_quote_fn=fake_alpaca,
        now=datetime(2026, 5, 12, 16, 0, tzinfo=timezone.utc),
    )
    assert out["ok"] is True
    assert out["rows_written"] == 0


# ── summarize_today ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summarize_today_empty(db):
    from services import kraken_equity_shadow_service as svc

    out = await svc.summarize_today(db)
    assert out["rows"] == 0
    assert out["max_bps"] == 0.0
    assert out["divergent_count"] == 0


@pytest.mark.asyncio
async def test_summarize_today_aggregates(db, monkeypatch):
    from services import kraken_equity_shadow_service as svc

    monkeypatch.setenv(svc.ENV_BPS_ALERT, "50")
    now = datetime.now(timezone.utc)
    # Seed three rows with varied bps.
    for bps, sym, sess, alerted in [
        (10.0, "AAPL", "regular", False),
        (75.0, "MSFT", "regular", True),
        (200.0, "NVDA", "post", False),
    ]:
        await db[svc.COMPARE_COLL].insert_one({
            "symbol": sym,
            "kraken_pair": f"{sym}USD",
            "kraken_mid": 100.0,
            "kraken_spread_bps": 1.0,
            "alpaca_mid": 99.0,
            "divergence_bps": bps,
            "market_session": sess,
            "fetched_at": now,
            "alert_fired": alerted,
        })

    out = await svc.summarize_today(db)
    assert out["rows"] == 3
    assert out["max_bps"] == 200.0
    # Divergent count: bps >= 50 → MSFT (75) and NVDA (200) → 2
    assert out["divergent_count"] == 2
    assert out["alerts_fired"] == 1
    assert out["session_counts"] == {"regular": 2, "post": 1}
    assert len(out["sample"]) == 3


# ── ensure_indexes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_resilient(db):
    from services.kraken_equity_shadow_service import ensure_indexes

    out = await ensure_indexes(db)
    assert out["status"] == "ok"

    out_none = await ensure_indexes(None)
    assert out_none["status"] == "no_db"
