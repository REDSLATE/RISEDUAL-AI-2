"""Regression test for the 2026-04-24 toxic-spike test-contamination cascade.

Lock the fix in: test fixtures whose `symbol` starts with TEST_/MOCK_/
FAKE_/DUMMY_/FIXTURE_ must NEVER reach a toxic-spike alert. We test:

  1. `_is_real_symbol` correctly classifies test vs real tickers.
  2. The boundary filter in `_send_toxic_alerts` strips test rows
     and adjusts `toxic_count` accordingly.
  3. When the ONLY toxic rows are test fixtures, the alert is fully
     suppressed (no email, no dedup row burned, no in-app fanout).
  4. Real symbols still flow through unchanged.
  5. The emergency mute (`TOXIC_SPIKE_ALERTS_DISABLED=true`) gates
     all fanout regardless of contents.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def test_is_real_symbol_classifies_test_prefixes():
    from services.market_memory_service import _is_real_symbol

    # Real tickers — pass
    for sym in ("NVDA", "AAPL", "BRK.B", "tsla", "spy"):
        assert _is_real_symbol(sym), f"Expected '{sym}' real"

    # Test/mock/fake — drop
    for sym in ("TEST_FAIL_61", "test_xyz", "MOCK_AAPL", "FAKE_NVDA",
                "DUMMY_001", "FIXTURE_ABC"):
        assert not _is_real_symbol(sym), f"Expected '{sym}' filtered"

    # Edge cases — None / empty / non-string
    for sym in (None, "", 0, [], {}):
        assert not _is_real_symbol(sym), f"Expected falsy '{sym!r}' filtered"


def test_send_toxic_alerts_drops_test_fixtures_at_boundary(monkeypatch):
    """Mixed real+test toxic set → only real symbols reach the email
    pipeline; toxic_count is adjusted; dedup row reflects real count."""
    from services import market_memory_service as mms

    monkeypatch.delenv("TOXIC_SPIKE_ALERTS_DISABLED", raising=False)

    cleanup_results = {
        "toxic_removed": 3,  # raw count
        "toxic_details": [
            {"id": "1", "symbol": "NVDA",         "confidence": 0.92, "date": "2026-04-24"},
            {"id": "2", "symbol": "TEST_FAIL_61", "confidence": 0.95, "date": "2026-04-24"},
            {"id": "3", "symbol": "AAPL",         "confidence": 0.88, "date": "2026-04-24"},
        ],
        "obsolete_removed": 0,
        "total_before": 100,
        "total_after": 97,
    }

    captured: dict = {}

    async def _fake_email(**kwargs):
        captured["email_kwargs"] = kwargs
        return True

    # Mock alert_dedup so we don't hit Mongo + capture what was reserved
    async def _fake_record(db, **kw):
        captured["recorded"] = kw
        return None

    async def _fake_persistence(*a, **kw):
        return 0

    with patch.object(mms, "_db", AsyncMock()):
        # Mock alerts_sent.update_one + agent_activity narration so
        # we don't need a live Mongo + log_alert_* services.
        mms._db.alerts_sent = AsyncMock()
        mms._db.alerts_sent.update_one = AsyncMock()
        mms._db.users = AsyncMock()
        # users.find returns an empty async cursor for the in-app path
        async def _empty_cursor():
            return
            yield  # noqa: F811
        mms._db.users.find = lambda *a, **kw: _empty_cursor()
        mms._db.notifications = AsyncMock()
        mms._db.notifications.insert_many = AsyncMock()

        with patch("services.alert_dedup.record_alert", _fake_record), \
             patch("services.alert_dedup.persistence_run_count", _fake_persistence), \
             patch("services.email_service.send_toxic_spikes_email", _fake_email):
            asyncio.get_event_loop().run_until_complete(
                mms._send_toxic_alerts(cleanup_results)
            )

    # Email kwargs reflect filtered count + filtered details
    assert "email_kwargs" in captured, "Email path should fire for real symbols"
    kwargs = captured["email_kwargs"]
    assert kwargs["toxic_count"] == 2, "TEST_FAIL_61 should be filtered out"
    syms = {d["symbol"] for d in kwargs["spike_details"]}
    assert syms == {"NVDA", "AAPL"}, f"Test fixture leaked: {syms}"
    # Dedup row reserved with adjusted count
    rec = captured.get("recorded", {})
    assert rec.get("metadata", {}).get("toxic_count") == 2


def test_send_toxic_alerts_suppresses_all_test_set(monkeypatch):
    """If EVERY toxic row is a test fixture, the alert short-circuits
    BEFORE reserving a dedup row or sending email."""
    from services import market_memory_service as mms

    monkeypatch.delenv("TOXIC_SPIKE_ALERTS_DISABLED", raising=False)

    cleanup_results = {
        "toxic_removed": 2,
        "toxic_details": [
            {"id": "1", "symbol": "TEST_FAIL_61", "confidence": 0.95, "date": "2026-04-24"},
            {"id": "2", "symbol": "MOCK_AAPL",    "confidence": 0.90, "date": "2026-04-24"},
        ],
        "obsolete_removed": 0,
        "total_before": 50, "total_after": 48,
    }

    fired = {"email": 0, "record": 0}

    async def _fake_email(**kwargs):
        fired["email"] += 1
        return True

    async def _fake_record(db, **kw):
        fired["record"] += 1

    with patch.object(mms, "_db", AsyncMock()), \
         patch("services.alert_dedup.record_alert", _fake_record), \
         patch("services.email_service.send_toxic_spikes_email", _fake_email):
        asyncio.get_event_loop().run_until_complete(
            mms._send_toxic_alerts(cleanup_results)
        )

    assert fired["email"] == 0, "Email must NOT fire when all rows are test fixtures"
    assert fired["record"] == 0, "Dedup row must NOT be burned for test-only sets"


def test_send_toxic_alerts_passes_through_real_symbols(monkeypatch):
    """Sanity: a clean toxic set with NO test rows passes through
    unchanged (no toxic_count adjustment, no warning log)."""
    from services import market_memory_service as mms

    monkeypatch.delenv("TOXIC_SPIKE_ALERTS_DISABLED", raising=False)

    cleanup_results = {
        "toxic_removed": 2,
        "toxic_details": [
            {"id": "1", "symbol": "NVDA", "confidence": 0.92, "date": "2026-04-24"},
            {"id": "2", "symbol": "AAPL", "confidence": 0.88, "date": "2026-04-24"},
        ],
        "obsolete_removed": 0,
        "total_before": 100, "total_after": 98,
    }

    captured: dict = {}

    async def _fake_email(**kwargs):
        captured["kwargs"] = kwargs
        return True

    async def _fake_record(db, **kw):
        captured["recorded"] = kw

    async def _fake_persistence(*a, **kw):
        return 0

    with patch.object(mms, "_db", AsyncMock()):
        mms._db.alerts_sent = AsyncMock()
        mms._db.alerts_sent.update_one = AsyncMock()
        async def _empty_cursor():
            return
            yield  # noqa: F811
        mms._db.users = AsyncMock()
        mms._db.users.find = lambda *a, **kw: _empty_cursor()
        mms._db.notifications = AsyncMock()
        mms._db.notifications.insert_many = AsyncMock()

        with patch("services.alert_dedup.record_alert", _fake_record), \
             patch("services.alert_dedup.persistence_run_count", _fake_persistence), \
             patch("services.email_service.send_toxic_spikes_email", _fake_email):
            asyncio.get_event_loop().run_until_complete(
                mms._send_toxic_alerts(cleanup_results)
            )

    assert captured["kwargs"]["toxic_count"] == 2
    syms = {d["symbol"] for d in captured["kwargs"]["spike_details"]}
    assert syms == {"NVDA", "AAPL"}


def test_emergency_mute_short_circuits_everything(monkeypatch):
    """`TOXIC_SPIKE_ALERTS_DISABLED=true` halts the pipeline before
    any DB read or email call, regardless of contents."""
    from services import market_memory_service as mms

    monkeypatch.setenv("TOXIC_SPIKE_ALERTS_DISABLED", "true")

    cleanup_results = {
        "toxic_removed": 99,
        "toxic_details": [
            {"id": "1", "symbol": "NVDA", "confidence": 0.99, "date": "2026-04-24"},
        ],
    }

    fired = {"email": 0, "record": 0}

    async def _fake_email(**kwargs):
        fired["email"] += 1
        return True

    async def _fake_record(db, **kw):
        fired["record"] += 1

    with patch.object(mms, "_db", AsyncMock()), \
         patch("services.alert_dedup.record_alert", _fake_record), \
         patch("services.email_service.send_toxic_spikes_email", _fake_email):
        asyncio.get_event_loop().run_until_complete(
            mms._send_toxic_alerts(cleanup_results)
        )

    assert fired["email"] == 0
    assert fired["record"] == 0
