"""Phase 2b integration-hook regression tests.

Covers the four additive hooks that read the options-universe snapshot:

    1. ``failure_mode_classifier.classify_options_liquidity``
    2. ``adversarial_core.apply_options_context``
    3. ``conviction_service.compute_conviction`` (options_flow_score arg)
    4. ``ml_paper_trader.options_activity_guard``

The *central* invariant every hook must satisfy:

    "Empty snapshot → identical behaviour to the pre-Phase-2b baseline."

Each hook has at least one test proving this: a call with ``None`` (or a
snapshot missing the per-symbol entry) must produce the same output as
not calling the hook at all. That's what keeps Phase 2b safely additive.
"""
from __future__ import annotations

import pytest


# ═══════════════ Hook 1: failure_mode_classifier ═══════════════


def test_classify_options_liquidity_empty_snapshot_noop():
    """None input → None output. No failure mode flagged."""
    from services.failure_mode_classifier import classify_options_liquidity
    assert classify_options_liquidity("SPY", None) is None
    assert classify_options_liquidity("SPY", {}) is None


def test_classify_options_liquidity_stress_index_flags_liquidity_stress():
    """Healthy contracts + stress index > 4 → LIQUIDITY_STRESS, not TRAP."""
    from services.failure_mode_classifier import (
        classify_options_liquidity, FailureMode,
    )
    entry = {
        "symbol": "SPY",
        "contracts": [
            {"spread_bps": 20.0, "volume": 10_000},   # narrow, liquid
        ],
        "aggregate": {"liquidity_stress_index": 4.8,
                      "stress_level": "stress"},
    }
    result = classify_options_liquidity("SPY", entry)
    assert result is not None
    assert result.mode == FailureMode.LIQUIDITY_STRESS
    assert result.reasons == ["liquidity_stress_building"]
    # Never-dominant: caps size but doesn't block
    assert result.block_trade is False
    assert result.risk_multiplier_cap == 0.70
    assert result.metadata["liquidity_stress_index"] == 4.8


def test_classify_options_liquidity_stress_index_flags_instability():
    """stress_index ≥ 6 → tighter 0.50 cap, 'instability' reason."""
    from services.failure_mode_classifier import (
        classify_options_liquidity, FailureMode,
    )
    entry = {
        "symbol": "QQQ",
        "contracts": [{"spread_bps": 30.0, "volume": 5000}],
        "aggregate": {"liquidity_stress_index": 8.0,
                      "stress_level": "instability"},
    }
    result = classify_options_liquidity("QQQ", entry)
    assert result is not None
    assert result.mode == FailureMode.LIQUIDITY_STRESS
    assert result.reasons == ["liquidity_instability_imminent"]
    assert result.risk_multiplier_cap == 0.50


def test_classify_options_liquidity_trap_and_stress_picks_tighter():
    """Both stressors active (wide spread + instability index) → tighter
    cap wins. Caps equal → deterministic sort by block/cap."""
    from services.failure_mode_classifier import (
        classify_options_liquidity,
    )
    entry = {
        "symbol": "SPY",
        "contracts": [{"spread_bps": 120.0}, {"spread_bps": 150.0}],  # trap
        "aggregate": {"liquidity_stress_index": 8.0},                 # instability
    }
    result = classify_options_liquidity("SPY", entry)
    # Both candidates cap at 0.50 — TRAP wins by sort stability (first
    # in candidate order). Either answer keeps the tight cap intact.
    assert result is not None
    assert result.risk_multiplier_cap == 0.50


def test_options_stress_size_multiplier_bands():
    from services.failure_mode_classifier import options_stress_size_multiplier

    # Empty / missing → 1.0 (no effect)
    assert options_stress_size_multiplier(None) == 1.0
    assert options_stress_size_multiplier({}) == 1.0
    assert options_stress_size_multiplier({"aggregate": {}}) == 1.0

    # Normal / cautious → 1.0
    for idx in (1.0, 2.5, 3.9):
        entry = {"aggregate": {"liquidity_stress_index": idx}}
        assert options_stress_size_multiplier(entry) == 1.0

    # Stress band [4, 6) → 0.70
    assert options_stress_size_multiplier(
        {"aggregate": {"liquidity_stress_index": 4.0}}) == 0.70
    assert options_stress_size_multiplier(
        {"aggregate": {"liquidity_stress_index": 5.9}}) == 0.70

    # Instability ≥ 6 → 0.50
    assert options_stress_size_multiplier(
        {"aggregate": {"liquidity_stress_index": 6.0}}) == 0.50
    assert options_stress_size_multiplier(
        {"aggregate": {"liquidity_stress_index": 10.0}}) == 0.50


