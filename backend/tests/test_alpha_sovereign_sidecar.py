"""Pytest coverage for the sovereign sidecar kit.

These tests must pass alongside the existing 3310+ suite. They mirror the
offline smoke checks plus a couple of additional edge cases.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign.local_state import LocalState, LocalStateError
from sovereign.mc_client import (
    MCContractError,
    auth_headers,
    build_contribution_body,
    contribution_url,
    heartbeat_url,
)
from sovereign.wild_adaptive_core_v2 import (
    ACTION_TO_STANCE,
    BRAIN_NAME,
    LIVE_TRADING_ENABLED,
    SUPPORTED_MODES,
    assert_doctrine,
)


# ── doctrine locks ─────────────────────────────────────────────────────
def test_doctrine_lock_1_constant_is_literal_false() -> None:
    assert LIVE_TRADING_ENABLED is False
    assert isinstance(LIVE_TRADING_ENABLED, bool)


def test_doctrine_lock_2_assert_passes_when_safe() -> None:
    assert_doctrine()


def test_doctrine_lock_2_assert_raises_when_violated(monkeypatch: pytest.MonkeyPatch) -> None:
    import sovereign.wild_adaptive_core_v2 as core
    monkeypatch.setattr(core, "LIVE_TRADING_ENABLED", True)
    with pytest.raises(RuntimeError, match="DOCTRINE VIOLATION"):
        core.assert_doctrine()


def test_action_to_stance_mapping() -> None:
    assert ACTION_TO_STANCE == {"BUY": "long", "SELL": "short", "HOLD": "abstain"}


def test_supported_modes() -> None:
    assert SUPPORTED_MODES == ("DTD", "PRD")
    assert BRAIN_NAME == "alpha"


# ── LocalState ─────────────────────────────────────────────────────────
def test_local_state_initial_defaults(tmp_path: Path) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    assert s.weights == {}
    assert s.learning_rate == 0.0
    assert s.recent_outcomes == []
    assert s.mode == "DTD"


def test_local_state_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    s = LocalState(brain="alpha", path=p, mode="DTD")
    s.set_weights({"trend": 0.85, "macd": 0.65, "rsi": -0.25})
    s.set_learning_rate(0.06)
    s.add_outcome(symbol="BTC/USD", action="BUY", confidence=0.8, outcome=1)
    s.save()

    s2 = LocalState(brain="alpha", path=p, mode="DTD")
    assert s2.weights["trend"] == 0.85
    assert s2.weights["rsi"] == -0.25
    assert s2.learning_rate == 0.06
    assert len(s2.recent_outcomes) == 1
    assert s2.recent_outcomes[0]["symbol"] == "BTC/USD"


def test_local_state_atomic_write_uses_replace(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    s = LocalState(brain="alpha", path=p, mode="DTD")
    s.set_weights({"trend": 0.5})
    s.save()
    # No leftover *.tmp files in the directory.
    leftover = list(tmp_path.glob(".state.*.tmp"))
    assert leftover == [], f"atomic write should clean up tmp files, found: {leftover}"
    assert json.loads(p.read_text())["weights"]["trend"] == 0.5


@pytest.mark.parametrize("bad", [3.5, -3.5, float("inf"), float("nan")])
def test_local_state_rejects_invalid_weights(tmp_path: Path, bad: float) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    with pytest.raises(LocalStateError):
        s.set_weights({"trend": bad})


def test_local_state_rejects_too_many_weights(tmp_path: Path) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    with pytest.raises(LocalStateError):
        s.set_weights({f"k{i}": 0.1 for i in range(17)})


@pytest.mark.parametrize("bad_lr", [-0.01, 0.51, float("inf")])
def test_local_state_rejects_invalid_lr(tmp_path: Path, bad_lr: float) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    with pytest.raises(LocalStateError):
        s.set_learning_rate(bad_lr)


def test_local_state_rejects_bad_mode(tmp_path: Path) -> None:
    with pytest.raises(LocalStateError):
        LocalState(brain="alpha", path=tmp_path / "s.json", mode="WILD")


def test_local_state_outcome_validation(tmp_path: Path) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    with pytest.raises(LocalStateError):
        s.add_outcome(symbol="X", action="YOLO", confidence=0.5, outcome=1)
    with pytest.raises(LocalStateError):
        s.add_outcome(symbol="X", action="BUY", confidence=0.5, outcome=2)
    with pytest.raises(LocalStateError):
        s.add_outcome(symbol="X", action="BUY", confidence=1.5, outcome=1)


def test_local_state_outcomes_trim_to_50(tmp_path: Path) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    for i in range(60):
        s.add_outcome(symbol=f"S{i}", action="BUY", confidence=0.5, outcome=1)
    assert len(s.recent_outcomes) == 50
    # FIFO — oldest dropped.
    assert s.recent_outcomes[0]["symbol"] == "S10"
    assert s.recent_outcomes[-1]["symbol"] == "S59"


# ── mc_client wire shapes ──────────────────────────────────────────────
def test_heartbeat_url() -> None:
    assert heartbeat_url("https://x.com/", "alpha") == "https://x.com/api/heartbeat-ping/alpha"


def test_contribution_url_carries_runtime_query() -> None:
    assert contribution_url("https://x.com", "alpha") == (
        "https://x.com/api/runtime-discussion/sovereign/contribution?runtime=alpha"
    )


def test_auth_header_uses_x_runtime_token_not_bearer() -> None:
    h = auth_headers("abc-123")
    assert h["X-Runtime-Token"] == "abc-123"
    assert "Authorization" not in h
    assert h["Content-Type"] == "application/json"


def test_auth_header_rejects_empty_token() -> None:
    with pytest.raises(MCContractError):
        auth_headers("")


# ── contribution body schema ───────────────────────────────────────────
def test_contribution_body_valid_payload() -> None:
    body = build_contribution_body(
        mode="DTD",
        weights={"trend": 0.85, "macd": 0.65, "rsi": -0.25},
        learning_rate=0.06,
        recent_outcomes=[{
            "symbol": "BTC/USD", "action": "BUY", "confidence": 0.78,
            "outcome": 1, "resolved_at": "2026-02-13T00:55:00+00:00",
            "notional": 0.0,
        }],
        notes="tick @ 0",
    )
    assert body["mode"] == "DTD"
    assert body["live_trading_enabled"] is False
    assert body["weights"]["rsi"] == -0.25
    assert body["learning_rate"] == 0.06
    assert body["confidence_delta"] == 0.0
    assert body["training_signal"] is False
    assert body["delta_reason"] == ""
    assert len(body["recent_outcomes"]) == 1
    assert body["notes"] == "tick @ 0"


def test_contribution_body_live_trading_is_always_false() -> None:
    # There is literally no parameter for live_trading_enabled — it's locked.
    body = build_contribution_body(mode="DTD", weights={"trend": 0.5}, learning_rate=0.0)
    assert body["live_trading_enabled"] is False


def test_contribution_body_rejects_bad_mode() -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(mode="WILD", weights={"trend": 0.5}, learning_rate=0.0)


@pytest.mark.parametrize("bad", [3.5, -3.5, float("inf"), float("nan")])
def test_contribution_body_rejects_bad_weights(bad: float) -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(mode="DTD", weights={"trend": bad}, learning_rate=0.0)


def test_contribution_body_rejects_too_many_weights() -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(
            mode="DTD",
            weights={f"k{i}": 0.1 for i in range(17)},
            learning_rate=0.0,
        )


@pytest.mark.parametrize("bad_lr", [-0.01, 0.51, float("inf")])
def test_contribution_body_rejects_bad_lr(bad_lr: float) -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(mode="DTD", weights={"trend": 0.5}, learning_rate=bad_lr)


def test_contribution_body_rejects_nan_confidence_delta() -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(
            mode="DTD", weights={"trend": 0.5}, learning_rate=0.0,
            confidence_delta=float("nan"),
        )


def test_contribution_body_rejects_training_signal_in_prd() -> None:
    with pytest.raises(MCContractError, match="training_signal"):
        build_contribution_body(
            mode="PRD", weights={"trend": 0.5}, learning_rate=0.0,
            training_signal=True,
        )


def test_contribution_body_allows_training_signal_in_dtd() -> None:
    body = build_contribution_body(
        mode="DTD", weights={"trend": 0.5}, learning_rate=0.0,
        training_signal=True,
    )
    assert body["training_signal"] is True


def test_contribution_body_rejects_bad_outcome_action() -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(
            mode="DTD", weights={"trend": 0.5}, learning_rate=0.0,
            recent_outcomes=[{"symbol": "X", "action": "YOLO", "confidence": 0.5, "outcome": 1}],
        )


def test_contribution_body_rejects_bad_outcome_value() -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(
            mode="DTD", weights={"trend": 0.5}, learning_rate=0.0,
            recent_outcomes=[{"symbol": "X", "action": "BUY", "confidence": 0.5, "outcome": 2}],
        )


def test_contribution_body_rejects_too_many_outcomes() -> None:
    outs = [{"symbol": "X", "action": "BUY", "confidence": 0.5, "outcome": 1} for _ in range(51)]
    with pytest.raises(MCContractError):
        build_contribution_body(
            mode="DTD", weights={"trend": 0.5}, learning_rate=0.0,
            recent_outcomes=outs,
        )


def test_contribution_body_clamps_nothing_locally() -> None:
    # confidence_delta > 0.25 must still be sent — MC server-side clamps it,
    # the client only verifies finite-ness.
    body = build_contribution_body(
        mode="DTD", weights={"trend": 0.5}, learning_rate=0.0,
        confidence_delta=0.9,
    )
    assert body["confidence_delta"] == 0.9


# ── sidecar argparse / seeder helper ───────────────────────────────────
def test_sidecar_parses_default_args() -> None:
    from sovereign.sidecar import _parse_args
    args = _parse_args([])
    assert args.brain == "alpha"
    assert args.mode == "DTD"
    assert args.symbols == ["BTC/USD", "ETH/USD", "SOL/USD"]
    assert args.interval == 60


def test_sidecar_parses_custom_symbols() -> None:
    from sovereign.sidecar import _parse_args
    args = _parse_args(["--symbols", "NVDA", "AMD"])
    assert args.symbols == ["NVDA", "AMD"]


def test_sidecar_seeder_only_runs_when_state_is_empty(tmp_path: Path) -> None:
    from sovereign.sidecar import _seed_if_empty, ALPHA_INITIAL_WEIGHTS
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    assert _seed_if_empty(s) is True
    assert s.weights == ALPHA_INITIAL_WEIGHTS
    # A second call must NOT clobber.
    assert _seed_if_empty(s) is False
