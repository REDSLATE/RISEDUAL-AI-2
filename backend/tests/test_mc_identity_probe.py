"""Tripwire coverage for the MC identity probe (2026-06).

Issue 1 from the handoff: MC's identity surface returned 401 when
the brain hit it with X-Brain-Id + X-Runtime-Token (the doc-blessed
runtime auth scheme). This probe endpoint surfaces the live MC
status code + a hint string so the operator can triage the cause
without writing one-off curls.

We pin the hint matrix here — the wire test (real HTTP) lives in
``test_admin_runtime_stamp.py`` style integration paths and is
exercised by the testing agent against the live preview pod.
"""
from __future__ import annotations

from routes.admin_runtime_stamp import _identity_probe_hint


def test_hint_when_unreachable():
    msg = _identity_probe_hint(None, "transport_error: timeout")
    assert "Could not reach MC" in msg


def test_hint_when_ok():
    msg = _identity_probe_hint(200, '{"identity": {...}}')
    assert "200" in msg and "accepted" in msg


def test_hint_when_401_explains_three_paths():
    msg = _identity_probe_hint(401, '{"detail": "unauthorized"}')
    # Three operator-facing branches must all be named so the
    # operator's next question to MC is concrete.
    assert "rotated" in msg
    assert "operator JWT" in msg
    assert "preview/prod" in msg


def test_hint_when_403_flags_jwt_only_path():
    msg = _identity_probe_hint(403, "")
    assert "403" in msg and "JWT" in msg


def test_hint_when_404_flags_route_drift():
    msg = _identity_probe_hint(404, "")
    assert "404" in msg and "route" in msg


def test_hint_default_falls_back_to_status_code():
    msg = _identity_probe_hint(500, "internal")
    assert "500" in msg