def test_options_stress_size_multiplier_malformed_index_is_identity():
    from services.failure_mode_classifier import options_stress_size_multiplier
    entry = {"aggregate": {"liquidity_stress_index": "NaN-ish"}}
    assert options_stress_size_multiplier(entry) == 1.0


def test_classify_options_liquidity_empty_contracts_flags_trap():
    """Symbol present but filter rejected all strikes → flag the trap."""
    from services.failure_mode_classifier import (
        classify_options_liquidity, FailureMode,
    )
    entry = {
        "symbol": "SPY",
        "contracts": [],
        "aggregate": {"put_call_ratio": 1.0},
    }
    result = classify_options_liquidity("SPY", entry)
    assert result is not None
    assert result.mode == FailureMode.OPTIONS_LIQUIDITY_TRAP
    assert result.reasons == ["no_hot_flow_contracts"]
    # "additive, never dominant": must not block, must cap at ≥ 0.50
    assert result.block_trade is False
    assert result.risk_multiplier_cap <= 0.50


def test_classify_options_liquidity_wide_min_spread_flags_trap():
    """All contracts liquid by volume/OI but narrowest spread > 75 bps."""
    from services.failure_mode_classifier import (
        classify_options_liquidity, FailureMode,
    )
    entry = {
        "symbol": "SPY",
        "contracts": [
            {"spread_bps": 120.0, "volume": 10_000},
            {"spread_bps": 150.0, "volume": 5_000},
        ],
        "aggregate": {},
    }
    result = classify_options_liquidity("SPY", entry)
    assert result is not None
    assert result.mode == FailureMode.OPTIONS_LIQUIDITY_TRAP
    assert result.reasons == ["options_spread_widening"]
    assert result.metadata["min_spread_bps"] == 120.0
    assert result.block_trade is False


def test_classify_options_liquidity_tight_spread_passes():
    """Healthy options market → no verdict (None, not NORMAL)."""
    from services.failure_mode_classifier import classify_options_liquidity
    entry = {
        "symbol": "SPY",
        "contracts": [
            {"spread_bps": 20.0, "volume": 10_000},
            {"spread_bps": 35.0, "volume": 5_000},
        ],
        "aggregate": {},
    }
    assert classify_options_liquidity("SPY", entry) is None


def test_pick_tighter_failure_prefers_blocking():
    """block_trade=True always wins, regardless of multiplier cap."""
    from services.failure_mode_classifier import (
        FailureMode, FailureModeResult, pick_tighter_failure,
    )
    blocker = FailureModeResult(
        mode=FailureMode.DATA_QUALITY_FAILURE, confidence=1.0,
        risk_multiplier_cap=0.0, block_trade=True,
        reasons=["data_missing"], metadata={},
    )
    capper = FailureModeResult(
        mode=FailureMode.OPTIONS_LIQUIDITY_TRAP, confidence=0.7,
        risk_multiplier_cap=0.50, block_trade=False,
        reasons=["wide_options_spread"], metadata={},
    )
    assert pick_tighter_failure(blocker, capper) is blocker
    assert pick_tighter_failure(None, capper) is capper
    assert pick_tighter_failure(None, None) is None


# ═══════════════ Hook 2: adversarial_core ═══════════════


def test_apply_options_context_empty_entry_is_identity():
    """None snapshot → identical AgentOutputs, same references ok."""
    from services.adversarial_core import AgentOutput, apply_options_context
    bull = AgentOutput(side="LONG", confidence=0.7, expected_r=1.2,
                       thesis="momentum", invalidations=[])
    bear = AgentOutput(side="SHORT_OR_REJECT", confidence=0.4, expected_r=1.1,
                       thesis="overbought", invalidations=[])
    bull2, bear2 = apply_options_context(bull, bear, None)
    assert bull2.thesis == "momentum"  # untouched
    assert bear2.thesis == "overbought"
    assert bull2.confidence == 0.7     # confidence never moves
    assert bear2.confidence == 0.4


