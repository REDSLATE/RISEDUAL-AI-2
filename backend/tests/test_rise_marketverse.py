"""Tripwire coverage for RISE MarketVerse v0.1.

Doctrine pinned here:
  1. SHADOW ONLY. MarketVerse must NEVER write to live collections
     (paper_trades, crypto_paper_trades, crypto_live_trades,
     research_shadow_decisions). Output lands in
     ``marketverse_observations`` exclusively.
  2. Deterministic. Same input bars → same output state (modulo
     ``timestamp``).
  3. Defensive. Empty bars / corrupt data / arena exceptions never
     raise; the panel always returns SOMETHING the persistence
     layer can store.
  4. ``compute_disagreement`` scores in [0, 1] with full agreement
     at 0 and a 3-way split near 0.67.
  5. Probable-paths probabilities sum to 1.0 within float epsilon.
  6. ``record_marketverse_observation`` swallows every error and
     never raises into the caller. It also never touches any
     collection besides ``marketverse_observations``.
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rise_marketverse import (
    BrainOpinion,
    DEFAULT_BRAIN_ROSTER,
    MarketState,
    MarketWorldModel,
    ProbablePath,
    ShadowBrainArena,
    compute_disagreement,
    marketstate_asdict,
    opinions_asdict,
)


def _flat_bars(n: int = 30, base: float = 100.0) -> list[dict]:
    """Pancake-flat closes. Should produce regime=range / vol=low /
    momentum near zero / RSI near 50."""
    return [{"c": base, "ts": i} for i in range(n)]


def _uptrending_bars(n: int = 30, base: float = 100.0, step: float = 1.0) -> list[dict]:
    """Strong, steady uptrend. Should produce trending_up regime,
    bullish momentum, RSI > 70 toward the end."""
    return [{"c": base + step * i, "ts": i} for i in range(n)]


def _downtrending_bars(n: int = 30, base: float = 200.0, step: float = -1.0) -> list[dict]:
    return [{"c": base + step * i, "ts": i} for i in range(n)]


def _spiky_bars(n: int = 30, base: float = 100.0) -> list[dict]:
    """Alternating + / - moves of 5%. Should land vol in elevated/extreme."""
    closes = []
    cur = base
    for i in range(n):
        cur = cur * (1.05 if i % 2 == 0 else 0.95)
        closes.append({"c": cur, "ts": i})
    return closes


# ── MarketWorldModel.infer_state ───────────────────────────────────────


def test_world_model_returns_marketstate_with_all_fields():
    state = MarketWorldModel().infer_state(_flat_bars(), symbol="BTC")
    assert isinstance(state, MarketState)
    for field in (
        "symbol", "regime", "volatility", "volatility_band",
        "momentum_5b", "rsi_14", "last_close", "bar_count",
        "probable_paths", "timestamp",
    ):
        assert hasattr(state, field), f"missing {field}"
    assert state.symbol == "BTC"
    assert state.bar_count == 30


def test_world_model_flat_bars_lands_low_vol_neutral_regime():
    state = MarketWorldModel().infer_state(_flat_bars())
    assert state.volatility_band == "low"
    assert state.regime in ("range", "uncertain")
    assert state.momentum_5b == pytest.approx(0.0, abs=1e-6)


def test_world_model_uptrend_lands_trending_or_overbought():
    state = MarketWorldModel().infer_state(_uptrending_bars())
    assert state.regime in ("trending_up", "overbought", "parabolic_up")
    assert state.momentum_5b > 0


def test_world_model_downtrend_lands_trending_down_or_oversold():
    state = MarketWorldModel().infer_state(_downtrending_bars())
    assert state.regime in ("trending_down", "oversold", "parabolic_down")
    assert state.momentum_5b < 0


def test_world_model_spiky_bars_lands_high_vol_band():
    state = MarketWorldModel().infer_state(_spiky_bars())
    assert state.volatility_band in ("elevated", "extreme")


def test_world_model_handles_empty_bars_gracefully():
    state = MarketWorldModel().infer_state([])
    assert state.regime == "uncertain"
    assert state.bar_count == 0
    assert state.probable_paths == []
    assert state.notes.get("empty_bars") is True


def test_world_model_handles_short_bar_window():
    """Fewer than 15 bars (RSI lookback) — must still produce a row
    with RSI=50 fallback, not crash."""
    state = MarketWorldModel().infer_state(_flat_bars(n=5))
    assert state.rsi_14 == 50.0
    assert state.bar_count == 5


def test_world_model_filters_bars_with_bad_close():
    bars = _flat_bars(n=20)
    bars.append({"c": None, "ts": 99})
    bars.append({"c": "garbage", "ts": 100})
    bars.append({"c": float("nan"), "ts": 101})
    state = MarketWorldModel().infer_state(bars)
    # Bad rows skipped — bar_count counts only valid closes.
    assert state.bar_count == 20


def test_world_model_is_deterministic():
    """Same bars → same state (excluding timestamp)."""
    bars = _uptrending_bars()
    s1 = MarketWorldModel().infer_state(bars)
    s2 = MarketWorldModel().infer_state(bars)
    for f in ("regime", "volatility", "momentum_5b", "rsi_14",
              "last_close", "bar_count", "volatility_band"):
        assert getattr(s1, f) == getattr(s2, f), f"non-deterministic {f}"


# ── Probable paths sanity ──────────────────────────────────────────────


def test_probable_paths_three_scenarios():
    state = MarketWorldModel().infer_state(_uptrending_bars())
    assert len(state.probable_paths) == 3
    names = {p.name for p in state.probable_paths}
    assert names == {"bull", "base", "bear"}


def test_probable_paths_probs_sum_to_one():
    state = MarketWorldModel().infer_state(_uptrending_bars())
    total = sum(p.prob for p in state.probable_paths)
    assert math.isclose(total, 1.0, abs_tol=1e-6)


def test_probable_paths_bull_above_base_above_bear():
    state = MarketWorldModel().infer_state(_spiky_bars())
    bull = [p for p in state.probable_paths if p.name == "bull"][0]
    base = [p for p in state.probable_paths if p.name == "base"][0]
    bear = [p for p in state.probable_paths if p.name == "bear"][0]
    assert bull.price > base.price > bear.price


# ── ShadowBrainArena ───────────────────────────────────────────────────


def test_arena_returns_opinion_per_brain():
    state = MarketWorldModel().infer_state(_uptrending_bars())
    opinions = ShadowBrainArena().decide_all(state)
    assert len(opinions) == len(DEFAULT_BRAIN_ROSTER)
    names = {o.brain_name for o in opinions}
    assert "momentum" in names
    assert "mean_revert" in names
    assert "volatility" in names
    assert "regime" in names
    assert "path_weighted" in names


def test_arena_actions_are_canonical_tokens():
    state = MarketWorldModel().infer_state(_uptrending_bars())
    opinions = ShadowBrainArena().decide_all(state)
    for o in opinions:
        assert o.action in ("LONG", "SHORT", "HOLD"), o.action


def test_arena_confidence_in_unit_range():
    state = MarketWorldModel().infer_state(_uptrending_bars())
    for o in ShadowBrainArena().decide_all(state):
        assert 0.0 <= o.confidence <= 1.0


def test_arena_swallows_brain_exceptions():
    """A broken brain function must NOT take down the panel — the
    arena substitutes a HOLD opinion with the exception type as
    the reasoning, and the other brains still vote."""
    def _exploding_brain(state):
        raise RuntimeError("kaboom")
    state = MarketWorldModel().infer_state(_uptrending_bars())
    arena = ShadowBrainArena(brains=(_exploding_brain,) + DEFAULT_BRAIN_ROSTER)
    opinions = arena.decide_all(state)
    assert len(opinions) == len(DEFAULT_BRAIN_ROSTER) + 1
    # Exploded brain landed a HOLD with the error in the reasoning.
    crashed = next(
        o for o in opinions if "RuntimeError" in (o.reasoning or "")
    )
    assert crashed.action == "HOLD"


def test_arena_rejects_empty_roster():
    with pytest.raises(ValueError):
        ShadowBrainArena(brains=())


def test_arena_uptrend_has_at_least_one_long_vote():
    state = MarketWorldModel().infer_state(_uptrending_bars())
    actions = [o.action for o in ShadowBrainArena().decide_all(state)]
    assert "LONG" in actions


def test_arena_downtrend_has_at_least_one_short_vote():
    state = MarketWorldModel().infer_state(_downtrending_bars())
    actions = [o.action for o in ShadowBrainArena().decide_all(state)]
    assert "SHORT" in actions


# ── compute_disagreement ───────────────────────────────────────────────


def test_disagreement_score_zero_when_all_agree():
    ops = [
        BrainOpinion(brain_name=f"b{i}", action="LONG",
                     confidence=0.5, reasoning="")
        for i in range(5)
    ]
    spread = compute_disagreement(ops)
    assert spread["disagreement_score"] == 0.0
    assert spread["top_action"] == "LONG"
    assert spread["vote_counts"]["LONG"] == 5


def test_disagreement_score_high_on_three_way_split():
    ops = [
        BrainOpinion(brain_name="a", action="LONG", confidence=0.5, reasoning=""),
        BrainOpinion(brain_name="b", action="LONG", confidence=0.5, reasoning=""),
        BrainOpinion(brain_name="c", action="SHORT", confidence=0.5, reasoning=""),
        BrainOpinion(brain_name="d", action="SHORT", confidence=0.5, reasoning=""),
        BrainOpinion(brain_name="e", action="HOLD", confidence=0.5, reasoning=""),
        BrainOpinion(brain_name="f", action="HOLD", confidence=0.5, reasoning=""),
    ]
    spread = compute_disagreement(ops)
    # max_share is 2/6, disagreement = 1 - 0.333 = 0.667
    assert spread["disagreement_score"] == pytest.approx(0.6667, abs=1e-3)


def test_disagreement_empty_returns_zero():
    spread = compute_disagreement([])
    assert spread["disagreement_score"] == 0.0


# ── asdict round-trip ──────────────────────────────────────────────────


def test_marketstate_asdict_recurses_paths():
    state = MarketWorldModel().infer_state(_uptrending_bars())
    d = marketstate_asdict(state)
    assert isinstance(d, dict)
    assert isinstance(d["probable_paths"], list)
    assert all(isinstance(p, dict) for p in d["probable_paths"])


def test_opinions_asdict_round_trip():
    ops = [
        BrainOpinion(brain_name="x", action="LONG",
                     confidence=0.5, reasoning="t"),
    ]
    out = opinions_asdict(ops)
    assert out == [{"brain_name": "x", "action": "LONG",
                    "confidence": 0.5, "reasoning": "t"}]


# ── record_marketverse_observation (the engine hook) ────────────────────


class _FakeColl:
    def __init__(self): self.inserted = []
    async def insert_one(self, doc):
        self.inserted.append(doc)
        return MagicMock(inserted_id="fake-id-" + str(len(self.inserted)))


class _FakeDB:
    def __init__(self):
        self.marketverse_observations = _FakeColl()


@pytest.mark.asyncio
async def test_record_writes_only_to_marketverse_collection():
    from services.research_shadow_engines import record_marketverse_observation
    db = _FakeDB()
    res = await record_marketverse_observation(
        db, symbol="BTC/USD", bars=_uptrending_bars(), asset_type="crypto",
    )
    assert res is not None
    assert len(db.marketverse_observations.inserted) == 1
    row = db.marketverse_observations.inserted[0]
    assert row["symbol"] == "BTC/USD"
    assert row["asset_type"] == "crypto"
    assert "market_state" in row
    assert "shadow_outputs" in row
    assert "disagreement" in row
    # Doctrine pin: no other collections touched.
    assert not hasattr(db, "crypto_paper_trades")
    assert not hasattr(db, "crypto_live_trades")
    assert not hasattr(db, "paper_trades")
    assert not hasattr(db, "research_shadow_decisions")


@pytest.mark.asyncio
async def test_record_handles_none_db_gracefully():
    from services.research_shadow_engines import record_marketverse_observation
    res = await record_marketverse_observation(
        None, symbol="BTC", bars=_uptrending_bars(),
    )
    assert res is None


@pytest.mark.asyncio
async def test_record_handles_empty_bars_gracefully():
    from services.research_shadow_engines import record_marketverse_observation
    db = _FakeDB()
    res = await record_marketverse_observation(
        db, symbol="BTC", bars=[],
    )
    assert res is None
    assert db.marketverse_observations.inserted == []


@pytest.mark.asyncio
async def test_record_handles_empty_symbol_gracefully():
    from services.research_shadow_engines import record_marketverse_observation
    db = _FakeDB()
    res = await record_marketverse_observation(
        db, symbol="", bars=_uptrending_bars(),
    )
    assert res is None


@pytest.mark.asyncio
async def test_record_swallows_mongo_insert_failure():
    """Mongo blowing up MUST NOT raise into the caller — Alpha's
    live path is the priority."""
    from services.research_shadow_engines import record_marketverse_observation

    class _BoomColl:
        async def insert_one(self, _doc): raise RuntimeError("db down")
    class _BoomDB:
        marketverse_observations = _BoomColl()

    res = await record_marketverse_observation(
        _BoomDB(), symbol="BTC", bars=_uptrending_bars(),
    )
    assert res is None


@pytest.mark.asyncio
async def test_record_carries_active_action_and_meta():
    from services.research_shadow_engines import record_marketverse_observation
    db = _FakeDB()
    await record_marketverse_observation(
        db, symbol="BTC", bars=_uptrending_bars(),
        active_action="LONG", extra={"trace_id": "abc123"},
    )
    row = db.marketverse_observations.inserted[0]
    assert row["active_action"] == "LONG"
    assert row["meta"]["trace_id"] == "abc123"
