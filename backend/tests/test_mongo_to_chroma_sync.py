"""Tests for the Mongo→Chroma sync hardening in ``market_memory_service``.

The historic bug pattern across four call sites was:

    "date": mongo_doc.get("timestamp", "")[:10]

Which had three failure modes:

1. Mongo round-trips ``timestamp`` as a tz-naive ``datetime`` →
   ``datetime[:10]`` raises ``TypeError`` → caller's broad except
   swallows it → Chroma row never written → ChromaDB silently drifts
   from MongoDB.
2. ``timestamp`` is missing/None → ``""[:10]`` → empty string →
   ``_make_id``'s v1 hash collides ALL empty-date rows into the same
   doc id → newer episodes overwrite older ones, losing data.
3. ChromaDB metadata only accepts str/int/float/bool — passing a
   ``datetime`` object directly through ``regime["date"]`` would
   either be rejected by Chroma or coerced inconsistently.

These tests pin the fix:
* ``save_regime`` coerces ``date`` through ``to_iso_date`` so any
  shape of timestamp produces a clean ``YYYY-MM-DD``.
* ``_make_id`` uses the coerced date so str/datetime inputs hash
  identically.
* All metadata fields are strings (or normalized numbers) before
  reaching Chroma.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.market_memory_service import _make_id, save_regime


# ── _make_id (v1 fallback path) ─────────────────────────────────────


def test_make_id_treats_datetime_and_string_date_identically():
    """The core re-coercion guarantee: a Mongo BSON ``datetime`` and
    its ISO string twin must hash to the same id, otherwise the same
    logical episode would get two ChromaDB rows."""
    iso = "2026-01-15"
    naive_dt = datetime(2026, 1, 15, 9, 30, 0)
    aware_dt = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)

    id_str = _make_id({"symbol": "AAPL", "date": iso, "price": 180.0})
    id_naive = _make_id({"symbol": "AAPL", "date": naive_dt, "price": 180.0})
    id_aware = _make_id({"symbol": "AAPL", "date": aware_dt, "price": 180.0})

    assert id_str == id_naive == id_aware


def test_make_id_v2_path_unaffected_by_date_coercion():
    """When ``prediction_id`` is present, the id is keyed on it
    regardless of date format (v2 schema). This is the intended
    long-term path; just confirming the v1 fix didn't break v2."""
    id_a = _make_id({"prediction_id": "pred-123", "date": "2026-01-15"})
    id_b = _make_id({"prediction_id": "pred-123", "date": datetime(2026, 1, 15)})
    id_c = _make_id({"prediction_id": "pred-123"})  # no date at all
    assert id_a == id_b == id_c


def test_make_id_empty_date_no_longer_collides_with_other_empties():
    """v1 fallback, missing dates: previous code stored as empty
    string and collapsed every dateless row to one id per
    ``(symbol, "", price)``. With ``to_iso_date`` returning None, the
    key is still ``(symbol, "", price)`` — but now any caller that
    *thought* it was passing a date (datetime / non-empty string) gets
    a real id, so collisions only happen for genuinely-missing data
    rather than for type mismatches."""
    id_dateless = _make_id({"symbol": "AAPL", "price": 180.0})
    id_naive = _make_id({"symbol": "AAPL", "date": datetime(2026, 1, 15), "price": 180.0})
    # Dateless and date-bearing must NOT collide.
    assert id_dateless != id_naive


def test_make_id_v1_price_canonicalized_across_numeric_types():
    """Mongo can hand back ``Decimal('180.0')``, ``Decimal('180.00')``,
    ``180`` (int), ``180.0`` (float), or ``"180.00"`` (str) for a
    financial field — depending on which writer touched the doc
    last. Pre-fix these all hashed differently → up to FIVE
    ChromaDB rows for the same trade. ``f"{float(price):.4f}"``
    canonicalises to a single ``"180.0000"`` form."""
    from decimal import Decimal
    base = {"symbol": "AAPL", "date": "2026-01-15"}
    ids = [
        _make_id({**base, "price": Decimal("180.0")}),
        _make_id({**base, "price": Decimal("180.00")}),
        _make_id({**base, "price": 180}),
        _make_id({**base, "price": 180.0}),
        _make_id({**base, "price": "180.00"}),
        _make_id({**base, "price": "180"}),
    ]
    assert len(set(ids)) == 1, (
        "Numeric-type variance produced multiple ids — Decimal/int/"
        "float/str must all canonicalise to the same key."
    )


def test_make_id_v1_different_prices_still_distinct():
    """Defensive: the canonicalisation must not collapse genuinely
    different prices. ``180.00`` and ``180.50`` are different
    trades."""
    base = {"symbol": "AAPL", "date": "2026-01-15"}
    a = _make_id({**base, "price": 180.0})
    b = _make_id({**base, "price": 180.5})
    assert a != b


def test_make_id_v1_handles_non_numeric_price_gracefully():
    """If price is corrupt junk, the v1 path falls back to
    string-form so the row at least remains addressable instead of
    raising and breaking the whole save flow."""
    base = {"symbol": "AAPL", "date": "2026-01-15"}
    # Should not raise.
    h = _make_id({**base, "price": "not-a-number"})
    assert isinstance(h, str) and len(h) == 64