def test_apply_options_context_missing_pcr_is_identity():
    """Entry present but aggregate has no PCR → no narrative change."""
    from services.adversarial_core import AgentOutput, apply_options_context
    bull = AgentOutput(side="LONG", confidence=0.7, expected_r=1.2,
                       thesis="momentum", invalidations=[])
    bear = AgentOutput(side="SHORT_OR_REJECT", confidence=0.4, expected_r=1.1,
                       thesis="overbought", invalidations=[])
    entry = {"symbol": "SPY", "contracts": [], "aggregate": {}}  # no PCR
    bull2, bear2 = apply_options_context(bull, bear, entry)
    assert bull2.thesis == "momentum"
    assert bear2.thesis == "overbought"


def test_apply_options_context_call_heavy_pcr_enriches_bull():
    """PCR < 0.7 → bullish flow annotated on Bull's thesis."""
    from services.adversarial_core import AgentOutput, apply_options_context
    bull = AgentOutput(side="LONG", confidence=0.7, expected_r=1.2,
                       thesis="momentum", invalidations=[])
    bear = AgentOutput(side="SHORT_OR_REJECT", confidence=0.4, expected_r=1.1,
                       thesis="overbought", invalidations=[])
    entry = {"symbol": "SPY", "contracts": [], "aggregate": {"put_call_ratio": 0.5}}
    bull2, bear2 = apply_options_context(bull, bear, entry)
    assert "strong_call_flow" in bull2.thesis
    assert bear2.thesis == "overbought"  # bear unchanged
    # Critical never-dominant rule: confidence/expected_r never move
    assert bull2.confidence == 0.7
    assert bull2.expected_r == 1.2


def test_apply_options_context_put_heavy_pcr_enriches_bear():
    """PCR > 1.3 → bearish flow annotated on Bear's thesis."""
    from services.adversarial_core import AgentOutput, apply_options_context
    bull = AgentOutput(side="LONG", confidence=0.7, expected_r=1.2,
                       thesis="momentum", invalidations=[])
    bear = AgentOutput(side="SHORT_OR_REJECT", confidence=0.4, expected_r=1.1,
                       thesis="overbought", invalidations=[])
    entry = {"symbol": "QQQ", "contracts": [], "aggregate": {"put_call_ratio": 1.8}}
    bull2, bear2 = apply_options_context(bull, bear, entry)
    assert bull2.thesis == "momentum"  # bull unchanged
    assert "elevated_put_activity" in bear2.thesis
    assert bear2.confidence == 0.4


def test_apply_options_context_neutral_pcr_is_identity():
    """PCR between 0.7 and 1.3 → no annotation (market is balanced)."""
    from services.adversarial_core import AgentOutput, apply_options_context
    bull = AgentOutput(side="LONG", confidence=0.7, expected_r=1.2,
                       thesis="momentum", invalidations=[])
    bear = AgentOutput(side="SHORT_OR_REJECT", confidence=0.4, expected_r=1.1,
                       thesis="overbought", invalidations=[])
    entry = {"aggregate": {"put_call_ratio": 1.0}}
    bull2, bear2 = apply_options_context(bull, bear, entry)
    assert bull2.thesis == "momentum"
    assert bear2.thesis == "overbought"


def test_apply_options_context_liquidity_stress_annotates_bear():
    """stress_index ≥ 4 → bear thesis gets stress annotation."""
    from services.adversarial_core import AgentOutput, apply_options_context
    bull = AgentOutput(side="LONG", confidence=0.7, expected_r=1.2,
                       thesis="momentum", invalidations=[])
    bear = AgentOutput(side="SHORT_OR_REJECT", confidence=0.4, expected_r=1.1,
                       thesis="overbought", invalidations=[])
    entry = {"aggregate": {"liquidity_stress_index": 5.0}}
    bull2, bear2 = apply_options_context(bull, bear, entry)
    assert "liquidity_stress_rising" in bear2.thesis
    # Bull never receives stress annotations — purely narrative-additive
    assert bull2.thesis == "momentum"
    # Confidence/expected_r still sacred
    assert bear2.confidence == 0.4
    assert bear2.expected_r == 1.1


