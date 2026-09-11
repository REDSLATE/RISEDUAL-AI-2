"""Broker reconcilers — Public.com + MooMoo. Each maps broker-native
status strings into the normalized watchdog outcome vocabulary and
degrades cleanly (UNREACHABLE) on any error.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.alpha_broker_event_watchdog import WatchdogEntry
from services import alpha_broker_reconcilers as reconcilers


def _entry(**overrides) -> WatchdogEntry:
    base = dict(
        broker="public",
        client_order_id="coid-1",
        symbol="AAPL",
        account_id="acc-1",
        broker_order_id="bo-1",
        submitted_at_ns=1,
    )
    base.update(overrides)
    return WatchdogEntry(**base)


# ── Status mapping — Public.com ────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("FILLED", "FILLED"),
    ("closed", "FILLED"),
    ("Completed", "FILLED"),
    ("EXECUTED", "FILLED"),
    ("REJECTED", "REJECTED"),
    ("CANCELED", "CANCELED"),
    ("CANCELLED", "CANCELED"),
    ("EXPIRED", "CANCELED"),
    ("PENDING", "OPEN"),
    ("ACCEPTED", "OPEN"),
    ("NEW", "OPEN"),
    ("PARTIALLY_FILLED", "OPEN"),
    ("something-weird", ""),
])
def test_public_status_mapping(status, expected):
    assert reconcilers._map_public_status(status) == expected


# ── Status mapping — MooMoo ────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("FILLED_ALL", "FILLED"),
    ("FILLED", "FILLED"),
    ("FILLED_PART", "OPEN"),
    ("SUBMITTED", "OPEN"),
    ("SUBMITTING", "OPEN"),
    ("WAITING_SUBMIT", "OPEN"),
    ("CANCELLED_ALL", "CANCELED"),
    ("CANCELLED_PART", "CANCELED"),
    ("FAILED", "REJECTED"),
    ("SUBMIT_FAILED", "REJECTED"),
    ("DISABLED", "REJECTED"),
    ("weird_status", ""),
])
def test_moomoo_status_mapping(status, expected):
    assert reconcilers._map_moomoo_status(status) == expected


# ── Public reconciler behaviour ────────────────────────────────────

def test_public_reconcile_unreachable_when_no_client(monkeypatch):
    monkeypatch.setattr(reconcilers, "_public_client", lambda: None)
    result = reconcilers.reconcile_public(_entry())
    assert result["outcome"] == "UNREACHABLE"


def test_public_reconcile_filled_from_get_order(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_order.return_value = {"status": "FILLED", "id": "bo-1"}
    monkeypatch.setattr(reconcilers, "_public_client", lambda: fake_client)
    result = reconcilers.reconcile_public(_entry())
    assert result["outcome"] == "FILLED"
    assert result["broker_order_id"] == "bo-1"


def test_public_reconcile_absent_when_get_order_returns_none(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_order.return_value = None
    monkeypatch.setattr(reconcilers, "_public_client", lambda: fake_client)
    result = reconcilers.reconcile_public(_entry())
    assert result["outcome"] == "ABSENT"


def test_public_reconcile_scans_orders_when_no_broker_id(monkeypatch):
    fake_client = MagicMock()
    # No get_order attribute on client → fall through to get_orders scan.
    del fake_client.get_order
    fake_client.get_orders.return_value = [
        {"client_order_id": "someone-else", "status": "FILLED", "id": "x"},
        {"client_order_id": "coid-1", "status": "PENDING", "id": "bo-scan"},
    ]
    monkeypatch.setattr(reconcilers, "_public_client", lambda: fake_client)
    entry = _entry(broker_order_id=None)
    result = reconcilers.reconcile_public(entry)
    assert result["outcome"] == "OPEN"
    assert result["broker_order_id"] == "bo-scan"


def test_public_reconcile_absent_when_not_in_recent_orders(monkeypatch):
    fake_client = MagicMock()
    del fake_client.get_order
    fake_client.get_orders.return_value = [
        {"client_order_id": "someone-else", "status": "FILLED"},
    ]
    monkeypatch.setattr(reconcilers, "_public_client", lambda: fake_client)
    result = reconcilers.reconcile_public(_entry(broker_order_id=None))
    assert result["outcome"] == "ABSENT"


def test_public_reconcile_unreachable_on_unknown_status(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_order.return_value = {"status": "MARTIAN", "id": "bo-1"}
    monkeypatch.setattr(reconcilers, "_public_client", lambda: fake_client)
    result = reconcilers.reconcile_public(_entry())
    assert result["outcome"] == "UNREACHABLE"


def test_public_reconcile_unreachable_on_exception(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_order.side_effect = RuntimeError("network down")
    monkeypatch.setattr(reconcilers, "_public_client", lambda: fake_client)
    result = reconcilers.reconcile_public(_entry())
    assert result["outcome"] == "UNREACHABLE"


# ── MooMoo reconciler behaviour ────────────────────────────────────

def test_moomoo_reconcile_unreachable_when_orders_none(monkeypatch):
    from services import moomoo_broker_adapter
    monkeypatch.setattr(moomoo_broker_adapter, "orders", lambda: None)
    result = reconcilers.reconcile_moomoo(_entry(broker="moomoo"))
    assert result["outcome"] == "UNREACHABLE"


def test_moomoo_reconcile_absent_when_not_in_list(monkeypatch):
    from services import moomoo_broker_adapter
    monkeypatch.setattr(moomoo_broker_adapter, "orders", lambda: [
        {"remark": "other-coid", "order_id": "x", "order_status": "FILLED_ALL"},
    ])
    result = reconcilers.reconcile_moomoo(_entry(broker="moomoo"))
    assert result["outcome"] == "ABSENT"


def test_moomoo_reconcile_matches_by_remark(monkeypatch):
    from services import moomoo_broker_adapter
    monkeypatch.setattr(moomoo_broker_adapter, "orders", lambda: [
        {"remark": "coid-1", "order_id": "mm-1", "order_status": "FILLED_ALL"},
    ])
    result = reconcilers.reconcile_moomoo(_entry(broker="moomoo"))
    assert result["outcome"] == "FILLED"
    assert result["broker_order_id"] == "mm-1"


def test_moomoo_reconcile_matches_by_broker_order_id(monkeypatch):
    from services import moomoo_broker_adapter
    monkeypatch.setattr(moomoo_broker_adapter, "orders", lambda: [
        {"remark": "different-coid", "order_id": "bo-1", "order_status": "SUBMITTED"},
    ])
    result = reconcilers.reconcile_moomoo(_entry(broker="moomoo", broker_order_id="bo-1"))
    assert result["outcome"] == "OPEN"


def test_moomoo_reconcile_unreachable_on_exception(monkeypatch):
    from services import moomoo_broker_adapter
    def _boom():
        raise RuntimeError("openD dead")
    monkeypatch.setattr(moomoo_broker_adapter, "orders", _boom)
    result = reconcilers.reconcile_moomoo(_entry(broker="moomoo"))
    assert result["outcome"] == "UNREACHABLE"


# ── Register-all wiring ────────────────────────────────────────────

def test_register_all_registers_both_brokers():
    from services import alpha_broker_event_watchdog as watchdog
    watchdog._reset_for_tests()
    reconcilers.register_all()
    assert "public" in watchdog._RECONCILERS
    assert "moomoo" in watchdog._RECONCILERS
    watchdog._reset_for_tests()
