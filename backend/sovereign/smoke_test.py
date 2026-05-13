"""Offline doctrine smoke test — runs WITHOUT touching Mission Control.

Run::

    python3 -m sovereign.smoke_test
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
    default_weights,
    map_action_to_stance,
    run_adaptive_core,
    update_weights,
)


def _check(name: str, fn) -> tuple[str, bool, str]:
    try:
        fn()
        return name, True, "ok"
    except Exception as e:  # noqa: BLE001
        return name, False, f"{type(e).__name__}: {e}"


def test_doctrine_constant_is_literally_false() -> None:
    assert LIVE_TRADING_ENABLED is False, "lock #1: must be the literal False"
    assert_doctrine()


def test_default_weights_is_alpha_personality() -> None:
    w = default_weights()
    assert w == {"trend": 0.85, "macd": 0.65, "rsi": -0.25}


def test_local_state_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state.json"
        s1 = LocalState(brain="alpha", path=p, mode="DTD")
        assert s1.weights == {}
        s1.set_weights(default_weights())
        s1.set_learning_rate(0.06)
        s1.save()

        s2 = LocalState(brain="alpha", path=p, mode="DTD")
        assert s2.weights == default_weights()
        assert s2.learning_rate == 0.06
        assert s2.recent_outcomes() == []
        assert s2.decisions() == []


def test_local_state_rejects_bad_inputs() -> None:
    with tempfile.TemporaryDirectory() as d:
        s = LocalState(brain="alpha", path=Path(d) / "s.json", mode="DTD")
        try:
            s.set_weights({"trend": 5.0})
        except LocalStateError:
            pass
        else:
            raise AssertionError("expected LocalStateError for weight > 3.0")
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
        weights=default_weights(),
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


def test_adaptive_core_is_deterministic() -> None:
    top = {
        "symbol": "BTC/USD",
        "price": 105.0,
        "technicals": {"sma20": 100.0, "macd": 0.4, "rsi14": 60.0},
    }
    d1 = run_adaptive_core(top, default_weights())
    d2 = run_adaptive_core(top, default_weights())
    assert d1.symbol == "BTC/USD"
    assert d1.action in {"BUY", "SELL", "HOLD"}
    assert d1.score == d2.score and d1.action == d2.action and d1.confidence == d2.confidence
    assert map_action_to_stance(d1.action) in {"long", "short", "abstain"}


def test_update_weights_clamps_and_is_pure() -> None:
    w0 = default_weights()
    w1 = update_weights(w0, {"trend": 0.5, "macd": 0.2, "rsi": -0.1}, outcome=1, lr=0.06)
    # Pure: original untouched.
    assert w0 == default_weights()
    # Trend pushed up because outcome=+1 and feature positive.
    assert w1["trend"] > w0["trend"]
    # Clamping: extreme features can't push outside [-3, 3].
    w_extreme = update_weights({"x": 2.99}, {"x": 100.0}, outcome=1, lr=0.5)
    assert w_extreme["x"] == 3.0


CHECKS = [
    ("lock #1+#2 — LIVE_TRADING_ENABLED is False & assert_doctrine passes", test_doctrine_constant_is_literally_false),
    ("default_weights is Alpha's trend-follower bias", test_default_weights_is_alpha_personality),
    ("LocalState roundtrip + decisions()/recent_outcomes() methods", test_local_state_roundtrip),
    ("LocalState rejects out-of-range weights + bad mode", test_local_state_rejects_bad_inputs),
    ("mc_client URLs + X-Runtime-Token header (no Bearer)", test_mc_client_url_and_auth_shapes),
    ("contribution body strict schema (live_trading_enabled hard-locked False)", test_contribution_body_strict_schema),
    ("adaptive core is deterministic + maps to stance", test_adaptive_core_is_deterministic),
    ("update_weights is pure, clamped, and gradient-correct", test_update_weights_clamps_and_is_pure),
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
