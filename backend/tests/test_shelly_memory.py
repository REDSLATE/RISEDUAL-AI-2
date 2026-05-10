"""Shelly Memory v0 — toxic-spike-prevention tests.

Pin every invariant in the operator audit (2026-05-10):
1. Auto-stamped fields on every write.
2. Mongo durable, Chroma best-effort.
3. ``_normalize_event_date`` covers every input shape; bad input
   raises ``ValueError``.
4. Past dates → ``regime_status="legacy"``.
5. ``event_date_ordinal`` is monotonic + matches the date.
6. ``recall(min_event_date=...)`` filters via the ordinal field, not
   the string field (Chroma toxic-spike landmine).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from services import shelly_memory as sm


# ── In-memory Mongo stub ────────────────────────────────────────────


class _Cursor:
    def __init__(self, items):
        self._items = list(items)

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._items = self._items[: int(n)]
        return self

    def __aiter__(self):
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration  # noqa: B904


class _Coll:
    def __init__(self):
        self._docs: list[dict] = []

    async def insert_one(self, doc):
        # Mimic Mongo: stamp _id, mutate input dict.
        doc["_id"] = f"oid-{len(self._docs)}"
        self._docs.append(dict(doc))
        return MagicMock(inserted_id=doc["_id"])

    def find(self, q, _proj=None):
        rows = [dict(r) for r in self._docs if _matches(r, q)]
        for r in rows:
            r.pop("_id", None)
        return _Cursor(rows)

    async def count_documents(self, q):
        return sum(1 for r in self._docs if _matches(r, q))


def _matches(doc: dict, q: dict) -> bool:
    """Tiny query matcher — supports our exact filter shape."""
    if not q:
        return True
    for k, v in q.items():
        if k.startswith("metadata."):
            field = k.split(".", 1)[1]
            actual = (doc.get("metadata") or {}).get(field)
        else:
            actual = doc.get(k)
        if isinstance(v, dict):
            for op, opv in v.items():
                if op == "$gte" and not (actual is not None and actual >= opv):
                    return False
                if op == "$lte" and not (actual is not None and actual <= opv):
                    return False
        else:
            if actual != v:
                return False
    return True


class _DB:
    def __init__(self):
        self._cols: dict[str, _Coll] = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _Coll()
        return self._cols[name]


@pytest.fixture(autouse=True)
def _disable_chroma(monkeypatch):
    """Tests run without a real Chroma server. Stub the client
    factory to return None so the best-effort path is exercised."""
    monkeypatch.setattr(sm, "_get_chroma_collection", lambda: None)
    yield


# ── 1. _normalize_event_date — the boundary ────────────────────────


def test_normalize_date_only_string_passthrough():
    assert sm._normalize_event_date("2024-03-15") == "2024-03-15"


def test_normalize_full_iso_with_timezone():
    assert sm._normalize_event_date("2022-05-09T14:30:00+00:00") == "2022-05-09"


def test_normalize_full_iso_with_z_suffix():
    assert sm._normalize_event_date("2022-05-09T14:30:00Z") == "2022-05-09"


def test_normalize_full_iso_non_utc_zone_coerces_to_utc_date():
    # 23:30 UTC-05:00 = 04:30 UTC the next day
    out = sm._normalize_event_date("2024-03-15T23:30:00-05:00")
    assert out == "2024-03-16"


def test_normalize_naive_datetime_assumes_utc():
    naive = datetime(2024, 3, 15, 14, 30, 0)
    assert sm._normalize_event_date(naive) == "2024-03-15"


def test_normalize_aware_datetime():
    aware = datetime(2024, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
    assert sm._normalize_event_date(aware) == "2024-03-15"


def test_normalize_date_object():
    assert sm._normalize_event_date(date(2024, 3, 15)) == "2024-03-15"


def test_normalize_empty_or_none_returns_today():
    today = datetime.now(timezone.utc).date().isoformat()
    assert sm._normalize_event_date(None) == today
    assert sm._normalize_event_date("") == today


def test_normalize_garbage_raises_value_error():
    with pytest.raises(ValueError, match="unparseable event_date"):
        sm._normalize_event_date("not-a-date")
    with pytest.raises(ValueError):
        sm._normalize_event_date(12345)
    with pytest.raises(ValueError):
        sm._normalize_event_date("2024-13-99")  # invalid month


def test_normalize_output_is_always_10_chars():
    for raw in [
        "2024-03-15",
        "2022-05-09T14:30:00+00:00",
        "2022-05-09T14:30:00Z",
        date(2024, 1, 1),
        datetime(2024, 1, 1, tzinfo=timezone.utc),
    ]:
        out = sm._normalize_event_date(raw)
        assert len(out) == 10


# ── 2. _stamp_regime — the labelling ────────────────────────────────


def test_stamp_regime_past_date_is_legacy():
    md = sm._stamp_regime({"event_date": "2022-05-09"})
    assert md["regime_status"] == "legacy"
    assert md["regime_label"] == "2022-05-09"
    assert md["event_date_ordinal"] == date(2022, 5, 9).toordinal()


def test_stamp_regime_today_is_active():
    today = datetime.now(timezone.utc).date().isoformat()
    md = sm._stamp_regime({"event_date": today})
    assert md["regime_status"] == "active"
    assert md["regime_label"] == today


def test_stamp_regime_no_event_date_uses_today():
    md = sm._stamp_regime({})
    today = datetime.now(timezone.utc).date().isoformat()
    assert md["event_date"] == today
    assert md["regime_status"] == "active"


def test_stamp_regime_preserves_caller_metadata():
    md = sm._stamp_regime({"event_date": "2024-03-15", "symbol": "AAPL", "lane": "equity"})
    assert md["symbol"] == "AAPL"
    assert md["lane"] == "equity"
    assert md["event_date"] == "2024-03-15"


def test_stamp_regime_does_not_mutate_input():
    inp = {"event_date": "2024-03-15"}
    out = sm._stamp_regime(inp)
    assert "event_date_ordinal" not in inp
    assert "event_date_ordinal" in out


def test_stamp_regime_normalizes_full_iso_input():
    md = sm._stamp_regime({"event_date": "2022-05-09T14:30:00+00:00"})
    assert md["event_date"] == "2022-05-09"  # normalized!
    assert md["regime_status"] == "legacy"


# ── 3. remember() — full write path ─────────────────────────────────


@pytest.mark.asyncio
async def test_remember_stamps_all_six_fields():
    db = _DB()
    out = await sm.remember(db, text="AAPL split announcement",
                            metadata={"event_date": "2024-03-15", "symbol": "AAPL"})
    assert "id" in out and len(out["id"]) > 0  # UUID4
    assert out["text"] == "AAPL split announcement"
    assert out["embedding_version"] == "minilm-l6-v2-default"
    assert out["created_at"]  # full ISO timestamp
    md = out["metadata"]
    assert md["event_date"] == "2024-03-15"
    assert md["event_date_ordinal"] == date(2024, 3, 15).toordinal()
    assert md["regime_status"] == "legacy"
    assert md["regime_label"] == "2024-03-15"
    assert md["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_remember_writes_to_mongo_first():
    db = _DB()
    await sm.remember(db, text="hello", metadata={"event_date": "2024-01-01"})
    # The durable record exists in Mongo.
    coll = db[sm.MEMORY_COLLECTION]
    assert len(coll._docs) == 1
    stored = coll._docs[0]
    assert stored["text"] == "hello"
    assert stored["metadata"]["event_date"] == "2024-01-01"


@pytest.mark.asyncio
async def test_remember_chroma_failure_does_not_block_durable_write(monkeypatch):
    """Critical doctrine — if Chroma blows up, Mongo write still
    succeeds and the call returns normally."""
    db = _DB()

    def bad_chroma():
        # Return a fake collection whose .upsert() raises.
        class _Bad:
            def upsert(self, **_kw):
                raise RuntimeError("chroma is on fire")
        return _Bad()

    monkeypatch.setattr(sm, "_get_chroma_collection", bad_chroma)
    out = await sm.remember(db, text="hello", metadata={"event_date": "2024-01-01"})
    assert out["text"] == "hello"
    assert len(db[sm.MEMORY_COLLECTION]._docs) == 1


@pytest.mark.asyncio
async def test_remember_uses_provided_memory_id():
    db = _DB()
    out = await sm.remember(db, text="x", memory_id="explicit-id-1")
    assert out["id"] == "explicit-id-1"


@pytest.mark.asyncio
async def test_remember_strips_mongo_id_from_response():
    db = _DB()
    out = await sm.remember(db, text="x")
    assert "_id" not in out


@pytest.mark.asyncio
async def test_remember_rejects_empty_text():
    db = _DB()
    with pytest.raises(ValueError, match="non-empty"):
        await sm.remember(db, text="")
    with pytest.raises(ValueError):
        await sm.remember(db, text=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_remember_propagates_bad_event_date():
    db = _DB()
    with pytest.raises(ValueError, match="unparseable event_date"):
        await sm.remember(db, text="x", metadata={"event_date": "not-a-date"})
    # Nothing was persisted.
    assert len(db[sm.MEMORY_COLLECTION]._docs) == 0


# ── 4. recall() — ordinal-filter doctrine ──────────────────────────


@pytest.mark.asyncio
async def test_recall_filters_via_ordinal_not_string():
    """Toxic-spike landmine: Chroma silently rejects ``$gte`` on
    string fields. The recall path MUST use ``event_date_ordinal``.
    Mongo can do either, but the parallel test in test_alpha_decision_log
    runs against the same code path so the ordinal contract sticks."""
    db = _DB()
    today = datetime.now(timezone.utc).date()
    days_ago = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    await sm.remember(db, text="aapl", metadata={"event_date": days_ago(2), "symbol": "AAPL"})
    await sm.remember(db, text="tsla", metadata={"event_date": days_ago(900), "symbol": "TSLA"})
    await sm.remember(db, text="nvda", metadata={"event_date": today.isoformat(), "symbol": "NVDA"})

    rows = await sm.recall(db, min_event_date=days_ago(30))
    syms = {r["metadata"]["symbol"] for r in rows}
    assert syms == {"AAPL", "NVDA"}  # TSLA correctly excluded.


@pytest.mark.asyncio
async def test_recall_include_legacy_false_drops_past_rows():
    db = _DB()
    today = datetime.now(timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    await sm.remember(db, text="old", metadata={"event_date": yesterday})
    await sm.remember(db, text="now", metadata={"event_date": today.isoformat()})

    active_only = await sm.recall(db, include_legacy=False)
    assert len(active_only) == 1
    assert active_only[0]["text"] == "now"


@pytest.mark.asyncio
async def test_recall_normalizes_filter_inputs():
    db = _DB()
    await sm.remember(db, text="x", metadata={"event_date": "2024-03-15"})
    # Mixed-precision filter input — boundary normalizer should
    # collapse to the date, so this query still hits the row.
    rows = await sm.recall(db, min_event_date="2024-03-14T00:00:00+00:00")
    assert len(rows) == 1


# ── 5. count_by_regime — operator audit ────────────────────────────


@pytest.mark.asyncio
async def test_count_by_regime_splits_active_and_legacy():
    db = _DB()
    today = datetime.now(timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    await sm.remember(db, text="a", metadata={"event_date": yesterday})
    await sm.remember(db, text="b", metadata={"event_date": yesterday})
    await sm.remember(db, text="c", metadata={"event_date": today.isoformat()})

    counts = await sm.count_by_regime(db)
    assert counts["legacy"] == 2
    assert counts["active"] == 1
    assert counts["total"] == 3


# ── 6. ordinal monotonicity ────────────────────────────────────────


def test_ordinal_is_monotonic():
    a = sm._date_to_ordinal("2024-01-01")
    b = sm._date_to_ordinal("2024-01-02")
    c = sm._date_to_ordinal("2024-12-31")
    assert a < b < c
    assert (b - a) == 1
