"""Tests for the lane-separated executor (equity vs crypto).

Verifies the four lane-contract pillars:

1. **Lane detection** — ``detect_lane`` resolves correctly from
   ``asset_type``, ``lane`` override, or symbol heuristic.
2. **Equity has the market-hours gate.** A signal arriving outside
   the regular session returns ``MARKET_CLOSED`` without hitting
   the broker.
3. **Crypto has NO market-hours gate.** Same off-hours timestamp
   that vetoes equity must let crypto through.
4. **Per-lane Fast Veto thresholds apply.** A 100-bps spread
   passes crypto (its threshold is 150) but vetoes equity (its
   threshold is 50).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.executors import (
    CRYPTO_LANE,
    EQUITY_LANE,
    detect_lane,
    is_equity_market_open,
    lane_config_for,
)
from services import fast_veto_layer


# ── 1. Lane detection ───────────────────────────────────────────


def test_detect_lane_explicit_asset_type_crypto():
    assert detect_lane({"asset_type": "crypto", "symbol": "BTC-USD"}) == "crypto"


def test_detect_lane_explicit_asset_type_stock():
    assert detect_lane({"asset_type": "stock", "symbol": "AAPL"}) == "equity"


def test_detect_lane_explicit_lane_override_wins():
    """``lane`` field beats ``asset_type`` so a manual override
    works without changing the inbound signal payload."""
    assert detect_lane(
        {"lane": "crypto", "asset_type": "stock", "symbol": "AAPL"}
    ) == "crypto"


def test_detect_lane_symbol_heuristic_btc_usd():
    assert detect_lane({"symbol": "BTC-USD"}) == "crypto"


def test_detect_lane_symbol_heuristic_ethusd():
    assert detect_lane({"symbol": "ETHUSDT"}) == "crypto"


def test_detect_lane_default_when_unknown():
    """Unknown signals default to equity — market-hours gate kicks
    in as a safety net for misclassified payloads."""
    assert detect_lane({"symbol": "AAPL"}) == "equity"
    assert detect_lane({}) == "equity"
    assert detect_lane(None) == "equity"  # type: ignore[arg-type]


def test_detect_lane_invalid_lane_string_falls_through():
    """A bogus ``lane`` value must not be honoured. Falls through
    to asset_type / heuristic."""
    assert detect_lane(
        {"lane": "futures", "asset_type": "crypto"}
    ) == "crypto"


# ── 2. Lane configs ────────────────────────────────────────────


def test_lane_config_equity_has_market_hours_required():
    cfg = lane_config_for("equity")
    assert cfg.market_hours_required is True
    assert cfg.name == "equity"


def test_lane_config_crypto_no_market_hours():
    cfg = lane_config_for("crypto")
    assert cfg.market_hours_required is False
    assert cfg.name == "crypto"


def test_lane_config_unknown_defaults_to_equity():
    """Defensive — typo in caller should not silently bypass the
    market-hours gate."""
    cfg = lane_config_for("forex")
    assert cfg.name == "equity"
    assert cfg.market_hours_required is True


def test_lane_config_thresholds_disjoint():
    """Equity is tighter than crypto across every shared key. If
    this regresses, someone has crossed the lanes."""
    eq = EQUITY_LANE.fast_veto_thresholds
    cr = CRYPTO_LANE.fast_veto_thresholds
    assert eq["spread_bps_max"] < cr["spread_bps_max"]
    assert eq["liquidity_score_min"] > cr["liquidity_score_min"]
    assert eq["drawdown_pct_max"] <= cr["drawdown_pct_max"]


# ── 3. Per-lane Fast Veto threshold application ─────────────────


def test_equity_thresholds_veto_50bps_spread():
    """100 bps spread → equity vetoes (threshold 50)."""
    r = fast_veto_layer.evaluate_fast_veto(
        signal={"confidence": 0.85},
        market_state={"spread_bps": 100.0, "drawdown_pct": 0.0,
                      "volatility_score": 0.3, "liquidity_score": 0.95},
        thresholds=EQUITY_LANE.fast_veto_thresholds,
    )
    assert r.would_veto is True
    assert r.reason == "FAST_VETO_WIDE_SPREAD"


def test_crypto_thresholds_pass_100bps_spread():
    """100 bps spread → crypto passes (threshold 150)."""
    r = fast_veto_layer.evaluate_fast_veto(
        signal={"confidence": 0.85},
        market_state={"spread_bps": 100.0, "drawdown_pct": 0.0,
                      "volatility_score": 0.3, "liquidity_score": 0.95},
        thresholds=CRYPTO_LANE.fast_veto_thresholds,
    )
    assert r.would_veto is False
    assert r.reason == "FAST_VETO_PASS_SHADOW"


def test_default_thresholds_unchanged_when_none():
    """Backward-compat — passing ``thresholds=None`` produces the
    same verdict as the historical hard-coded values."""
    r1 = fast_veto_layer.evaluate_fast_veto(
        signal={"confidence": 0.85},
        market_state={"spread_bps": 80.0, "drawdown_pct": 0.0,
                      "volatility_score": 0.3, "liquidity_score": 0.95},
    )
    r2 = fast_veto_layer.evaluate_fast_veto(
        signal={"confidence": 0.85},
        market_state={"spread_bps": 80.0, "drawdown_pct": 0.0,
                      "volatility_score": 0.3, "liquidity_score": 0.95},
        thresholds=None,
    )
    assert r1.reason == r2.reason
    assert r1.reason == "FAST_VETO_WIDE_SPREAD"  # 80 ≥ 75 default


def test_crypto_thresholds_relax_drawdown_cap():
    """12% drawdown → equity vetoes (cap 10), crypto passes (cap 15)."""
    payload = {
        "signal": {"confidence": 0.85},
        "market_state": {"spread_bps": 5.0, "drawdown_pct": 12.0,
                         "volatility_score": 0.3, "liquidity_score": 0.95},
    }
    eq = fast_veto_layer.evaluate_fast_veto(
        **payload, thresholds=EQUITY_LANE.fast_veto_thresholds
    )
    cr = fast_veto_layer.evaluate_fast_veto(
        **payload, thresholds=CRYPTO_LANE.fast_veto_thresholds
    )
    assert eq.would_veto is True
    assert eq.reason == "FAST_VETO_DRAWDOWN_BREACH"
    assert cr.would_veto is False


# ── 4. Equity lane — market-hours gate ─────────────────────────


@pytest.mark.asyncio
async def test_equity_executor_skips_when_market_closed(monkeypatch):
    """Market-closed → returns MARKET_CLOSED skip without touching
    kill-switch, fast-veto, or broker."""
    from services.executors import equity_executor as eq_mod

    # Force market closed
    monkeypatch.setattr(eq_mod, "is_equity_market_open", lambda *a, **k: False)

    # If any downstream code is invoked, this would explode — proves
    # the gate short-circuits before any other work.
    monkeypatch.setattr(
        "services.trading_bot_service.execute_signal",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not reach core when market closed"),
        ),
    )

    result = await eq_mod.execute_equity_signal(
        signal={"symbol": "AAPL", "confidence": 0.85},
        market_data={},
        tier3_readiness={},
        config={"trade_size": 100},
    )
    assert result == {
        "skipped": True,
        "reason": "MARKET_CLOSED",
        "lane": "equity",
    }


@pytest.mark.asyncio
async def test_equity_executor_calls_core_when_market_open(monkeypatch):
    from services.executors import equity_executor as eq_mod

    monkeypatch.setattr(eq_mod, "is_equity_market_open", lambda *a, **k: True)

    captured: dict = {}

    async def _fake_core(**kwargs):
        captured.update(kwargs)
        return {"order": "fake", "lane": kwargs.get("lane")}

    monkeypatch.setattr(
        "services.trading_bot_service.execute_signal", _fake_core,
    )

    result = await eq_mod.execute_equity_signal(
        signal={"symbol": "AAPL", "confidence": 0.85},
        market_data={"spread_bps": 5.0},
        tier3_readiness={},
        config={"trade_size": 100},
    )
    assert captured.get("lane") == "equity"
    assert result == {"order": "fake", "lane": "equity"}


# ── 5. Crypto lane — no market-hours gate ──────────────────────


@pytest.mark.asyncio
async def test_crypto_executor_does_not_check_market_hours(monkeypatch):
    """Even when ``is_equity_market_open`` is False, the crypto
    lane proceeds. Locks the 24/7 contract."""
    from services.executors import crypto_executor as cr_mod

    captured: dict = {}

    async def _fake_core(**kwargs):
        captured.update(kwargs)
        return {"order": "fake", "lane": kwargs.get("lane")}

    monkeypatch.setattr(
        "services.trading_bot_service.execute_signal", _fake_core,
    )

    # Sanity-check: even if a future code rev imports the equity
    # gate into this module by accident, we'd rather break the
    # test than silently bypass.
    assert not hasattr(cr_mod, "is_equity_market_open")

    result = await cr_mod.execute_crypto_signal(
        signal={"symbol": "BTC-USD", "asset_type": "crypto", "confidence": 0.85},
        market_data={},
        tier3_readiness={},
        config={"trade_size": 100},
    )
    assert captured.get("lane") == "crypto"
    assert result == {"order": "fake", "lane": "crypto"}


# ── 6. Router behaviour — execute_signal(lane=None) dispatches ──


@pytest.mark.asyncio
async def test_router_dispatches_crypto_signal_to_crypto_lane(monkeypatch):
    """Calling ``execute_signal`` with no ``lane`` and a crypto
    signal must route through ``execute_crypto_signal``."""
    from services import trading_bot_service as tbs

    crypto_calls: list[str] = []

    async def _fake_crypto(**kwargs):
        crypto_calls.append(kwargs["signal"]["symbol"])
        return {"order": "crypto-fake", "lane": "crypto"}

    monkeypatch.setattr(
        "services.executors.crypto_executor.execute_crypto_signal",
        _fake_crypto,
    )
    # We don't expect equity executor to be called — make it loud
    # if the router gets it wrong.
    async def _fail_equity(**kwargs):
        raise AssertionError("crypto signal incorrectly routed to equity")

    monkeypatch.setattr(
        "services.executors.equity_executor.execute_equity_signal",
        _fail_equity,
    )

    result = await tbs.execute_signal(
        signal={"asset_type": "crypto", "symbol": "BTC-USD", "confidence": 0.85},
        market_data={},
        tier3_readiness={},
        config={"trade_size": 100},
    )
    assert crypto_calls == ["BTC-USD"]
    assert result["lane"] == "crypto"


@pytest.mark.asyncio
async def test_router_dispatches_equity_signal_to_equity_lane(monkeypatch):
    from services import trading_bot_service as tbs

    equity_calls: list[str] = []

    async def _fake_equity(**kwargs):
        equity_calls.append(kwargs["signal"]["symbol"])
        return {"order": "equity-fake", "lane": "equity"}

    monkeypatch.setattr(
        "services.executors.equity_executor.execute_equity_signal",
        _fake_equity,
    )
    async def _fail_crypto(**kwargs):
        raise AssertionError("equity signal incorrectly routed to crypto")

    monkeypatch.setattr(
        "services.executors.crypto_executor.execute_crypto_signal",
        _fail_crypto,
    )

    result = await tbs.execute_signal(
        signal={"asset_type": "stock", "symbol": "AAPL", "confidence": 0.85},
        market_data={},
        tier3_readiness={},
        config={"trade_size": 100},
    )
    assert equity_calls == ["AAPL"]
    assert result["lane"] == "equity"


@pytest.mark.asyncio
async def test_router_skipped_when_lane_already_set(monkeypatch):
    """When a caller sets ``lane=`` explicitly (the lane modules do
    this), the router must NOT re-dispatch — that would create an
    infinite loop. Instead the body runs straight through with the
    pre-set lane."""
    from services import trading_bot_service as tbs

    # If the router tries to dispatch we'll see the call here.
    dispatch_attempts: list[str] = []

    async def _track(**kwargs):
        dispatch_attempts.append(kwargs["signal"]["symbol"])
        return {"order": "should-not-happen"}

    monkeypatch.setattr(
        "services.executors.equity_executor.execute_equity_signal",
        _track,
    )
    monkeypatch.setattr(
        "services.executors.crypto_executor.execute_crypto_signal",
        _track,
    )

    # Simulate the body's first non-router check returning a skip
    # so we don't have to mock the full path.
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown",
        lambda *a, **k: {"skipped": True, "reason": "test_short_circuit"},
    )

    result = await tbs.execute_signal(
        signal={"symbol": "AAPL", "confidence": 0.85},
        market_data={},
        tier3_readiness={},
        config={"trade_size": 100},
        lane="equity",  # pre-set
    )
    assert dispatch_attempts == [], "router must not re-dispatch when lane is set"
    assert result == {"skipped": True, "reason": "test_short_circuit"}
