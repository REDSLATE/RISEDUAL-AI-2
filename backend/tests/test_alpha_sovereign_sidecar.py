"""Pytest coverage for the sovereign sidecar kit.

These tests must pass alongside the existing 3310+ suite.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sovereign.local_state import LocalState, LocalStateError
from sovereign.mc_client import (
    MCClient,
    MCClientError,
    MCContractError,
    auth_headers,
    build_contribution_body,
    build_stance_body,
    contribution_url,
    heartbeat_url,
    stance_url,
)
from sovereign.wild_adaptive_core_v2 import (
    ACTION_TO_STANCE,
    BRAIN_NAME,
    LIVE_TRADING_ENABLED,
    SUPPORTED_MODES,
    Decision,
    asdict,
    assert_doctrine,
    assert_safe_action,
    default_weights,
    extract_features,
    map_action_to_stance,
    run_adaptive_core,
    update_weights,
)


# ── doctrine locks ─────────────────────────────────────────────────────
def test_doctrine_lock_1_constant_is_literal_false() -> None:
    assert LIVE_TRADING_ENABLED is False
    assert isinstance(LIVE_TRADING_ENABLED, bool)


def test_doctrine_lock_2_assert_passes_when_safe() -> None:
    assert_doctrine()


def test_doctrine_lock_2_assert_is_now_noop_under_v3(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCTRINE V3 (2026-05-13): ``assert_doctrine`` was retired when
    RISEDUAL became a headless brain. It remains importable for
    back-compat but is a no-op — flipping ``LIVE_TRADING_ENABLED`` does
    not raise."""
    import sovereign.wild_adaptive_core_v2 as core
    monkeypatch.setattr(core, "LIVE_TRADING_ENABLED", True)
    # Must NOT raise under V3.
    core.assert_doctrine()


def test_assert_safe_action_accepts_known_actions() -> None:
    for a in ("BUY", "SELL", "HOLD"):
        assert_safe_action(a)


def test_assert_safe_action_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        assert_safe_action("YOLO")


