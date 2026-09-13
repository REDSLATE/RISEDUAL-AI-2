"""Tests for `services.public_short_eligibility` — the 4-rung ladder."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.public_short_eligibility import (
    check_account_eligibility,
    check_instrument_shortable,
    check_no_existing_position,
    preflight_short,
    run_full_ladder,
)


def _make_client(*, base_url="https://api.public.com/userapigateway",
                 account_id="ACCT-1", headers=None):
    c = MagicMock()
    c.base_url = base_url
    c.account_id = account_id
    c._auth_headers.return_value = headers or {
        "Authorization": "Bearer TOK", "Content-Type": "application/json",
    }
    c.get_positions.return_value = []
    return c


# ── Rung 1: account eligibility ─────────────────────────────────────

def test_account_margin_buy_and_sell_passes():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {"accounts": [{
        "accountId": "ACCT-1",
        "brokerageAccountType": "MARGIN",
        "tradePermissions": "BUY_AND_SELL",
    }]}
    with patch("services.public_short_eligibility.requests.get",
               return_value=fake_resp):
        v = check_account_eligibility(c)
    assert v.eligible is True
    assert v.account_type == "MARGIN"
    assert v.trade_permissions == "BUY_AND_SELL"


def test_account_cash_type_fails():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {"accounts": [{
        "accountId": "ACCT-1",
        "brokerageAccountType": "CASH",
        "tradePermissions": "BUY_AND_SELL",
    }]}
    with patch("services.public_short_eligibility.requests.get",
               return_value=fake_resp):
        v = check_account_eligibility(c)
    assert v.eligible is False
    assert v.reason == "account_not_margin"


def test_account_buy_only_fails():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {"accounts": [{
        "accountId": "ACCT-1",
        "brokerageAccountType": "MARGIN",
        "tradePermissions": "BUY_ONLY",
    }]}
    with patch("services.public_short_eligibility.requests.get",
               return_value=fake_resp):
        v = check_account_eligibility(c)
    assert v.eligible is False
    assert v.reason == "account_permissions_insufficient"


def test_account_fetch_http_error_fails_closed():
    c = _make_client()
    fake_resp = MagicMock(status_code=401, content=b"", text="unauthorized")
    with patch("services.public_short_eligibility.requests.get",
               return_value=fake_resp):
        v = check_account_eligibility(c)
    assert v.eligible is False
    assert v.reason == "account_fetch_failed"


def test_account_auth_header_missing_fails_closed():
    c = _make_client(headers=None)
    c._auth_headers.return_value = None
    v = check_account_eligibility(c)
    assert v.eligible is False
    assert v.reason == "auth_failed"


# ── Rung 2: existing position ───────────────────────────────────────

def test_no_existing_position_passes():
    c = _make_client()
    c.get_positions.return_value = [
        {"symbol": "MSFT", "qty": 10.0, "side": "long"},
    ]
    v = check_no_existing_position(c, "AAPL")
    assert v.eligible is True


def test_existing_long_blocks_short():
    c = _make_client()
    c.get_positions.return_value = [
        {"symbol": "AAPL", "qty": 5.0, "side": "long"},
    ]
    v = check_no_existing_position(c, "AAPL")
    assert v.eligible is False
    assert v.reason == "existing_position_blocks_short"
    assert v.existing_position_qty == 5.0


def test_existing_short_also_blocks_new_short():
    """Doctrine: never stack a second short on top of an open short."""
    c = _make_client()
    c.get_positions.return_value = [
        {"symbol": "AAPL", "qty": 3.0, "side": "short"},
    ]
    v = check_no_existing_position(c, "AAPL")
    assert v.eligible is False


# ── Rung 3: instrument shortable ────────────────────────────────────

def test_instrument_easy_to_borrow_passes():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {
        "shortingAvailability": "EASY_TO_BORROW",
        "hardToBorrowPercentageRate": None,
    }
    with patch("services.public_short_eligibility.requests.get",
               return_value=fake_resp):
        v = check_instrument_shortable(c, "AAPL")
    assert v.eligible is True
    assert v.shorting_availability == "EASY_TO_BORROW"


def test_instrument_hard_to_borrow_exposes_rate():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {
        "shortingAvailability": "HARD_TO_BORROW",
        "hardToBorrowPercentageRate": 12.5,
    }
    with patch("services.public_short_eligibility.requests.get",
               return_value=fake_resp):
        v = check_instrument_shortable(c, "GME")
    assert v.eligible is True
    assert v.htb_rate_pct == 12.5


def test_instrument_not_shortable_fails():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {"shortingAvailability": "NOT_SHORTABLE"}
    with patch("services.public_short_eligibility.requests.get",
               return_value=fake_resp):
        v = check_instrument_shortable(c, "SPCX")
    assert v.eligible is False
    assert v.reason == "not_shortable"


def test_instrument_unknown_enum_fails_closed():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {"shortingAvailability": "MAYBE"}
    with patch("services.public_short_eligibility.requests.get",
               return_value=fake_resp):
        v = check_instrument_shortable(c, "AAPL")
    assert v.eligible is False
    assert v.reason == "shorting_availability_unknown"


# ── Rung 4: preflight ───────────────────────────────────────────────

def test_preflight_ok_returns_full_fields():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {
        "buyingPowerRequirement": 500.0,
        "marginImpact": 250.0,
        "upTickRuleRequired": False,
        "maxLocateQuantity": 100,
        "shortSellingInformation": {
            "shortingAvailability": "EASY_TO_BORROW",
            "hardToBorrowPercentageRate": None,
        },
    }
    with patch("services.public_short_eligibility.requests.post",
               return_value=fake_resp):
        v = preflight_short(c, symbol="AAPL", qty=10)
    assert v.eligible is True
    assert v.reason == "preflight_ok"
    assert v.buying_power_required == 500.0
    assert v.margin_impact == 250.0
    assert v.uptick_rule is False
    assert v.max_locate_qty == 100


def test_preflight_rejected_fails():
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {
        "rejected": True, "reason": "insufficient_margin",
    }
    with patch("services.public_short_eligibility.requests.post",
               return_value=fake_resp):
        v = preflight_short(c, symbol="AAPL", qty=10)
    assert v.eligible is False
    assert v.reason == "preflight_rejected"


def test_preflight_max_locate_below_requested_fails():
    """Requested 50 shares, broker only has 10 → refuse."""
    c = _make_client()
    fake_resp = MagicMock(status_code=200, content=b"{}")
    fake_resp.json.return_value = {"maxLocateQuantity": 10}
    with patch("services.public_short_eligibility.requests.post",
               return_value=fake_resp):
        v = preflight_short(c, symbol="AAPL", qty=50)
    assert v.eligible is False
    assert v.reason == "max_locate_below_requested"
    assert v.max_locate_qty == 10


# ── Full ladder ──────────────────────────────────────────────────────

def test_full_ladder_passes_when_everything_green():
    c = _make_client()

    def _get(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "trading/account" in url:
            r.json.return_value = {"accounts": [{
                "accountId": "ACCT-1",
                "brokerageAccountType": "MARGIN",
                "tradePermissions": "BUY_AND_SELL",
            }]}
        elif "marketdata/instruments" in url:
            r.json.return_value = {"shortingAvailability": "EASY_TO_BORROW"}
        else:
            r.json.return_value = {}
        return r

    def _post(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        r.json.return_value = {
            "buyingPowerRequirement": 500.0, "marginImpact": 250.0,
            "upTickRuleRequired": False, "maxLocateQuantity": 100,
        }
        return r

    with patch("services.public_short_eligibility.requests.get", side_effect=_get), \
         patch("services.public_short_eligibility.requests.post", side_effect=_post):
        v = run_full_ladder(c, symbol="AAPL", qty=10)

    assert v.eligible is True
    assert v.reason == "ladder_passed"
    assert v.account_type == "MARGIN"
    assert v.shorting_availability == "EASY_TO_BORROW"
    assert v.buying_power_required == 500.0


def test_full_ladder_short_circuits_on_first_failure():
    """If Rung 1 fails, we do not call Rungs 2-4 (network cost + no
    reason to look further)."""
    c = _make_client()
    _post_calls: list = []

    def _get(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "trading/account" in url:
            r.json.return_value = {"accounts": [{
                "accountId": "ACCT-1",
                "brokerageAccountType": "CASH",   # fails rung 1
                "tradePermissions": "BUY_AND_SELL",
            }]}
        else:
            r.json.return_value = {}
        return r

    def _post(url, **_kw):
        _post_calls.append(url)
        r = MagicMock(status_code=200, content=b"{}")
        r.json.return_value = {}
        return r

    with patch("services.public_short_eligibility.requests.get", side_effect=_get), \
         patch("services.public_short_eligibility.requests.post", side_effect=_post):
        v = run_full_ladder(c, symbol="AAPL", qty=10)

    assert v.eligible is False
    assert v.reason == "account_not_margin"
    assert _post_calls == [], "preflight should not have been called"


def test_full_ladder_htb_rate_policy_gate():
    """Policy cap on HTB rate; ladder rejects when exceeded."""
    c = _make_client()

    def _get(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "trading/account" in url:
            r.json.return_value = {"accounts": [{
                "accountId": "ACCT-1",
                "brokerageAccountType": "MARGIN",
                "tradePermissions": "BUY_AND_SELL",
            }]}
        elif "marketdata/instruments" in url:
            r.json.return_value = {
                "shortingAvailability": "HARD_TO_BORROW",
                "hardToBorrowPercentageRate": 25.0,   # 25%
            }
        else:
            r.json.return_value = {}
        return r

    with patch("services.public_short_eligibility.requests.get", side_effect=_get):
        v = run_full_ladder(c, symbol="GME", qty=5, max_htb_rate_pct=10.0)

    assert v.eligible is False
    assert v.reason == "htb_rate_too_expensive"
    assert v.htb_rate_pct == 25.0