def test_apply_options_context_instability_annotates_bear_differently():
    """stress_index ≥ 6 → 'liquidity_instability_imminent' tag."""
    from services.adversarial_core import AgentOutput, apply_options_context
    bull = AgentOutput(side="LONG", confidence=0.7, expected_r=1.2,
                       thesis="momentum", invalidations=[])
    bear = AgentOutput(side="SHORT_OR_REJECT", confidence=0.4, expected_r=1.1,
                       thesis="overbought", invalidations=[])
    entry = {"aggregate": {"liquidity_stress_index": 7.5}}
    _, bear2 = apply_options_context(bull, bear, entry)
    assert "liquidity_instability_imminent" in bear2.thesis
    # The stress-rising tag is superseded, not both
    assert "liquidity_stress_rising" not in bear2.thesis


# ═══════════════ Hook 3: conviction_service ═══════════════


@pytest.mark.asyncio
async def test_compute_conviction_without_options_arg_is_unchanged():
    """Callers not yet passing ``options_flow_score`` must see the
    identical result as the pre-Phase-2b implementation (score,
    tier, size_multiplier, breakdown structure).
    """
    from services.conviction_service import compute_conviction

    class _FakeDB:
        def __getitem__(self, name):
            class _Col:
                async def find_one(self, *a, **kw):
                    return None
                async def count_documents(self, *a, **kw):
                    return 0
                def find(self, *a, **kw):
                    class _C:
                        async def to_list(self, *_a, **_kw):
                            return []
                        def sort(self, *a, **kw): return self
                        def limit(self, *a, **kw): return self
                    return _C()
            return _Col()

    db = _FakeDB()
    # Baseline: no options flow score
    r_baseline = await compute_conviction(
        db, user_id="u1", asset="AAPL", direction="up",
        confidence=0.7, regime_match=True,
    )
    # Same inputs + None options_flow_score — must match baseline exactly
    r_none = await compute_conviction(
        db, user_id="u1", asset="AAPL", direction="up",
        confidence=0.7, regime_match=True, options_flow_score=None,
    )
    assert r_baseline["score"] == r_none["score"]
    assert r_baseline["tier"] == r_none["tier"]
    assert r_baseline["size_multiplier"] == r_none["size_multiplier"]
    # The new breakdown field is present with a zero value in both paths
    assert r_baseline["breakdown"]["options_flow_boost"] == 0.0
    assert r_none["breakdown"]["options_flow_boost"] == 0.0


@pytest.mark.asyncio
async def test_compute_conviction_low_options_score_no_boost():
    """Flow score below threshold → boost component stays at zero."""
    from services.conviction_service import compute_conviction

    class _FakeDB:
        def __getitem__(self, name):
            class _Col:
                async def find_one(self, *a, **kw): return None
                async def count_documents(self, *a, **kw): return 0
                def find(self, *a, **kw):
                    class _C:
                        async def to_list(self, *a, **kw): return []
                        def sort(self, *a, **kw): return self
                        def limit(self, *a, **kw): return self
                    return _C()
            return _Col()

    r = await compute_conviction(
        _FakeDB(), user_id="u", asset="AAPL", direction="up",
        confidence=0.6, options_flow_score=2.0,  # below 3.0 threshold
    )
    assert r["breakdown"]["options_flow_boost"] == 0.0


@pytest.mark.asyncio
async def test_compute_conviction_high_options_score_adds_capped_boost():
    """Flow score >> threshold → boost maxes at OPTIONS_FLOW_BOOST_MAX.
    This is the "never-dominant" guarantee: the boost alone can NOT
    promote a 0.50 signal to "strong" (needs ≥ 0.60) without existing
    strong calibration/regime signals."""
    from services.conviction_service import (
        compute_conviction, OPTIONS_FLOW_BOOST_MAX,
    )

    class _FakeDB:
        def __getitem__(self, name):
            class _Col:
                async def find_one(self, *a, **kw): return None
                async def count_documents(self, *a, **kw): return 0
                def find(self, *a, **kw):
                    class _C:
                        async def to_list(self, *a, **kw): return []
                        def sort(self, *a, **kw): return self
                        def limit(self, *a, **kw): return self
                    return _C()
            return _Col()

    r = await compute_conviction(
        _FakeDB(), user_id="u", asset="AAPL", direction="up",
        confidence=0.6, options_flow_score=10.0,  # way above threshold
    )
    # Boost capped at the configured max (0.05)
    assert r["breakdown"]["options_flow_boost"] == pytest.approx(OPTIONS_FLOW_BOOST_MAX)
    # Boost is additive + clamped at 1.0 via the final max/min
    assert 0.0 <= r["score"] <= 1.0


