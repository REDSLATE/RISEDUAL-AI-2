"""Tests for ``services.datetime_utils.ensure_utc``.

The helper is the canonical guard against MongoDB's tzinfo-stripping
behaviour. These tests pin the contract:

* tz-aware datetime    → returned unchanged
* tz-naive datetime    → tagged UTC
* ISO string with Z    → parsed, tagged UTC
* ISO string with +tz  → parsed, tz preserved
* ISO string naive     → parsed, tagged UTC
* Garbage string       → ``None``
* ``None``             → ``None``
* Non-supported types  → ``None``

The downstream contract: ``datetime.now(timezone.utc) - ensure_utc(x)``
must never raise ``TypeError: can't subtract offset-naive and
offset-aware datetimes``.
"""
from datetime import datetime, timezone, timedelta

import pytest

from services.datetime_utils import ensure_utc


def test_tz_aware_datetime_returned_unchanged():
    dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    out = ensure_utc(dt)
    assert out is dt


def test_tz_aware_non_utc_preserved():
    eastern = timezone(timedelta(hours=-5))
    dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=eastern)
    out = ensure_utc(dt)
    assert out is dt
    assert out.tzinfo is eastern


def test_tz_naive_datetime_tagged_utc():
    dt = datetime(2026, 1, 15, 12, 0, 0)  # naive — Mongo round-trip shape
    out = ensure_utc(dt)
    assert out is not None
    assert out.tzinfo is timezone.utc
    assert out.replace(tzinfo=None) == dt


def test_iso_string_with_z():
    out = ensure_utc("2026-01-15T12:00:00Z")
    assert out is not None
    assert out.tzinfo is not None
    assert out.utcoffset() == timedelta(0)


def test_iso_string_with_offset():
    out = ensure_utc("2026-01-15T12:00:00+02:00")
    assert out is not None
    assert out.utcoffset() == timedelta(hours=2)


def test_iso_string_naive_tagged_utc():
    out = ensure_utc("2026-01-15T12:00:00")
    assert out is not None
    assert out.tzinfo is timezone.utc


def test_unparseable_string_returns_none():
    assert ensure_utc("not a date") is None


def test_none_returns_none():
    assert ensure_utc(None) is None


@pytest.mark.parametrize("value", [42, 3.14, [], {}, object()])
def test_unsupported_types_return_none(value):
    assert ensure_utc(value) is None


def test_subtract_against_now_does_not_raise():
    """The actual contract that motivated this helper."""
    naive_from_mongo = datetime(2026, 1, 1, 0, 0, 0)
    safe = ensure_utc(naive_from_mongo)
    delta = (datetime.now(timezone.utc) - safe).total_seconds()
    assert delta > 0


def test_compare_against_now_does_not_raise():
    naive_from_mongo = datetime(2030, 1, 1, 0, 0, 0)
    safe = ensure_utc(naive_from_mongo)
    assert safe > datetime.now(timezone.utc)