# ── v1 price edge cases ────────────────────────────────────────────


def test_make_id_v1_nan_price_treated_as_missing():
    """``float('nan')`` formatted with ``:.4f`` gives the literal
    string ``'nan'``. Hashing that would silently collapse every
    NaN-priced row to one id while LOOKING like distinct data.
    Treat as missing instead — keeps semantics honest and matches
    behaviour of ``price=None``."""
    base = {"symbol": "AAPL", "date": "2026-01-15"}
    id_nan = _make_id({**base, "price": float("nan")})
    id_missing = _make_id({**base, "price": None})
    id_omitted = _make_id({**base})
    # All three resolve to the same "no usable price" key.
    assert id_nan == id_missing == id_omitted


def test_make_id_v1_inf_price_treated_as_missing():
    """``float('inf')`` is structurally invalid as a trade price.
    Same treatment as NaN — fold into the missing-price bucket
    so we don't write a row keyed on the string ``'inf'``."""
    base = {"symbol": "AAPL", "date": "2026-01-15"}
    id_inf = _make_id({**base, "price": float("inf")})
    id_neginf = _make_id({**base, "price": float("-inf")})
    id_missing = _make_id({**base, "price": None})
    assert id_inf == id_neginf == id_missing


def test_make_id_v1_negative_zero_collapses_with_zero():
    """Python's ``-0.0`` formats to ``'-0.0000'`` while ``0.0``
    formats to ``'0.0000'`` — same logical value, different hashes
    if you don't fold them. Won't happen often in practice (only
    from explicit arithmetic), but the canonicalisation should be
    deterministic for ALL representations of zero."""
    base = {"symbol": "AAPL", "date": "2026-01-15"}
    id_pos = _make_id({**base, "price": 0.0})
    id_neg = _make_id({**base, "price": -0.0})
    id_int = _make_id({**base, "price": 0})
    id_str = _make_id({**base, "price": "0.00"})
    assert id_pos == id_neg == id_int == id_str


# ── v2 prediction_id hardening ─────────────────────────────────────


def test_make_id_v2_corrupt_prediction_ids_fall_back_to_v1():
    """The historic v2 path used ``f"v2|{prediction_id}"`` directly.
    A corrupt id (None, empty, whitespace-only, NaN, sentinel
    strings like ``"none"`` / ``"nan"``) would collapse every bad
    row onto a single ChromaDB id — silently overwriting earlier
    rows. Hardened path: ``_normalize_prediction_id`` rejects all
    such values, so ``_make_id`` falls through to the v1
    ``(symbol, date, price)`` fallback. Each row keeps a real id."""
    base = {"symbol": "AAPL", "date": "2026-01-15", "price": 180.0}

    # Each of these prediction_ids is "corrupt" and must NOT
    # collapse to ``"v2|"``. They should all fall through to v1
    # (and therefore equal the v1-only id).
    v1_id = _make_id(base)  # no prediction_id at all → v1 path
    corrupt_inputs = [
        None,
        "",
        "   ",           # whitespace only
        "\t\n",          # other whitespace
        float("nan"),
        float("inf"),
        float("-inf"),
        "nan",
        "NaN",
        "none",
        "None",
        "null",
        "NULL",
        "undefined",
        "inf",
        "-inf",
    ]
    for bad in corrupt_inputs:
        h = _make_id({**base, "prediction_id": bad})
        assert h == v1_id, (
            f"Corrupt prediction_id {bad!r} should fall back to v1, "
            f"not produce a distinct (or collision-prone) v2 hash."
        )


def test_make_id_v2_valid_prediction_id_still_uses_v2_path():
    """Defensive: hardening must not break the happy v2 path. A
    real prediction_id continues to take precedence over
    (symbol, date, price)."""
    base = {"symbol": "AAPL", "date": "2026-01-15", "price": 180.0}
    v1_id = _make_id(base)
    v2_id = _make_id({**base, "prediction_id": "pred-abc-123"})
    assert v2_id != v1_id


def test_make_id_v2_strips_whitespace_around_valid_prediction_id():
    """``" pred-123 "`` and ``"pred-123"`` are the same logical id;
    upstream Mongo writers occasionally leave incidental whitespace
    that would otherwise produce two ChromaDB rows for one
    prediction."""
    a = _make_id({"prediction_id": "pred-123"})
    b = _make_id({"prediction_id": "  pred-123  "})
    c = _make_id({"prediction_id": "\tpred-123\n"})
    assert a == b == c


def test_make_id_v2_distinct_ids_remain_distinct():
    """Sanity: two genuinely different prediction_ids must hash
    to different ChromaDB rows."""
    a = _make_id({"prediction_id": "pred-aaa"})
    b = _make_id({"prediction_id": "pred-bbb"})
    assert a != b


def test_make_id_v2_numeric_prediction_id_stringified():
    """If a numeric prediction_id sneaks through (legacy schema or
    test fixture), coerce to its string form rather than blowing up
    or skipping the v2 path entirely."""
    a = _make_id({"prediction_id": 12345})
    b = _make_id({"prediction_id": "12345"})
    assert a == b