# ═══════════════ Hook 4: ml_paper_trader ═══════════════


def test_options_activity_guard_empty_snapshot_allows():
    """None entry → allow (unchanged behaviour)."""
    from services.ml_paper_trader import options_activity_guard
    allow, reason = options_activity_guard(None)
    assert allow is True
    assert reason == ""


def test_options_activity_guard_missing_aggregate_allows():
    """Entry present but no aggregate.total_volume → allow (unknown, not veto)."""
    from services.ml_paper_trader import options_activity_guard
    entry = {"symbol": "SPY", "contracts": [], "aggregate": {}}
    allow, reason = options_activity_guard(entry)
    assert allow is True
    assert reason == ""


def test_options_activity_guard_low_volume_vetoes():
    """Below min_volume → deny with LOW_OPTIONS_ACTIVITY reason."""
    from services.ml_paper_trader import options_activity_guard
    entry = {"aggregate": {"total_volume": 500}}
    allow, reason = options_activity_guard(entry)
    assert allow is False
    assert reason == "LOW_OPTIONS_ACTIVITY"


def test_options_activity_guard_healthy_volume_allows():
    from services.ml_paper_trader import options_activity_guard
    entry = {"aggregate": {"total_volume": 50_000}}
    allow, reason = options_activity_guard(entry)
    assert allow is True
    assert reason == ""


# ═══════════════ Options snapshot reader (shared guard layer) ═══════════════


@pytest.mark.asyncio
async def test_read_options_snapshot_missing_db_returns_none():
    from services.options_universe_service import read_options_snapshot
    assert await read_options_snapshot(None, "SPY") is None


@pytest.mark.asyncio
async def test_read_options_snapshot_no_doc_returns_none():
    """Snapshot collection is empty (warm never ran) → None."""
    from services.options_universe_service import read_options_snapshot
    from tests.test_top_universe_service import _FakeDB
    assert await read_options_snapshot(_FakeDB(), "SPY") is None


@pytest.mark.asyncio
async def test_read_options_snapshot_unconfigured_symbol_returns_none():
    """Doc exists but symbol isn't in it → None. (e.g., trading AAPL
    but AAPL not in the options universe)."""
    from services.options_universe_service import (
        read_options_snapshot, OPTIONS_UNIVERSE_COLLECTION, CURRENT_SNAPSHOT_ID,
    )
    from tests.test_top_universe_service import _FakeDB
    from datetime import datetime, timezone

    db = _FakeDB()
    db[OPTIONS_UNIVERSE_COLLECTION].docs.append({
        "_id": CURRENT_SNAPSHOT_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data": [{"symbol": "SPY", "contracts": [], "has_hot_flow": False,
                  "flow_maturity": True, "stable_minutes": 15,
                  "aggregate": {}}],
    })
    # AAPL isn't in the snapshot
    assert await read_options_snapshot(db, "AAPL") is None
    # SPY is
    entry = await read_options_snapshot(db, "SPY")
    assert entry is not None
    assert entry["symbol"] == "SPY"


@pytest.mark.asyncio
async def test_read_options_snapshot_case_insensitive():
    from services.options_universe_service import (
        read_options_snapshot, OPTIONS_UNIVERSE_COLLECTION, CURRENT_SNAPSHOT_ID,
    )
    from tests.test_top_universe_service import _FakeDB
    from datetime import datetime, timezone

    db = _FakeDB()
    db[OPTIONS_UNIVERSE_COLLECTION].docs.append({
        "_id": CURRENT_SNAPSHOT_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data": [{"symbol": "SPY", "contracts": [], "has_hot_flow": False,
                  "flow_maturity": True, "stable_minutes": 15,
                  "aggregate": {}}],
    })
    assert (await read_options_snapshot(db, "spy"))["symbol"] == "SPY"
