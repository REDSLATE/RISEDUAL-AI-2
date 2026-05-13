"""Offline doctrine smoke test — runs WITHOUT touching Mission Control.

Run::

    python3 -m backend.sovereign.smoke_test
    # expect: 8/8 PASS
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from .local_state import LocalState, LocalStateError
from .mc_client import (
    MCContractError,
    auth_headers,
    build_contribution_body,
    contribution_url,
    heartbeat_url,
)
from .wild_adaptive_core_v2 import (
    LIVE_TRADING_ENABLED,
    assert_doctrine,
)


def _check(name: str, fn) -> tuple[str, bool, str]:
    try:
        fn()
        return name, True, "ok"
    except Exception as e:  # noqa: BLE001
        return name, False, f"{type(e).__name__}: {e}"


def test_doctrine_constant_is_literally_false() -> None:
    assert LIVE_TRADING_ENABLED is False, "lock #1: must be the literal False"


def test_assert_doctrine_passes_when_safe() -> None:
    assert_doctrine()  # must not raise


def test_local_state_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state.json"
        s1 = LocalState(brain="alpha", path=p, mode="DTD")
        assert s1.weights == {}
        s1.set_weights({"trend": 0.85, "macd": 0.65, "rsi": -0.25})
        s1.set_learning_rate(0.06)
        s1.save()

        s2 = LocalState(brain="alpha", path=p, mode="DTD")
        assert s2.weights == {"trend": 0.85, "macd": 0.65, "rsi": -0.25}
        assert s2.learning_rate == 0.06


def test_local_state_rejects_out_of_range_weight() -> None:
    s = LocalState(brain="alpha", path="/tmp/_unused_smoke.json", mode="DTD")
    try:
        s.set_weights({"trend": 5.0})
    except LocalStateError:
        return
    raise AssertionError("expected LocalStateError for weight > 3.0")


def test_local_state_rejects_bad_mode() -> None:
    try:
        LocalState(brain="alpha", path="/tmp/_unused.json", mode="WILD")
    except LocalStateError:
        return
    raise AssertionError("expected LocalStateError for invalid mode")


def test_mc_client_url_and_auth_shapes() -> None:
    base = "https://example.invalid"
    assert heartbeat_url(base, "alpha") == "https://example.invalid/api/heartbeat-ping/alpha"
    assert contribution_url(base, "alpha") == (
        "https://example.invalid/api/runtime-discussion/sovereign/contribution?runtime=alpha"
    )
    h = auth_headers("tok-123")
    assert h["X-Runtime-Token"] == "tok-123"
    assert "Authorization" not in h, "must NOT use Bearer/Authorization"
    assert h["Content-Type"] == "application/json"


def test_contribution_body_strict_schema() -> None:
    body = build_contribution_body(
        mode="DTD",
        weights={"trend": 0.85, "macd": 0.65, "rsi": -0.25},
        learning_rate=0.06,
        recent_outcomes=[{
            "symbol": "BTC/USD",
            "action": "BUY",
            "confidence": 0.78,
            "outcome": 1,
            "resolved_at": "2026-02-13T00:55:00+00:00",
            "notional": 0.0,
        }],
        notes="smoke",
    )
    assert body["mode"] == "DTD"
    assert body["live_trading_enabled"] is False, "lock #3 mirror: must serialize False"
    assert body["weights"]["trend"] == 0.85
    assert body["learning_rate"] == 0.06
    assert body["training_signal"] is False
    assert len(body["recent_outcomes"]) == 1


def test_contribution_body_rejects_invalid_payloads() -> None:
    # weight out of range
    try:
        build_contribution_body(mode="DTD", weights={"trend": 9.0}, learning_rate=0.06)
    except MCContractError:
        pass
    else:
        raise AssertionError("expected MCContractError for weight=9.0")

    # PRD + training_signal=True
    try:
        build_contribution_body(
            mode="PRD",
            weights={"trend": 0.5},
            learning_rate=0.06,
            training_signal=True,
        )
    except MCContractError:
        pass
    else:
        raise AssertionError("expected MCContractError for PRD+training_signal")

    # bad action in recent_outcomes
    try:
        build_contribution_body(
            mode="DTD",
            weights={"trend": 0.5},
            learning_rate=0.06,
            recent_outcomes=[{
                "symbol": "X", "action": "YOLO", "confidence": 0.5,
                "outcome": 1,
            }],
        )
    except MCContractError:
        pass
    else:
        raise AssertionError("expected MCContractError for action='YOLO'")


CHECKS = [
    ("lock #1 — LIVE_TRADING_ENABLED is False", test_doctrine_constant_is_literally_false),
    ("lock #2 — assert_doctrine passes when safe", test_assert_doctrine_passes_when_safe),
    ("LocalState roundtrip (write+load)", test_local_state_roundtrip),
    ("LocalState rejects weight > 3.0", test_local_state_rejects_out_of_range_weight),
    ("LocalState rejects invalid mode", test_local_state_rejects_bad_mode),
    ("mc_client URLs + X-Runtime-Token header", test_mc_client_url_and_auth_shapes),
    ("contribution body strict schema accepts valid", test_contribution_body_strict_schema),
    ("contribution body strict schema rejects invalid", test_contribution_body_rejects_invalid_payloads),
]


def main() -> int:
    pass_n = 0
    fail_n = 0
    for name, fn in CHECKS:
        n, ok, msg = _check(name, fn)
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {n}: {msg}")
        if ok:
            pass_n += 1
        else:
            fail_n += 1
    total = pass_n + fail_n
    print(f"\n{pass_n}/{total} PASS")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