def test_assert_safe_action_is_vocabulary_only_under_v3(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCTRINE V3: ``assert_safe_action`` only checks action vocabulary.
    The previous live-trading guard was retired."""
    import sovereign.wild_adaptive_core_v2 as core
    monkeypatch.setattr(core, "LIVE_TRADING_ENABLED", True)
    # Vocabulary check still runs — bad action still raises.
    with pytest.raises(ValueError):
        core.assert_safe_action("YOLO")
    # But a valid action with LIVE_TRADING_ENABLED flipped no longer raises.
    core.assert_safe_action("BUY")


def test_action_to_stance_mapping() -> None:
    assert ACTION_TO_STANCE == {"BUY": "long", "SELL": "short", "HOLD": "abstain"}
    assert map_action_to_stance("BUY") == "long"
    assert map_action_to_stance("SELL") == "short"
    assert map_action_to_stance("HOLD") == "abstain"
    with pytest.raises(ValueError):
        map_action_to_stance("YOLO")


def test_supported_modes_and_brain_name() -> None:
    assert SUPPORTED_MODES == ("DTD", "PRD")
    assert BRAIN_NAME == "alpha"


# ── adaptive core ──────────────────────────────────────────────────────
def test_default_weights_alpha_personality() -> None:
    assert default_weights() == {"trend": 0.85, "macd": 0.65, "rsi": -0.25}


def test_extract_features_basic() -> None:
    feats = extract_features({
        "symbol": "BTC/USD",
        "price": 110.0,
        "technicals": {"sma20": 100.0, "macd": 0.4, "rsi14": 65.0},
    })
    assert feats["trend"] == pytest.approx(0.10)
    assert feats["macd"] == pytest.approx(0.4)
    assert feats["rsi"] == pytest.approx(0.30)


def test_extract_features_handles_missing_sma() -> None:
    feats = extract_features({"price": 100.0, "technicals": {}})
    assert feats == {"trend": 0.0, "macd": 0.0, "rsi": 0.0}


def test_extract_features_handles_non_mapping() -> None:
    with pytest.raises(ValueError):
        extract_features("not-a-dict")  # type: ignore[arg-type]


def test_extract_features_replaces_nan() -> None:
    feats = extract_features({
        "price": float("inf"),
        "technicals": {"sma20": 0.0, "macd": float("nan"), "rsi14": 50.0},
    })
    assert all(isinstance(v, float) for v in feats.values())
    assert all(v == v for v in feats.values())  # no NaN


def test_run_adaptive_core_deterministic() -> None:
    top = {
        "symbol": "BTC/USD", "price": 110.0,
        "technicals": {"sma20": 100.0, "macd": 0.4, "rsi14": 60.0},
    }
    d1 = run_adaptive_core(top, default_weights())
    d2 = run_adaptive_core(top, default_weights())
    assert d1.symbol == d2.symbol == "BTC/USD"
    assert d1.action == d2.action
    assert d1.score == d2.score
    assert d1.confidence == d2.confidence
    assert d1.features == d2.features
    assert d1.confidence_origin == d2.confidence_origin


def test_run_adaptive_core_buy_on_strong_uptrend() -> None:
    top = {
        "symbol": "BTC/USD", "price": 120.0,
        "technicals": {"sma20": 100.0, "macd": 0.5, "rsi14": 65.0},
    }
    d = run_adaptive_core(top, default_weights())
    assert d.action == "BUY"
    assert 0.0 < d.confidence <= 1.0


def test_run_adaptive_core_sell_on_strong_downtrend() -> None:
    # Strong negative trend → SELL.
    top = {
        "symbol": "BTC/USD", "price": 80.0,
        "technicals": {"sma20": 100.0, "macd": -0.5, "rsi14": 35.0},
    }
    d = run_adaptive_core(top, default_weights())
    assert d.action == "SELL"


def test_run_adaptive_core_hold_when_flat() -> None:
    top = {
        "symbol": "BTC/USD", "price": 100.0,
        "technicals": {"sma20": 100.0, "macd": 0.0, "rsi14": 50.0},
    }
    d = run_adaptive_core(top, default_weights())
    assert d.action == "HOLD"
    assert d.confidence <= 0.25


def test_run_adaptive_core_decision_is_dataclass_serializable() -> None:
    top = {"symbol": "BTC/USD", "price": 110.0, "technicals": {"sma20": 100.0}}
    d = run_adaptive_core(top, default_weights())
    payload = asdict(d)
    assert isinstance(payload, dict)
    assert payload["symbol"] == "BTC/USD"
    assert "features" in payload
    assert "confidence_origin" in payload
    assert isinstance(d, Decision)


def test_update_weights_pure() -> None:
    w0 = default_weights()
    update_weights(w0, {"trend": 0.5}, 1, lr=0.06)
    assert w0 == default_weights()


def test_update_weights_correct_direction() -> None:
    # Outcome=+1 with positive feature → push weight UP.
    w = update_weights({"trend": 0.5}, {"trend": 0.3}, outcome=1, lr=0.1)
    assert w["trend"] > 0.5
    # Outcome=-1 with positive feature → push weight DOWN.
    w2 = update_weights({"trend": 0.5}, {"trend": 0.3}, outcome=-1, lr=0.1)
    assert w2["trend"] < 0.5
    # Outcome=0 → no change.
    w3 = update_weights({"trend": 0.5}, {"trend": 0.3}, outcome=0, lr=0.1)
    assert w3["trend"] == 0.5


def test_update_weights_clamps_to_schema_bounds() -> None:
    w = update_weights({"trend": 2.95}, {"trend": 100.0}, outcome=1, lr=0.5)
    assert w["trend"] == 3.0
    w2 = update_weights({"trend": -2.95}, {"trend": 100.0}, outcome=-1, lr=0.5)
    assert w2["trend"] == -3.0


def test_update_weights_rejects_bad_outcome() -> None:
    with pytest.raises(ValueError):
        update_weights({"trend": 0.5}, {"trend": 0.3}, outcome=2)


def test_update_weights_rejects_bad_lr() -> None:
    with pytest.raises(ValueError):
        update_weights({"trend": 0.5}, {"trend": 0.3}, outcome=1, lr=0.6)


# ── LocalState ─────────────────────────────────────────────────────────
def test_local_state_initial_defaults(tmp_path: Path) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    assert s.weights == {}
    assert s.learning_rate == 0.0
    assert s.recent_outcomes() == []
    assert s.decisions() == []
    assert s.mode == "DTD"


def test_local_state_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    s = LocalState(brain="alpha", path=p, mode="DTD")
    s.set_weights(default_weights())
    s.set_learning_rate(0.06)
    s.add_outcome(symbol="BTC/USD", action="BUY", confidence=0.8, outcome=1)
    s.append_decision({"symbol": "BTC/USD", "action": "BUY", "confidence": 0.8})
    s.save()

    s2 = LocalState(brain="alpha", path=p, mode="DTD")
    assert s2.weights["trend"] == 0.85
    assert s2.weights["rsi"] == -0.25
    assert s2.learning_rate == 0.06
    assert len(s2.recent_outcomes()) == 1
    assert s2.recent_outcomes()[0]["symbol"] == "BTC/USD"
    assert len(s2.decisions()) == 1


def test_local_state_atomic_write(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    s = LocalState(brain="alpha", path=p, mode="DTD")
    s.set_weights({"trend": 0.5})
    s.save()
    leftover = list(tmp_path.glob(".state.*.tmp"))
    assert leftover == [], f"atomic write should clean up tmp files, found: {leftover}"
    assert json.loads(p.read_text())["weights"]["trend"] == 0.5


def test_local_state_back_compat_loads_recent_outcomes_key(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    # Simulate older state file format.
    p.write_text(json.dumps({
        "brain": "alpha", "mode": "DTD", "weights": {"trend": 0.5},
        "learning_rate": 0.06,
        "recent_outcomes": [
            {"symbol": "X", "action": "BUY", "confidence": 0.5, "outcome": 1,
             "resolved_at": "2026-01-01T00:00:00+00:00", "notional": 0.0}
        ],
    }))
    s = LocalState(brain="alpha", path=p, mode="DTD")
    assert len(s.recent_outcomes()) == 1


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
    assert len(s.recent_outcomes()) == 50
    assert s.recent_outcomes()[0]["symbol"] == "S10"
    assert s.recent_outcomes()[-1]["symbol"] == "S59"


def test_local_state_recent_outcomes_limit(tmp_path: Path) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    for i in range(10):
        s.add_outcome(symbol=f"S{i}", action="BUY", confidence=0.5, outcome=1)
    assert len(s.recent_outcomes(3)) == 3
    assert s.recent_outcomes(3)[-1]["symbol"] == "S9"
    assert s.recent_outcomes(0) == []


def test_local_state_decisions_trim_and_limit(tmp_path: Path) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    for i in range(220):
        s.append_decision({"symbol": f"S{i}", "action": "BUY"})
    assert len(s.decisions()) == 200  # cap
    assert s.decisions()[-1]["symbol"] == "S219"
    assert len(s.decisions(5)) == 5


def test_local_state_rejects_non_mapping_decision(tmp_path: Path) -> None:
    s = LocalState(brain="alpha", path=tmp_path / "s.json", mode="DTD")
    with pytest.raises(LocalStateError):
        s.append_decision("not-a-dict")  # type: ignore[arg-type]


# ── mc_client wire shapes ──────────────────────────────────────────────
def test_heartbeat_url() -> None:
    assert heartbeat_url("https://x.com/", "alpha") == "https://x.com/api/heartbeat-ping/alpha"


def test_contribution_url_carries_runtime_query() -> None:
    assert contribution_url("https://x.com", "alpha") == (
        "https://x.com/api/runtime-discussion/sovereign/contribution?runtime=alpha"
    )


def test_stance_url_format() -> None:
    assert stance_url("https://x.com", "pos123", "alpha") == (
        "https://x.com/api/runtime-discussion/positions/pos123/stance?runtime=alpha"
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
def test_contribution_body_valid() -> None:
    body = build_contribution_body(
        mode="DTD",
        weights=default_weights(),
        learning_rate=0.06,
        recent_outcomes=[{
            "symbol": "BTC/USD", "action": "BUY", "confidence": 0.78,
            "outcome": 1, "resolved_at": "2026-02-13T00:55:00+00:00",
            "notional": 0.0,
        }],
        notes="tick @ 0",
    )
    assert body["mode"] == "DTD"
    assert body["live_trading_enabled"] is True  # 2026-05-17 open-trading override
    assert body["weights"]["rsi"] == -0.25
    assert body["training_signal"] is False
    assert len(body["recent_outcomes"]) == 1


def test_contribution_body_live_trading_always_true_under_open_trading() -> None:
    """2026-05-17 operator override: under the open-trading regime the
    brain advertises live trading capability. MC remains executor."""
    body = build_contribution_body(mode="DTD", weights={"trend": 0.5}, learning_rate=0.0)
    assert body["live_trading_enabled"] is True


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
            mode="DTD", weights={f"k{i}": 0.1 for i in range(17)}, learning_rate=0.0,
        )


def test_contribution_body_rejects_training_signal_in_prd() -> None:
    with pytest.raises(MCContractError, match="training_signal"):
        build_contribution_body(
            mode="PRD", weights={"trend": 0.5}, learning_rate=0.0,
            training_signal=True,
        )


def test_contribution_body_allows_training_signal_in_dtd() -> None:
    body = build_contribution_body(
        mode="DTD", weights={"trend": 0.5}, learning_rate=0.0, training_signal=True,
    )
    assert body["training_signal"] is True


def test_contribution_body_rejects_nan_confidence_delta() -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(
            mode="DTD", weights={"trend": 0.5}, learning_rate=0.0,
            confidence_delta=float("nan"),
        )


def test_contribution_body_rejects_bad_outcome_action() -> None:
    with pytest.raises(MCContractError):
        build_contribution_body(
            mode="DTD", weights={"trend": 0.5}, learning_rate=0.0,
            recent_outcomes=[{"symbol": "X", "action": "YOLO", "confidence": 0.5, "outcome": 1}],
        )


def test_contribution_body_rejects_too_many_outcomes() -> None:
    outs = [{"symbol": "X", "action": "BUY", "confidence": 0.5, "outcome": 1} for _ in range(51)]
    with pytest.raises(MCContractError):
        build_contribution_body(
            mode="DTD", weights={"trend": 0.5}, learning_rate=0.0, recent_outcomes=outs,
        )


# ── stance body schema ─────────────────────────────────────────────────
def test_stance_body_valid() -> None:
    body = build_stance_body(
        stance="long", confidence=0.8, notes="why",
        memory_sources=["sovereign.weights_snapshot"],
        confidence_origin={"trend": 0.5, "macd": 0.3, "rsi": -0.05},
    )
    assert body["stance"] == "long"
    assert body["confidence"] == 0.8
    assert body["memory_sources"] == ["sovereign.weights_snapshot"]
    assert body["confidence_origin"]["trend"] == 0.5


def test_stance_body_rejects_bad_stance() -> None:
    with pytest.raises(MCContractError):
        build_stance_body(stance="BUY", confidence=0.5)  # must use long/short/abstain


def test_stance_body_rejects_bad_confidence() -> None:
    with pytest.raises(MCContractError):
        build_stance_body(stance="long", confidence=1.5)


# ── MCClient construction ─────────────────────────────────────────────
def test_mc_client_construct_validates_inputs() -> None:
    with pytest.raises(MCContractError):
        MCClient(base_url="", brain="alpha", runtime_token="t")
    with pytest.raises(MCContractError):
        MCClient(base_url="x", brain="alpha", runtime_token="")
    with pytest.raises(MCContractError):
        MCClient(base_url="x", brain="", runtime_token="t")
    # Valid construction does not raise.
    c = MCClient(base_url="https://x", brain="alpha", runtime_token="t")
    c.close()


def test_mc_client_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("ALPHA_INGEST_TOKEN", "tok-abc")
    c = MCClient.from_env("alpha")
    assert c.base_url == "https://example.invalid"
    assert c.token == "tok-abc"
    assert c.brain == "alpha"
    c.close()


# ── SovereignSidecar tick (offline, with a fake top-of-book) ──────────
def test_sidecar_tick_runs_without_mc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end tick using a stub feed + a fake MCClient that records calls."""
    import sovereign.sidecar as sc

    state_path = tmp_path / "alpha.json"

    class _FakeClient:
        def __init__(self, **_kw):
            self.contributions: list[dict] = []
            self.heartbeats: int = 0
            self.stances: list[dict] = []

        def post_contribution(self, **kwargs):
            self.contributions.append(kwargs)
            return {"posted_as": "executor", "seat_epoch": 1, "updated_at": "now"}

        def post_stance(self, **kwargs):
            self.stances.append(kwargs)
            return {"ok": True}

        def heartbeat(self, *_a, **_kw):
            self.heartbeats += 1
            return {"ok": True}

        def close(self):
            pass

    monkeypatch.setattr(sc, "MCClient", _FakeClient)
    # 2026-06-23: standalone mode picks LocalMCClient over MCClient.
    # Patch both so the fake is chosen regardless of the env flag.
    monkeypatch.setattr(sc, "LocalMCClient", _FakeClient)
    monkeypatch.setattr(sc, "is_standalone_mode", lambda: False)

    side = sc.SovereignSidecar(
        brain="alpha", mode="DTD", mc_base_url="https://x",
        runtime_token="t", symbols=["BTC/USD"], state_path=state_path,
    )
    # Seeded with default_weights() automatically.
    assert side.state.weights == default_weights()

    # 2026-05-22: under the empty-contribution refusal doctrine, the
    # sidecar will NOT post a contribution unless `recent_outcomes` is
    # non-empty. Seed one outcome so the post-path is exercised.
    side.state.add_outcome(
        symbol="BTC/USD", action="BUY", confidence=0.6,
        outcome=1, notional=0.0,
    )

    side.tick()

    fake: _FakeClient = side.client  # type: ignore[assignment]
    assert len(fake.contributions) == 1
    contrib = fake.contributions[0]
    assert contrib["mode"] == "DTD"
    assert contrib["training_signal"] is False
    # 2026-05-14: heartbeat moved out of tick() into its own daemon
    # thread to decouple from contribution path. tick() no longer
    # publishes a heartbeat; the heartbeat thread does (and is verified
    # in test_heartbeat_thread_publishes_independently below).
    assert fake.heartbeats == 0
    # No active_position_resolver → no stance posted.
    assert fake.stances == []
    # Decision recorded locally.
    assert len(side.state.decisions()) == 1


def test_heartbeat_thread_publishes_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heartbeat daemon thread must publish heartbeats on its own
    schedule, separate from tick(). This guards against the 2026-05-14
    silent-freeze where a hung contribution starved the heartbeat path."""
    import sovereign.sidecar as sc

    state_path = tmp_path / "alpha.json"

    class _FakeClient:
        def __init__(self, **_kw):
            self.contributions: list[dict] = []
            self.heartbeats: int = 0
            self.stances: list[dict] = []

        def post_contribution(self, **kwargs):
            self.contributions.append(kwargs)
            return {"posted_as": "executor", "seat_epoch": 1, "updated_at": "now"}

        def post_stance(self, **kwargs):
            self.stances.append(kwargs)
            return {"ok": True}

        def heartbeat(self, *_a, **_kw):
            self.heartbeats += 1
            return {"ok": True}

        def close(self):
            pass

    monkeypatch.setattr(sc, "MCClient", _FakeClient)
    # 2026-06-23: standalone mode picks LocalMCClient over MCClient.
    # Patch both so the fake is chosen regardless of the env flag.
    monkeypatch.setattr(sc, "LocalMCClient", _FakeClient)
    monkeypatch.setattr(sc, "is_standalone_mode", lambda: False)

    side = sc.SovereignSidecar(
        brain="alpha", mode="DTD", mc_base_url="https://x",
        runtime_token="t", symbols=["BTC/USD"], state_path=state_path,
    )
    # Drive the heartbeat loop directly with a short interval + a stop
    # event we trigger after ~2 cycles. Avoids fragile real-time sleeps.
    import threading
    stop = threading.Event()

    def _hb_runner() -> None:
        side._heartbeat_loop(interval_seconds=1, stop=stop)

    hb_thread = threading.Thread(target=_hb_runner, daemon=True)
    hb_thread.start()
    # Let the loop fire at least one heartbeat then stop it.
    time.sleep(0.2)
    stop.set()
    hb_thread.join(timeout=2.0)
    assert not hb_thread.is_alive(), "heartbeat thread failed to stop"

    hb_client: _FakeClient = side._hb_client  # type: ignore[assignment]
    assert hb_client.heartbeats >= 1
    # Heartbeat client is a SEPARATE instance from the tick client so a
    # hung contribution can't starve it.
    assert side._hb_client is not side.client


def test_watchdog_exits_on_stale_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the main loop hasn't advanced _last_tick_at within the limit,
    watchdog must call os._exit so supervisor can respawn us."""
    import sovereign.sidecar as sc

    state_path = tmp_path / "alpha.json"
    monkeypatch.setattr(sc, "MCClient", lambda **_kw: type("C", (), {"close": lambda s: None})())  # noqa: E731

    side = sc.SovereignSidecar(
        brain="alpha", mode="DTD", mc_base_url="https://x",
        runtime_token="t", symbols=["BTC/USD"], state_path=state_path,
    )
    # Force the last tick into the distant past so the watchdog trips.
    side._last_tick_at = time.time() - 9999

    exit_calls: list[int] = []
    monkeypatch.setattr(sc.os, "_exit", lambda code: exit_calls.append(code))

    import threading
    stop = threading.Event()

    def _wd_runner() -> None:
        side._watchdog_loop(max_stale_seconds=1, stop=stop)

    wd_thread = threading.Thread(target=_wd_runner, daemon=True)
    wd_thread.start()
    time.sleep(0.2)
    stop.set()
    wd_thread.join(timeout=2.0)

    assert exit_calls == [2]


def test_sidecar_apply_outcome_dtd_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sovereign.sidecar as sc

    monkeypatch.setattr(sc, "MCClient", lambda **_kw: type("C", (), {"close": lambda s: None})())  # noqa: E731

    side = sc.SovereignSidecar(
        brain="alpha", mode="DTD", mc_base_url="https://x",
        runtime_token="t", symbols=["BTC/USD"], state_path=tmp_path / "s.json",
    )
    # The sidecar seeds default weights but NOT learning_rate; bootstrap
    # does that in production. Mirror that here.
    side.state.set_learning_rate(0.06)
    w_before = dict(side.state.weights)
    side.apply_outcome({"features": {"trend": 0.3}}, outcome=1)
    assert side.state.weights["trend"] > w_before["trend"]

    # PRD mode refuses.
    side.state.set_mode("PRD")
    with pytest.raises(RuntimeError, match="refusing to retrain"):
        side.apply_outcome({"features": {"trend": 0.3}}, outcome=1)
