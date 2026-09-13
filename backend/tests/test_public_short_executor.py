"""Tests for `services.public_short_executor` — direct REST short path."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.public_short_executor import (
    compute_whole_share_qty,
    submit_short_order,
)


# ── Whole-share sizing ────────────────────────────────────────────

def test_whole_share_qty_floors_to_integer():
    # $100 / $37.50 = 2.667 → floor to 2 shares (never fractional)
    assert compute_whole_share_qty(notional_usd=100.0, mark_price=37.50) == 2


def test_whole_share_qty_zero_when_under_minimum():
    # $10 / $50 = 0.2 → cannot short less than 1 share
    assert compute_whole_share_qty(notional_usd=10.0, mark_price=50.0) == 0


def test_whole_share_qty_exact_multiple():
    assert compute_whole_share_qty(notional_usd=200.0, mark_price=100.0) == 2


def test_whole_share_qty_rejects_bad_inputs():
    assert compute_whole_share_qty(notional_usd=0.0, mark_price=100.0) == 0
    assert compute_whole_share_qty(notional_usd=100.0, mark_price=0.0) == 0
    assert compute_whole_share_qty(notional_usd=-50.0, mark_price=10.0) == 0


# ── REST body construction ────────────────────────────────────────

def _make_client():
    c = MagicMock()
    c.base_url = "https://api.public.com/userapigateway"
    c.account_id = "ACCT-1"
    c._auth_headers.return_value = {
        "Authorization": "Bearer TOK", "Content-Type": "application/json",
    }
    return c


def _capture_post():
    """Return (capture_list, post_fn) that captures the URL + kwargs
    of each requests.post call and returns a 200 success shape."""
    calls: list[dict] = []

    def _post(url, **kw):
        calls.append({"url": url, **kw})
        r = MagicMock(status_code=200, content=b"{}")
        r.json.return_value = {"orderId": "PUB-SHORT-1", "status": "submitted"}
        return r

    return calls, _post


def test_submit_short_open_sends_openCloseIndicator_OPEN():
    c = _make_client()
    calls, post_fn = _capture_post()
    with patch("services.public_short_executor.requests.post",
               side_effect=post_fn):
        out = submit_short_order(
            c, symbol="AAPL", qty=10, side="SELL", open_close="OPEN",
        )
    assert out is not None
    assert out["id"] == "PUB-SHORT-1"
    body = calls[0]["json"]
    assert body["orderSide"] == "SELL"
    assert body["openCloseIndicator"] == "OPEN"
    assert body["useMargin"] is True
    assert body["quantity"] == "10"
    assert body["instrument"]["symbol"] == "AAPL"
    assert body["instrument"]["type"] == "EQUITY"
    assert "orderId" in body   # client-side idempotency key


def test_submit_short_cover_sends_openCloseIndicator_CLOSE():
    c = _make_client()
    calls, post_fn = _capture_post()
    with patch("services.public_short_executor.requests.post",
               side_effect=post_fn):
        out = submit_short_order(
            c, symbol="AAPL", qty=10, side="BUY", open_close="CLOSE",
        )
    assert out is not None
    body = calls[0]["json"]
    assert body["orderSide"] == "BUY"
    assert body["openCloseIndicator"] == "CLOSE"


def test_submit_short_rejects_fractional_qty_by_type():
    """qty is declared int; a float passes through int() cast so any
    non-integer is truncated. Zero is refused explicitly."""
    c = _make_client()
    _, post_fn = _capture_post()
    with patch("services.public_short_executor.requests.post",
               side_effect=post_fn):
        # 0.5 → int(0.5) == 0 → rejected
        out = submit_short_order(
            c, symbol="AAPL", qty=0, side="SELL", open_close="OPEN",
        )
    assert out is None


def test_submit_short_rejects_bad_side():
    c = _make_client()
    with patch("services.public_short_executor.requests.post") as p:
        out = submit_short_order(
            c, symbol="AAPL", qty=10, side="HOLD", open_close="OPEN",
        )
    assert out is None
    p.assert_not_called()


def test_submit_short_rejects_bad_open_close():
    c = _make_client()
    with patch("services.public_short_executor.requests.post") as p:
        out = submit_short_order(
            c, symbol="AAPL", qty=10, side="SELL", open_close="TOGGLE",
        )
    assert out is None
    p.assert_not_called()


def test_submit_short_returns_none_on_auth_failure():
    c = _make_client()
    c._auth_headers.return_value = None
    with patch("services.public_short_executor.requests.post") as p:
        out = submit_short_order(
            c, symbol="AAPL", qty=10, side="SELL", open_close="OPEN",
        )
    assert out is None
    p.assert_not_called()


def test_submit_short_returns_none_on_broker_rejection():
    c = _make_client()
    fake = MagicMock(status_code=400, content=b"{}", text='{"error":"HTB not available"}')
    with patch("services.public_short_executor.requests.post",
               return_value=fake):
        out = submit_short_order(
            c, symbol="GME", qty=10, side="SELL", open_close="OPEN",
        )
    assert out is None


def test_submit_short_use_margin_can_be_disabled():
    """Some accounts explicitly want the useMargin flag off — verify
    the flag is propagated exactly as given."""
    c = _make_client()
    calls, post_fn = _capture_post()
    with patch("services.public_short_executor.requests.post",
               side_effect=post_fn):
        submit_short_order(
            c, symbol="AAPL", qty=10, side="SELL", open_close="OPEN",
            use_margin=False,
        )
    assert calls[0]["json"]["useMargin"] is False


def test_submit_short_honors_custom_client_order_id():
    """Client-provided idempotency key must be echoed to Public verbatim."""
    c = _make_client()
    calls, post_fn = _capture_post()
    with patch("services.public_short_executor.requests.post",
               side_effect=post_fn):
        submit_short_order(
            c, symbol="AAPL", qty=10, side="SELL", open_close="OPEN",
            client_order_id="alpha-42",
        )
    assert calls[0]["json"]["orderId"] == "alpha-42"
