"""Unit tests for the Phase 2a options universe stack.

Covers three modules:
    * services.options_filters       — pure liquid/unusual gates
    * services.options_flow_scorer   — score formula + ranker
    * services.options_universe_service — orchestrator + market-hours gate
      + single-doc snapshot schema
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


from tests.test_top_universe_service import _FakeDB  # noqa: E402  (reuse fake)


# ═══════════════ options_filters ═══════════════


def test_compute_spread_bps_tight_quote():
    from services.options_filters import compute_spread_bps
    # $1.00 / $1.01 → 1c / $1.005 mid ≈ 99.5 bps
    result = compute_spread_bps(1.00, 1.01)
    assert 99 <= result <= 100


def test_compute_spread_bps_degenerate_returns_sentinel():
    from services.options_filters import compute_spread_bps
    # 9999 sentinel means "effectively untradeable" — used by callers
    # rather than None so arithmetic paths don't need null-checks.
    assert compute_spread_bps(0, 0) == 9999.0
    assert compute_spread_bps(1.5, 1.0) == 9999.0  # inverted
    assert compute_spread_bps(-1, 1) == 9999.0     # negative


def test_is_liquid_requires_all_three_gates():
    from services.options_filters import is_liquid
    # Baseline liquid contract
    base = {"volume": 600, "open_interest": 1500, "bid": 1.00, "ask": 1.005}
    assert is_liquid(base)
    # Fail on volume
    assert not is_liquid({**base, "volume": 500})
    # Fail on OI
    assert not is_liquid({**base, "open_interest": 1000})
    # Fail on spread (wide)
    assert not is_liquid({**base, "ask": 1.50})


def test_is_unusual_volume_multiplier():
    from services.options_filters import is_unusual
    # avg 1000 → flagged at > 2000
    assert is_unusual({"volume": 2500}, avg_volume=1000)
    assert not is_unusual({"volume": 1900}, avg_volume=1000)
    # avg=0 → can't be unusual (empty-chain guard)
    assert not is_unusual({"volume": 10_000}, avg_volume=0)


def test_filter_contracts_liquid_and_unusual_path():
    """Liquid + vol/OI > 1.5 passes even without chain-avg unusual flag."""
    from services.options_filters import filter_contracts

    chain = [
        # Liquid + net-new positioning (vol > 1.5 × OI). Tight spread 50 bps.
        {"volume": 3000, "open_interest": 1500, "bid": 1.00, "ask": 1.005},
        # Liquid but stale (vol/OI = 0.12, not unusual vs avg either)
        {"volume": 600, "open_interest": 5000, "bid": 1.00, "ask": 1.005},
        # Illiquid — fails spread gate (1.5% spread ≈ 1500 bps)
        {"volume": 100, "open_interest": 200, "bid": 1.00, "ask": 1.50},
    ]
    out = filter_contracts(chain)
    # First survives, second filtered (liquid but neither unusual nor fresh),
    # third filtered (not liquid).
    assert len(out) == 1
    assert out[0]["volume"] == 3000
    # spread_bps was stamped by filter
    assert "spread_bps" in out[0]


def test_filter_contracts_unusual_volume_path():
    """Liquid + vol ≥ 2× chain avg passes even if vol/OI < 1.5."""
    from services.options_filters import filter_contracts

    chain = [
        # Liquid, vol/OI = 0.625 (fresh-flow gate would fail), but 5× avg
        {"volume": 5000, "open_interest": 8000, "bid": 1.00, "ask": 1.005},
        {"volume": 600, "open_interest": 5000, "bid": 1.00, "ask": 1.005},
        {"volume": 700, "open_interest": 4000, "bid": 1.00, "ask": 1.005},
        {"volume": 800, "open_interest": 3000, "bid": 1.00, "ask": 1.005},
    ]
    # avg = (5000+600+700+800)/4 = 1775; row 1 vol = 5000 > 2*1775 = 3550 → unusual
    out = filter_contracts(chain)
    assert any(c["volume"] == 5000 for c in out)


def test_filter_contracts_empty_input():
    from services.options_filters import filter_contracts
    assert filter_contracts([]) == []


# ═══════════════ options_flow_scorer ═══════════════


def test_compute_flow_score_formula_matches_spec():
    """log(vol) + 2*(vol/OI) - 0.01*spread_bps — verify arithmetic."""
    import math
    from services.options_flow_scorer import compute_flow_score

    c = {"volume": 1000, "open_interest": 500, "spread_bps": 50}
    expected = math.log(1000) + 2.0 * (1000 / 500) - 0.01 * 50
    assert abs(compute_flow_score(c) - round(expected, 4)) < 1e-4


def test_compute_flow_score_zero_volume_does_not_crash():
    """log(0) is undefined — must fall back to log(1) per the spec."""
    from services.options_flow_scorer import compute_flow_score
    # 0 vol → log(max(0,1)) = 0, 0/1 = 0, -0.01*100 = -1 → score = -1
    score = compute_flow_score({"volume": 0, "open_interest": 0, "spread_bps": 100})
    assert score == -1.0


def test_compute_flow_score_higher_vol_oi_ratio_wins():
    """Two contracts with same volume — higher fresh-flow ratio ranks up."""
    from services.options_flow_scorer import compute_flow_score
    fresh = {"volume": 2000, "open_interest": 500, "spread_bps": 50}
    stale = {"volume": 2000, "open_interest": 10_000, "spread_bps": 50}
    assert compute_flow_score(fresh) > compute_flow_score(stale)


def test_rank_contracts_does_not_mutate_input():
    """Ranker must shallow-copy — callers' raw-chain views stay clean."""
    from services.options_flow_scorer import rank_contracts
    chain = [
        {"volume": 1000, "open_interest": 500, "spread_bps": 30},
        {"volume": 5000, "open_interest": 1000, "spread_bps": 20},
    ]
    before = [dict(c) for c in chain]
    result = rank_contracts(chain, top_n=2)
    # flow_score is on the returned copies, NOT on the originals
    assert "flow_score" in result[0]
    assert "flow_score" not in chain[0]
    assert chain == before  # unmodified


