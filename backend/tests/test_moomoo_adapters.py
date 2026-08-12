"""Tests for MooMoo adapters — V1 safety guarantees.

Focus: cannot actually reach OpenD in the preview container, so tests
target the pre-network safety gates (feature flag, notional cap,
single-position, unlock-password required) and the symbol/L2 helpers.
"""
from __future__ import annotations

import os

import pytest

from services import moomoo_broker_adapter as bk
from services import moomoo_market_data_adapter as md


# ─── symbol translation ─────────────────────────────

def test_symbol_translation_roundtrip():
    assert md._to_moomoo_symbol("aapl") == "US.AAPL"
    assert md._to_moomoo_symbol("US.MSFT") == "US.MSFT"
    assert md._from_moomoo_symbol("US.NVDA") == "NVDA"
    assert md._from_moomoo_symbol("nvda") == "NVDA"


# ─── V1 safety gates on submit_equity ────────────────

def _clear_env(monkeypatch):
    for k in ("MOOMOO_LIVE_ENABLED", "MOOMOO_ACC_ID",
              "MOOMOO_TRADE_UNLOCK_PASSWORD", "MOOMOO_MAX_NOTIONAL_USD",
              "MOOMOO_OPTIONS_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    bk._cached_acc_id = None
    bk._trade_ctx = None


def test_submit_blocks_when_live_disabled(monkeypatch):
    _clear_env(monkeypatch)
    r = bk.submit_equity(symbol="AAPL", side="BUY", qty=1, limit_price=100.0,
                          client_order_id="c1")
    assert r.ok is False
    assert r.error == "moomoo_live_disabled"


def test_submit_blocks_without_acc_id(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MOOMOO_LIVE_ENABLED", "1")
    monkeypatch.setattr(bk, "_get_trade_ctx", lambda: object())  # bypass ctx
    r = bk.submit_equity(symbol="AAPL", side="BUY", qty=1, limit_price=100.0,
                          client_order_id="c1")
    assert r.ok is False
    assert r.error == "no_context_or_acc_id"


def test_submit_blocks_when_notional_over_cap(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MOOMOO_LIVE_ENABLED", "1")
    monkeypatch.setenv("MOOMOO_ACC_ID", "12345")
    monkeypatch.setenv("MOOMOO_MAX_NOTIONAL_USD", "50")
    monkeypatch.setattr(bk, "_get_trade_ctx", lambda: object())
    r = bk.submit_equity(symbol="AAPL", side="BUY", qty=1, limit_price=100.0,
                          client_order_id="c1")
    assert r.ok is False
    assert r.error and r.error.startswith("notional_over_cap")


def test_submit_blocks_when_unlock_password_missing(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MOOMOO_LIVE_ENABLED", "1")
    monkeypatch.setenv("MOOMOO_ACC_ID", "12345")
    monkeypatch.setenv("MOOMOO_MAX_NOTIONAL_USD", "1000")
    monkeypatch.setattr(bk, "_get_trade_ctx", lambda: object())
    monkeypatch.setattr(bk, "_one_position_open", lambda: False)
    r = bk.submit_equity(symbol="AAPL", side="BUY", qty=1, limit_price=10.0,
                          client_order_id="c1")
    assert r.ok is False
    assert r.error == "unlock_password_missing"


def test_submit_blocks_when_position_already_open(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MOOMOO_LIVE_ENABLED", "1")
    monkeypatch.setenv("MOOMOO_ACC_ID", "12345")
    monkeypatch.setenv("MOOMOO_MAX_NOTIONAL_USD", "1000")
    monkeypatch.setenv("MOOMOO_TRADE_UNLOCK_PASSWORD", "not-real")
    monkeypatch.setattr(bk, "_get_trade_ctx", lambda: object())
    monkeypatch.setattr(bk, "_one_position_open", lambda: True)
    r = bk.submit_equity(symbol="AAPL", side="BUY", qty=1, limit_price=10.0,
                          client_order_id="c1")
    assert r.ok is False
    assert r.error == "position_already_open"


# ─── options gated OFF by default ────────────────────

def test_options_submit_disabled_by_default(monkeypatch):
    _clear_env(monkeypatch)
    r = bk.submit_option(option_symbol="AAPL 250321 200 C",
                          side="BUY", qty=1, limit_price=1.20,
                          client_order_id="opt1")
    assert r.ok is False
    assert r.error == "options_disabled"


def test_options_submit_still_blocked_when_flag_on_pending_policy(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MOOMOO_OPTIONS_ENABLED", "1")
    r = bk.submit_option(option_symbol="AAPL 250321 200 C",
                          side="BUY", qty=1, limit_price=1.20,
                          client_order_id="opt1")
    assert r.ok is False
    assert r.error == "options_execution_policy_not_ready"


# ─── broker router ───────────────────────────────────

@pytest.mark.asyncio
async def test_router_default_is_public(monkeypatch):
    monkeypatch.delenv("BROKER_DEFAULT", raising=False)
    from services.broker_router import resolve_broker, default_broker
    assert default_broker() == "public"
    assert await resolve_broker(None, {}) == "public"


@pytest.mark.asyncio
async def test_router_respects_intent_broker_field():
    from services.broker_router import resolve_broker
    assert await resolve_broker(None, {"broker": "moomoo"}) == "moomoo"


@pytest.mark.asyncio
async def test_router_rejects_unknown_broker_field():
    from services.broker_router import resolve_broker
    # Unknown broker → falls back to default (public), never crashes
    assert await resolve_broker(None, {"broker": "e-trade"}) == "public"


# ─── secrets never in status payloads ────────────────

def test_broker_status_never_leaks_credentials(monkeypatch):
    monkeypatch.setenv("MOOMOO_TRADE_UNLOCK_PASSWORD", "secret-please-hide")
    monkeypatch.setenv("MOOMOO_ACC_ID", "99999")
    s = bk.status()
    flat = str(s)
    assert "secret-please-hide" not in flat
    # acc_id itself is fine (int), but the password must never appear
    assert s["acc_id_configured"] is True
