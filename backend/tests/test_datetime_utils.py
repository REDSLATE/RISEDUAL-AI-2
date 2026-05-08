"""Tests for ``services.datetime_utils.ensure_utc`` and ``to_iso_date``.

The helpers are the canonical guards against MongoDB's tzinfo-stripping
behaviour and against the brittle ``mongo_doc.get("timestamp", "")[:10]``
pattern used in the Mongo→Chroma sync layer. These tests pin both
contracts.
"""
from datetime import datetime, timezone, timedelta, date

import pytest

from services.datetime_utils import ensure_utc, to_iso_date


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



# ── to_iso_date — Mongo→Chroma sync layer ─────────────────────────


def test_to_iso_date_from_tz_aware_datetime():
    dt = datetime(2026, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
    assert to_iso_date(dt) == "2026-01-15"


def test_to_iso_date_from_tz_naive_datetime_mongo_round_trip():
    """The exact failure mode the user flagged: Mongo strips tzinfo
    and the old [:10] slice on a datetime raised TypeError."""
    naive = datetime(2026, 1, 15, 12, 30, 0)  # mimic Mongo BSON Date
    assert to_iso_date(naive) == "2026-01-15"


def test_to_iso_date_from_date_object():
    assert to_iso_date(date(2026, 1, 15)) == "2026-01-15"


def test_to_iso_date_from_iso_string_with_z():
    assert to_iso_date("2026-01-15T12:00:00Z") == "2026-01-15"


def test_to_iso_date_from_iso_string_with_offset():
    assert to_iso_date("2026-01-15T12:00:00+02:00") == "2026-01-15"


def test_to_iso_date_from_naive_iso_string():
    assert to_iso_date("2026-01-15T12:00:00") == "2026-01-15"


def test_to_iso_date_from_bare_date_string():
    assert to_iso_date("2026-01-15") == "2026-01-15"


def test_to_iso_date_from_date_only_prefix():
    """Some legacy rows store just YYYY-MM-DD with no time."""
    assert to_iso_date("2026-01-15-extra") == "2026-01-15"


def test_to_iso_date_returns_none_for_empty_string():
    """The other historic failure: ``""[:10]`` returned `""` which
    then collided with all other empty-date rows in _make_id."""
    assert to_iso_date("") is None


def test_to_iso_date_returns_none_for_none():
    assert to_iso_date(None) is None


def test_to_iso_date_returns_none_for_garbage():
    assert to_iso_date("not a date") is None
    assert to_iso_date("2026/01/15") is None  # wrong separator
    assert to_iso_date("01-15-2026") is None  # wrong order


def test_to_iso_date_returns_none_for_non_string_non_datetime():
    assert to_iso_date(12345) is None
    assert to_iso_date([]) is None
    assert to_iso_date({}) is None


def test_to_iso_date_invalid_calendar_date_returns_none():
    """``2026-13-99`` looks like a date prefix but isn't a real date."""
    assert to_iso_date("2026-13-99T00:00:00") is None
    assert to_iso_date("2026-13-99") is None