def test_rank_contracts_top_n_cap():
    from services.options_flow_scorer import rank_contracts
    chain = [{"volume": 100 * i, "open_interest": 500, "spread_bps": 30}
             for i in range(20)]
    assert len(rank_contracts(chain, top_n=5)) == 5


# ═══════════════ options_universe_service ═══════════════


def test_market_hours_gate_monday_session():
    from services.options_universe_service import is_market_open
    # Monday 15:00 UTC — inside session
    t = datetime(2026, 2, 2, 15, 0, tzinfo=timezone.utc)
    assert is_market_open(t)


def test_market_hours_gate_saturday_closed():
    from services.options_universe_service import is_market_open
    t = datetime(2026, 2, 7, 15, 0, tzinfo=timezone.utc)  # Saturday
    assert not is_market_open(t)


def test_market_hours_gate_pre_open():
    from services.options_universe_service import is_market_open
    # Tuesday 13:00 UTC — before 13:30 open
    t = datetime(2026, 2, 3, 13, 0, tzinfo=timezone.utc)
    assert not is_market_open(t)


def test_market_hours_gate_post_close():
    from services.options_universe_service import is_market_open
    # Tuesday 21:00 UTC — boundary is exclusive-end, so 21:00 is closed
    t = datetime(2026, 2, 3, 21, 0, tzinfo=timezone.utc)
    assert not is_market_open(t)


def test_get_options_underlyings_contains_spec_anchors():
    from services.options_universe_service import get_options_underlyings
    u = get_options_underlyings()
    for must_have in ("SPY", "QQQ", "NVDA", "TSLA"):
        assert must_have in u


def test_env_override_for_underlyings(monkeypatch):
    from services.options_universe_service import get_options_underlyings
    monkeypatch.setenv("OPTIONS_UNIVERSE_UNDERLYINGS", "PLTR,SHOP,COIN")
    assert get_options_underlyings() == ["PLTR", "SHOP", "COIN"]


def test_aggregate_contracts_put_call_ratio_and_mean_iv():
    from services.options_universe_service import _aggregate_contracts
    chain = [
        {"type": "CALL", "volume": 1000, "open_interest": 500,
         "implied_volatility": 0.30},
        {"type": "PUT", "volume": 1500, "open_interest": 400,
         "implied_volatility": 0.35},
        {"type": "PUT", "volume": 0, "open_interest": 100,
         "implied_volatility": None},
    ]
    agg = _aggregate_contracts(chain)
    assert agg["total_call_volume"] == 1000
    assert agg["total_put_volume"] == 1500
    assert agg["put_call_ratio"] == 1.5
    assert agg["mean_iv"] is not None and abs(agg["mean_iv"] - 0.325) < 0.005
    # IV rank / percentile MUST be None until history exists — never fake.
    assert agg["iv_rank"] is None
    assert agg["iv_percentile"] is None


def test_aggregate_contracts_zero_call_volume_returns_none_pcr():
    from services.options_universe_service import _aggregate_contracts
    agg = _aggregate_contracts([
        {"type": "PUT", "volume": 500, "open_interest": 100,
         "implied_volatility": 0.3},
    ])
    assert agg["put_call_ratio"] is None


