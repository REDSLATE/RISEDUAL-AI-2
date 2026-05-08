"""
Regression test for the 2026-05-01 brute-force-DoS-by-typo bug.

Pre-fix the login route extracted the client IP via
``request.client.host``, which behind the Kubernetes ingress always
returned the ingress pod IP (e.g. ``10.219.1.109``) rather than the
real end-user IP. Five failed login attempts from any external user
locked out the ``(pod_ip, email)`` bucket for 15 minutes — meaning
ANY user could DoS ANY other user (admin included) by mistyping a
password 5 times. That single oversight blocked the testing agent
suite for a full afternoon and was the "last leg" of the toxic-
spike investigation.

The fix: ``_get_client_ip(request)`` extracts the real client IP from
``X-Forwarded-For`` (first entry) or ``X-Real-IP``, falling back to
``request.client.host`` only when neither header is present.

These tests pin the contract so the regression can't return.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from routes.auth import _get_client_ip


def _make_request(*, headers=None, client_host="10.219.1.109"):
    """Build a minimal request stub that mirrors what FastAPI gives us."""
    req = MagicMock()
    req.headers = headers or {}
    req.client = MagicMock()
    req.client.host = client_host
    return req


def test_xff_first_entry_wins_over_proxy_pod_ip():
    """The whole point of the fix: real client IP beats pod IP."""
    req = _make_request(
        headers={"x-forwarded-for": "203.0.113.42, 10.219.1.109"},
        client_host="10.219.1.109",
    )
    assert _get_client_ip(req) == "203.0.113.42"


def test_xff_single_entry_is_returned_unchanged():
    req = _make_request(headers={"x-forwarded-for": "198.51.100.7"})
    assert _get_client_ip(req) == "198.51.100.7"


def test_xff_strips_whitespace_around_first_entry():
    req = _make_request(headers={"x-forwarded-for": "  198.51.100.7  ,10.0.0.1"})
    assert _get_client_ip(req) == "198.51.100.7"


def test_xff_capitalised_header_also_works():
    """HTTP headers are case-insensitive — both spellings must match."""
    req = _make_request(headers={"X-Forwarded-For": "198.51.100.7"})
    assert _get_client_ip(req) == "198.51.100.7"


def test_xreal_ip_used_when_xff_absent():
    req = _make_request(headers={"x-real-ip": "192.0.2.99"})
    assert _get_client_ip(req) == "192.0.2.99"


def test_xff_takes_precedence_over_xreal_ip():
    req = _make_request(headers={
        "x-forwarded-for": "203.0.113.42",
        "x-real-ip": "192.0.2.99",
    })
    assert _get_client_ip(req) == "203.0.113.42"


def test_falls_back_to_client_host_when_no_proxy_headers():
    req = _make_request(client_host="127.0.0.1")
    assert _get_client_ip(req) == "127.0.0.1"


def test_returns_unknown_when_no_client_and_no_headers():
    """Defensive: never crash on a malformed request — return a sentinel."""
    req = MagicMock()
    req.headers = {}
    req.client = None
    assert _get_client_ip(req) == "unknown"


def test_empty_xff_falls_through_to_other_sources():
    """An empty XFF header (e.g. from a misbehaving proxy) must NOT
    return the empty string — fall through to the next source."""
    req = _make_request(
        headers={"x-forwarded-for": "", "x-real-ip": "192.0.2.99"},
    )
    assert _get_client_ip(req) == "192.0.2.99"


def test_whitespace_only_xff_falls_through():
    req = _make_request(
        headers={"x-forwarded-for": "   ", "x-real-ip": "192.0.2.99"},
    )
    assert _get_client_ip(req) == "192.0.2.99"


def test_two_distinct_real_clients_get_distinct_buckets():
    """The bug-class regression check: two real clients sharing a
    proxy pod IP MUST hash to different brute-force buckets."""
    pod_ip = "10.219.1.109"
    req_alice = _make_request(
        headers={"x-forwarded-for": f"198.51.100.42, {pod_ip}"},
        client_host=pod_ip,
    )
    req_bob = _make_request(
        headers={"x-forwarded-for": f"198.51.100.99, {pod_ip}"},
        client_host=pod_ip,
    )
    alice_id = f"{_get_client_ip(req_alice)}:alice@example.com"
    bob_id = f"{_get_client_ip(req_bob)}:alice@example.com"  # same email
    assert alice_id != bob_id, (
        "Two distinct client IPs sharing a proxy pod must produce "
        "distinct brute-force identifiers — otherwise one user can "
        "lock out another."
    )