# ── save_regime metadata coercion ──────────────────────────────────


@pytest.mark.asyncio
async def test_save_regime_coerces_datetime_date_to_string():
    """Mongo BSON datetime in ``regime["date"]`` must become a clean
    YYYY-MM-DD string in the metadata passed to Chroma."""
    fake_collection = MagicMock()
    fake_collection.upsert = MagicMock()

    with patch("services.market_memory_service._collection", fake_collection):
        await save_regime({
            "symbol": "AAPL",
            "date": datetime(2026, 1, 15, 9, 30, 0),  # tz-naive — Mongo shape
            "price": 180.0,
            "outcome": "hit",
            "confidence": 75,
        })

    fake_collection.upsert.assert_called_once()
    call_kwargs = fake_collection.upsert.call_args.kwargs
    metadata = call_kwargs["metadatas"][0]
    assert metadata["date"] == "2026-01-15"
    assert isinstance(metadata["date"], str)
    assert isinstance(metadata["symbol"], str)
    assert isinstance(metadata["outcome"], str)


@pytest.mark.asyncio
async def test_save_regime_coerces_iso_string_date_through_to_iso_date():
    fake_collection = MagicMock()
    fake_collection.upsert = MagicMock()

    with patch("services.market_memory_service._collection", fake_collection):
        await save_regime({
            "symbol": "TSLA",
            "date": "2026-01-15T12:30:00Z",
            "price": 250.0,
            "outcome": "miss",
            "confidence": 45,
        })

    metadata = fake_collection.upsert.call_args.kwargs["metadatas"][0]
    assert metadata["date"] == "2026-01-15"  # not the raw string


@pytest.mark.asyncio
async def test_save_regime_falls_back_to_today_for_garbage_date():
    """Empty / garbage dates were the silent corruption mode. Now we
    fall back to today's date so the row is at least usefully tagged
    rather than silently dropped or stored with date=''."""
    fake_collection = MagicMock()
    fake_collection.upsert = MagicMock()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with patch("services.market_memory_service._collection", fake_collection):
        await save_regime({
            "symbol": "AAPL",
            "date": "",  # empty — was the historic broken case
            "price": 180.0,
            "outcome": "hit",
            "confidence": 70,
        })

    metadata = fake_collection.upsert.call_args.kwargs["metadatas"][0]
    assert metadata["date"] == today
    assert metadata["date"] != ""


@pytest.mark.asyncio
async def test_save_regime_metadata_all_chroma_safe_types():
    """ChromaDB only accepts str/int/float/bool in metadata. Verify
    that every field we set is one of these — no datetime, no None,
    no nested dict."""
    fake_collection = MagicMock()
    fake_collection.upsert = MagicMock()

    with patch("services.market_memory_service._collection", fake_collection):
        await save_regime({
            "symbol": "AAPL",
            "date": datetime(2026, 1, 15),
            "price": 180.0,
            "outcome": "hit",
            "confidence": 80,
            "prediction_id": "pred-abc",
            "failure_code": "REGIME_SHIFT",
        })

    metadata = fake_collection.upsert.call_args.kwargs["metadatas"][0]
    for k, v in metadata.items():
        assert isinstance(v, (str, int, float, bool)), (
            f"metadata[{k!r}] is {type(v).__name__}, not Chroma-safe"
        )


@pytest.mark.asyncio
async def test_save_regime_handles_missing_date_gracefully():
    """When ``date`` key is absent entirely, fall back to today —
    same as for empty-string dates. Critical for the chroma_warmup
    rehydration path where some old predictions may not have a
    timestamp at all."""
    fake_collection = MagicMock()
    fake_collection.upsert = MagicMock()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with patch("services.market_memory_service._collection", fake_collection):
        await save_regime({
            "symbol": "AAPL",
            "price": 180.0,
            "outcome": "pending",
            # no "date" key
        })

    metadata = fake_collection.upsert.call_args.kwargs["metadatas"][0]
    assert metadata["date"] == today


@pytest.mark.asyncio
async def test_save_regime_id_stable_for_str_vs_datetime_date():
    """End-to-end: same logical episode passed with str date and
    datetime date must produce the same doc_id when upserting to
    Chroma — proving the re-coercion bug is closed."""
    fake_collection = MagicMock()
    fake_collection.upsert = MagicMock()

    with patch("services.market_memory_service._collection", fake_collection):
        await save_regime({
            "symbol": "AAPL", "date": "2026-01-15", "price": 180.0,
            "outcome": "hit", "confidence": 70,
        })
        await save_regime({
            "symbol": "AAPL", "date": datetime(2026, 1, 15, 9, 30),
            "price": 180.0, "outcome": "hit", "confidence": 70,
        })

    assert fake_collection.upsert.call_count == 2
    first_id = fake_collection.upsert.call_args_list[0].kwargs["ids"][0]
    second_id = fake_collection.upsert.call_args_list[1].kwargs["ids"][0]
    assert first_id == second_id, (
        "Same logical episode produced two different Chroma ids — "
        "the re-coercion bug is back."
    )
