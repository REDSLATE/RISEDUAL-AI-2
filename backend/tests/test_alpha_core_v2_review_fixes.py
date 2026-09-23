"""Regression tests for the 2026-06 code-review fixes:
missing-timestamp freshness gate, accepted-but-unknown-status handling,
and the single-flight live-cycle lock."""
from decimal import Decimal

import pytest

from services.alpha_core_v2.broker import _normalize_order, _parse_ts
from services.alpha_core_v2.contracts import ExecutionQuote, Outcome


def test_parse_ts_returns_none_on_unparseable():
    assert _parse_ts("") is None
    assert _parse_ts(None) is None
    assert _parse_ts("not-a-date") is None
    assert _parse_ts("2026-06-01T00:00:00Z") is not None
    assert _parse_ts(1790201852) is not None  # epoch seconds


def test_missing_timestamp_quote_is_infinite_age():
    q = ExecutionQuote(symbol="AAA", price=Decimal("10"), timestamp=None, source="x")
    assert q.age_seconds() == float("inf")


def test_normalize_order_unknown_status_with_id_is_accepted():
    r = _normalize_order({"status": "queued_weird", "id": "OID9"}, requested_qty=1.0)
    assert r.ok is True
    assert r.status == "accepted"
    assert r.order_id == "OID9"


def test_normalize_order_unknown_status_without_id_stays_failed():
    r = _normalize_order({"status": "weird"}, requested_qty=1.0)
    assert r.ok is False


def test_normalize_order_rejected_stays_failed_even_with_id():
    r = _normalize_order({"status": "rejected", "id": "X"}, requested_qty=1.0)
    assert r.ok is False
    assert r.status == "rejected"


@pytest.mark.asyncio
async def test_engine_blocks_missing_timestamp_quote():
    # Import the shared harness from the sibling test module.
    from tests.test_alpha_core_v2 import FakeBroker, _engine, _cand
    b = FakeBroker(equity=1000.0, buying_power=1000.0, positions=[],
                   quote_no_ts=True)
    eng = _engine(b)
    r = await eng._process("c1", _cand(), b.get_account(), live=True)
    assert r.outcome is Outcome.BLOCKED
    assert "missing_timestamp" in r.reason
    assert not b.submitted  # never reached ORDER


def test_live_cycle_lock_is_single_flight():
    import asyncio
    from routes import admin_alpha_v2
    assert isinstance(admin_alpha_v2._live_cycle_lock, asyncio.Lock)