@pytest.mark.asyncio
async def test_warm_skipped_when_market_closed(monkeypatch):
    """Market-closed path: writes a skipped stats row, never touches the
    snapshot doc, never calls the chain fetcher."""
    from services.options_universe_service import (
        WARM_STATS_COLLECTION, OPTIONS_UNIVERSE_COLLECTION,
        warm_options_universe,
    )

    monkeypatch.setattr(
        "services.options_universe_service.is_market_open", lambda *_: False,
    )
    # If the fetcher runs anyway, the test will fail (no such fixture)
    async def _exploder(*_a, **_k):
        raise AssertionError("Chain fetcher should not be called when market closed")
    monkeypatch.setattr(
        "services.options_universe_service.build_options_universe", _exploder,
    )

    db = _FakeDB()
    stats = await warm_options_universe(db)

    assert stats["status"] == "skipped"
    assert stats["reason"] == "market_closed"
    # Snapshot untouched
    assert db[OPTIONS_UNIVERSE_COLLECTION].docs == []
    # Exactly one stats row
    assert len(db[WARM_STATS_COLLECTION].docs) == 1
    assert db[WARM_STATS_COLLECTION].docs[0]["run_type"] == "options_warm"


@pytest.mark.asyncio
async def test_warm_force_bypasses_market_closed_gate(monkeypatch):
    """Admin manual trigger (force=True) must run even outside session."""
    from services.options_universe_service import (
        OPTIONS_UNIVERSE_COLLECTION, CURRENT_SNAPSHOT_ID,
        warm_options_universe,
    )

    monkeypatch.setattr(
        "services.options_universe_service.is_market_open", lambda *_: False,
    )
    monkeypatch.setattr(
        "services.options_universe_service.get_options_underlyings",
        lambda: ["SPY"],
    )

    async def _fake_build(symbols):
        return [{
            "symbol": "SPY",
            "contracts": [{"flow_score": 10.0, "strike": 500,
                           "type": "CALL", "volume": 3000,
                           "open_interest": 1500, "spread_bps": 20}],
            "aggregate": {
                "put_call_ratio": 1.1, "total_volume": 3000,
                "total_call_volume": 3000, "total_put_volume": 0,
                "total_open_interest": 1500, "mean_iv": 0.25,
                "iv_rank": None, "iv_percentile": None,
            },
        }]

    monkeypatch.setattr(
        "services.options_universe_service.build_options_universe",
        _fake_build,
    )

    db = _FakeDB()
    stats = await warm_options_universe(db, force=True)

    assert stats["status"] == "success"
    # Single-doc snapshot under _id="current"
    snapshots = [d for d in db[OPTIONS_UNIVERSE_COLLECTION].docs
                 if d.get("_id") == CURRENT_SNAPSHOT_ID]
    assert len(snapshots) == 1
    assert snapshots[0]["data"][0]["symbol"] == "SPY"
    assert "updated_at" in snapshots[0]


@pytest.mark.asyncio
async def test_warm_replaces_not_appends_snapshot(monkeypatch):
    """Second warm must replace the first snapshot, not append — the
    single-doc pattern depends on this."""
    from services.options_universe_service import (
        OPTIONS_UNIVERSE_COLLECTION, CURRENT_SNAPSHOT_ID,
        warm_options_universe,
    )

    monkeypatch.setattr(
        "services.options_universe_service.is_market_open", lambda *_: True,
    )
    monkeypatch.setattr(
        "services.options_universe_service.get_options_underlyings",
        lambda: ["SPY"],
    )

    pass_n = {"n": 0}

    async def _fake_build(symbols):
        pass_n["n"] += 1
        return [{
            "symbol": "SPY",
            "contracts": [{"flow_score": pass_n["n"], "volume": 1000,
                           "open_interest": 500, "spread_bps": 30}],
            "aggregate": {"put_call_ratio": 1.0, "total_volume": 1000,
                          "total_call_volume": 500, "total_put_volume": 500,
                          "total_open_interest": 500, "mean_iv": 0.3,
                          "iv_rank": None, "iv_percentile": None},
        }]

    monkeypatch.setattr(
        "services.options_universe_service.build_options_universe",
        _fake_build,
    )

    db = _FakeDB()
    await warm_options_universe(db)
    await warm_options_universe(db)

    snapshots = [d for d in db[OPTIONS_UNIVERSE_COLLECTION].docs
                 if d.get("_id") == CURRENT_SNAPSHOT_ID]
    # Exactly one snapshot doc exists (replaced, not appended)
    assert len(snapshots) == 1
    # And it carries the SECOND pass's data
    assert snapshots[0]["data"][0]["contracts"][0]["flow_score"] == 2


@pytest.mark.asyncio
async def test_get_options_status_empty_state(monkeypatch):
    from services.options_universe_service import get_options_status

    monkeypatch.setattr(
        "services.options_universe_service.get_options_underlyings",
        lambda: ["SPY", "QQQ"],
    )
    db = _FakeDB()
    out = await get_options_status(db)

    assert out["snapshot_updated_at"] is None
    assert out["underlyings_configured"] == ["SPY", "QQQ"]
    assert out["symbols_with_data"] == 0
    assert out["symbols_with_hot_flow"] == 0
    assert out["data"] == []
    assert out["last_warm"] is None
    # is_market_open flag is present (whatever its current value)
    assert "is_market_open" in out
