"""Tests for services.data_source_labeler — derived data_source field.

Covers:
- Date parsing (ISO, full timestamp, malformed → default).
- Per-doc label_data_source dispatches across the priority list
  of timestamp keys (opened_at > predicted_at > created_at > timestamp).
- annotate() is idempotent and returns the same list ref.
- floor_date_iso() honours env override.
- Naive datetimes assumed UTC.
- Strings, datetimes, and missing all coerce safely.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from services import data_source_labeler as dsl


# ── Floor-date parsing ────────────────────────────────────────────


def test_default_floor_when_env_unset(monkeypatch):
    monkeypatch.delenv("PUBLIC_DATA_FLOOR_DATE", raising=False)
    floor = dsl._floor_dt()
    assert floor.year == 2026 and floor.month == 4 and floor.day == 23
    assert floor.tzinfo is not None


def test_env_override_iso_date(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2025-01-15")
    assert dsl.floor_date_iso() == "2025-01-15"


def test_env_override_full_iso_timestamp(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2025-06-01T12:00:00Z")
    floor = dsl._floor_dt()
    assert floor.year == 2025 and floor.month == 6 and floor.day == 1


def test_malformed_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "not-a-date")
    assert dsl.floor_date_iso() == "2026-04-23"


# ── label_data_source ─────────────────────────────────────────────


def test_label_pre_floor_is_backtest(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    pre = "2026-04-22T10:00:00+00:00"
    assert dsl.label_data_source({"opened_at": pre}) == "backtest"


def test_label_post_floor_is_live(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    post = "2026-04-24T10:00:00+00:00"
    assert dsl.label_data_source({"opened_at": post}) == "live"


def test_label_exactly_at_floor_is_live(monkeypatch):
    """The floor itself is the start of "live" — strict less-than."""
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    at = "2026-04-23T00:00:00+00:00"
    assert dsl.label_data_source({"opened_at": at}) == "live"


def test_label_naive_datetime_assumed_utc(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    pre = datetime(2026, 4, 22, 10, 0, 0)  # naive
    assert dsl.label_data_source({"opened_at": pre}) == "backtest"


def test_label_priority_order(monkeypatch):
    """opened_at wins over predicted_at, predicted_at wins over
    created_at — first matching field decides."""
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    doc = {
        "opened_at": "2026-04-22T10:00:00+00:00",   # backtest
        "predicted_at": "2026-04-24T10:00:00+00:00",  # live
        "created_at": "2026-04-25T10:00:00+00:00",  # live
    }
    assert dsl.label_data_source(doc) == "backtest"


def test_label_falls_through_to_predicted_at(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    assert dsl.label_data_source({
        "predicted_at": "2026-04-22T10:00:00+00:00",
    }) == "backtest"


def test_label_no_timestamps_defaults_to_live():
    """Better to under-label than over-label — a doc with no
    timestamp shouldn't get a misleading 'backtest' badge."""
    assert dsl.label_data_source({}) == "live"
    assert dsl.label_data_source({"ticker": "AAPL"}) == "live"


def test_label_unparseable_timestamp_falls_through():
    """Unparseable opened_at falls through to predicted_at."""
    doc = {
        "opened_at": "not-a-date",
        "predicted_at": "2026-04-22T10:00:00+00:00",
    }
    # opened_at is unparseable → continue scanning →
    # predicted_at (pre-floor) → backtest
    import os
    os.environ["PUBLIC_DATA_FLOOR_DATE"] = "2026-04-23"
    try:
        assert dsl.label_data_source(doc) == "backtest"
    finally:
        os.environ.pop("PUBLIC_DATA_FLOOR_DATE", None)


def test_label_non_dict_input_safe():
    assert dsl.label_data_source(None) == "live"
    assert dsl.label_data_source("string") == "live"
    assert dsl.label_data_source([]) == "live"


# ── annotate ──────────────────────────────────────────────────────


def test_annotate_attaches_field(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    rows = [
        {"opened_at": "2026-04-22T10:00:00+00:00"},
        {"opened_at": "2026-04-24T10:00:00+00:00"},
    ]
    out = dsl.annotate(rows)
    assert [r["data_source"] for r in out] == ["backtest", "live"]


def test_annotate_idempotent(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    rows = [{"opened_at": "2026-04-22T10:00:00+00:00"}]
    dsl.annotate(rows)
    dsl.annotate(rows)
    assert rows[0]["data_source"] == "backtest"


def test_annotate_skips_non_dicts(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2026-04-23")
    # Mixed iterable — non-dicts pass through without error.
    out = dsl.annotate([
        {"opened_at": "2026-04-22T10:00:00+00:00"},
        None,  # type: ignore[list-item]
        "not-a-dict",  # type: ignore[list-item]
    ])
    assert out[0]["data_source"] == "backtest"
    assert out[1] is None
    assert out[2] == "not-a-dict"


# ── floor_date_iso ────────────────────────────────────────────────


def test_floor_date_iso_format(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_FLOOR_DATE", "2025-12-31")
    assert dsl.floor_date_iso() == "2025-12-31"
