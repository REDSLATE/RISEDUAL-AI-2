"""Tests for the RISEDUAL Learning Core (Patent M).

Every IP-safety invariant from the contract is pinned by name:

1. ``test_evaluate_context_has_no_side_effects``
2. ``test_unknown_direction_does_not_get_positive_boost``
3. ``test_hold_does_not_get_positive_boost``
4. ``test_confidence_delta_capped_at_plus_minus_15``
5. ``test_adjusted_confidence_clamped_0_to_1``

Plus behaviour tests for the orchestrator wiring (training step
runs, memories ingest into clusters, pre-tell warning lowers
confidence, etc.).

Tests force-enable the canonical engine via env so retrieval/
ingest paths actually execute. We use a fresh
``RegimeMemoryRetrievalEngine`` per test to keep clusters
isolated from the process-wide singleton.
"""

from __future__ import annotations

import os
from typing import List

import numpy as np


# Force-enable the canonical engine. We mutate the module-level
# constants directly (not via ``importlib.reload``) because reload
# breaks class identity if any other test has already imported
# these modules — the alias check ``RegimeLabeledMemory is
# RegimeMemory`` would silently fail across test files otherwise.
os.environ["REGIME_MEMORY_ENABLED"] = "true"
os.environ["REGIME_MEMORY_MODE"] = "full_context"

import services.regime_memory_retrieval as _rmr  # noqa: E402

_rmr.REGIME_MEMORY_ENABLED = True
_rmr.REGIME_MEMORY_MODE = "full_context"

from services.auto_regime_tagger import (  # noqa: E402
    RawMacroData,
    RegimeTagger,
)
from services.cacl_original_ml import (  # noqa: E402
    ConfusionAwareEmbeddingNetwork,
)
from services.regime_clustering_layer import (  # noqa: E402
    RegimeClusteringEngine,
    RegimeLabeledMemory,
    RegimeFingerprint,
)
from services.risedual_learning_core import (  # noqa: E402
    LearningCoreDecisionContext,
    MAX_CONFIDENCE_DELTA,
    RisedualLearningCore,
)


# ─── builders ───────────────────────────────────────────────────


def _macro(date: str, vix: float = 18.0) -> RawMacroData:
    return RawMacroData(
        date=date,
        vix=vix,
        yield_2y=4.5,
        yield_10y=4.6,
        dxy=104.0,
        dxy_60d_avg=103.5,
        hy_oas_bp=420.0,
        ig_oas_bp=120.0,
        liquidity_z=0.0,
        macro_phase_hint="expansion",
    )


def _series(n: int = 60, start_vix: float = 18.0) -> List[RawMacroData]:
    """N daily snapshots ending at 2026-02-15."""
    out = []
    for i in range(n):
        # zero-pad month/day so date strings sort correctly
        day = (i % 28) + 1
        month = (i // 28) % 12 + 1
        out.append(_macro(
            date=f"2026-{month:02d}-{day:02d}-{i}",
            vix=start_vix + (i * 0.05),
        ))
    return out


def _ctx(
    direction: str = "LONG",
    base_confidence: float = 0.70,
    feature_dim: int = 8,
) -> LearningCoreDecisionContext:
    series = _series()
    return LearningCoreDecisionContext(
        symbol="AAPL",
        direction=direction,
        base_confidence=base_confidence,
        feature_vector=[0.1] * feature_dim,
        label=None,
        macro_data=series[-1],
        macro_history={},
        macro_series=series,
    )


def _core(input_dim: int = 8, n_classes: int = 3) -> RisedualLearningCore:
    return RisedualLearningCore(
        input_dim=input_dim,
        n_classes=n_classes,
        embedding_dim=16,
        regime_engine=RegimeClusteringEngine(),  # fresh, isolated
    )


# ─── invariant tests ────────────────────────────────────────────


def test_evaluate_context_has_no_side_effects():
    """Invariant 1 — evaluate is read-only."""
    core = _core()
    ctx = _ctx()

    report_before = core.regime_memory.get_cluster_report()
    proto_before = core.embedding_core.P.copy()
    cm_before = core.embedding_core.confusion_matrix.copy()

    out = core.evaluate_context(ctx)

    report_after = core.regime_memory.get_cluster_report()
    assert report_after == report_before
    np.testing.assert_array_equal(core.embedding_core.P, proto_before)
    np.testing.assert_array_equal(
        core.embedding_core.confusion_matrix, cm_before,
    )
    assert out["symbol"] == "AAPL"


def test_unknown_direction_does_not_get_positive_boost():
    """Invariant 2/3 — UNKNOWN → no positive memory boost.

    We force a high memory_win_rate scenario by ingesting winning
    LONG memories and then evaluate with an unknown token.
    The canonical engine returns ``[]`` for non-trade directions,
    so memory_win_rate is ``None`` and no boost can apply.
    """
    core = _core()
    _seed_winning_memories(core, ticker="AAPL", direction="LONG", n=10)

    ctx = _ctx(direction="ZIGZAG_GIBBERISH", base_confidence=0.50)
    out = core.evaluate_context(ctx)

    assert out["direction_canonical"] == "UNKNOWN"
    assert out["similar_memory_count"] == 0
    assert out["memory_win_rate"] is None
    # Boost is gated on canonical_direction in {"LONG","SHORT"} —
    # no ``+0.03`` should leak through.
    delta = out["adjusted_confidence"] - 0.50
    assert delta <= MAX_CONFIDENCE_DELTA + 1e-9
    # Specifically: with no memories and no pretell warning,
    # the only adjustment is the base/model blend, which can't
    # exceed the cap regardless.


def test_hold_does_not_get_positive_boost():
    """Invariant 3 — HOLD never receives the +0.03 memory boost."""
    # Even if the canonical engine somehow returned winning
    # memories for HOLD (it won't — the engine filters non-trade
    # tokens), the orchestrator's ``_adjust_confidence`` itself
    # must refuse the boost.
    delta_with_boost_attempt = (
        RisedualLearningCore._adjust_confidence(
            base_confidence=0.50,
            model_confidence=0.50,
            memory_win_rate=0.99,        # would-be huge boost
            pretell_warning=None,
            canonical_direction="HOLD",
        ) - 0.50
    )
    delta_with_long = (
        RisedualLearningCore._adjust_confidence(
            base_confidence=0.50,
            model_confidence=0.50,
            memory_win_rate=0.99,
            pretell_warning=None,
            canonical_direction="LONG",
        ) - 0.50
    )
    # LONG gets the +0.03 boost on top of the blend; HOLD does not.
    assert delta_with_long > delta_with_boost_attempt


def test_confidence_delta_capped_at_plus_minus_15():
    """Invariant 4 — total delta bounded to ±MAX_CONFIDENCE_DELTA."""
    # Maximum positive scenario: tiny base, huge model conf,
    # winning memories, LONG direction.
    big_pos = RisedualLearningCore._adjust_confidence(
        base_confidence=0.10,
        model_confidence=1.00,
        memory_win_rate=0.99,
        pretell_warning=None,
        canonical_direction="LONG",
    )
    assert big_pos - 0.10 <= MAX_CONFIDENCE_DELTA + 1e-9

    # Maximum negative scenario: huge base, zero model conf,
    # losing memories, pretell warning.
    big_neg = RisedualLearningCore._adjust_confidence(
        base_confidence=0.95,
        model_confidence=0.00,
        memory_win_rate=0.10,
        pretell_warning={"shift_type": "vol_spike", "similarity": 0.9},
        canonical_direction="LONG",
    )
    assert 0.95 - big_neg <= MAX_CONFIDENCE_DELTA + 1e-9


def test_adjusted_confidence_clamped_0_to_1():
    """Invariant 5 — final value never escapes [0, 1]."""
    # Try absurd inputs.
    for base in [0.0, 0.5, 1.0]:
        for model in [0.0, 0.5, 1.0]:
            for mwr in [None, 0.0, 0.5, 1.0]:
                for pretell in [None, {"shift_type": "x"}]:
                    for d in ["LONG", "SHORT", "HOLD", "UNKNOWN"]:
                        v = RisedualLearningCore._adjust_confidence(
                            base, model, mwr, pretell, d,
                        )
                        assert 0.0 <= v <= 1.0


# ─── orchestrator behaviour ─────────────────────────────────────


def test_train_batch_runs_and_returns_diagnostics():
    core = _core()
    rng = np.random.default_rng(0)
    X = rng.normal(size=(8, 8))
    y = rng.integers(0, 3, size=8)
    out = core.train_batch(X, y)
    assert out["batch_size"] == 8
    assert "accuracy" in out
    assert out["proto_drift"] >= 0.0


def test_add_resolved_memory_clusters_a_winning_long():
    core = _core()
    mem = _winning_memory("MEM-1", "AAPL", "LONG", pnl=4.2)
    out = core.add_resolved_memory(mem)
    assert out["regime_cluster_id"] is not None


def test_evaluate_context_returns_full_envelope():
    core = _core()
    out = core.evaluate_context(_ctx())
    for key in (
        "symbol", "direction_raw", "direction_canonical",
        "base_confidence", "model_confidence", "memory_win_rate",
        "adjusted_confidence", "current_regime", "pretell_warning",
        "similar_memory_count", "similar_memories", "notes",
    ):
        assert key in out


def test_pretell_warning_lowers_confidence_directly():
    base = 0.60
    no_warn = RisedualLearningCore._adjust_confidence(
        base_confidence=base,
        model_confidence=0.60,
        memory_win_rate=None,
        pretell_warning=None,
        canonical_direction="LONG",
    )
    with_warn = RisedualLearningCore._adjust_confidence(
        base_confidence=base,
        model_confidence=0.60,
        memory_win_rate=None,
        pretell_warning={"shift_type": "vol_spike"},
        canonical_direction="LONG",
    )
    assert with_warn < no_warn


def test_winning_memories_increase_long_confidence_within_cap():
    base = 0.50
    losing = RisedualLearningCore._adjust_confidence(
        base, 0.50, 0.20, None, "LONG",
    )
    winning = RisedualLearningCore._adjust_confidence(
        base, 0.50, 0.80, None, "LONG",
    )
    assert winning > losing
    assert winning - base <= MAX_CONFIDENCE_DELTA + 1e-9


def test_shim_aliases_are_canonical_classes():
    """RegimeLabeledMemory must be the canonical RegimeMemory.

    Re-imports both lazily because another test in the suite
    (``test_regime_memory_retrieval``) calls
    ``importlib.reload(services.regime_memory_retrieval)`` and would
    otherwise leave our top-of-file imports pointing at the
    pre-reload class identity.
    """
    import importlib
    import services.regime_memory_retrieval as _rmr_now
    import services.regime_clustering_layer as _rcl_now
    importlib.reload(_rcl_now)  # rebuild alias against current _rmr
    assert _rcl_now.RegimeLabeledMemory is _rmr_now.RegimeMemory


def test_cacl_predict_proba_sums_to_one():
    net = ConfusionAwareEmbeddingNetwork(
        input_dim=4, embedding_dim=8, n_prototypes=3,
    )
    emb = net.embed(np.array([[0.1, 0.2, 0.3, 0.4]]))
    p = net.predict_proba(emb)
    np.testing.assert_allclose(p.sum(axis=1), [1.0], atol=1e-9)


def test_auto_regime_tagger_classifies_extreme_vix():
    tagger = RegimeTagger()
    fp = tagger.generate_fingerprint(
        _macro(date="2026-02-15", vix=45.0),
        macro_history=None,
    )
    assert fp.vix_level == "extreme"


def test_auto_regime_tagger_pretell_returns_none_for_short_series():
    tagger = RegimeTagger()
    out = tagger.generate_pretell(
        current_date="2026-02-15",
        macro_series=[_macro("2026-02-15")],
        days_back=30,
    )
    assert out is None


# ─── helpers ────────────────────────────────────────────────────


def _winning_memory(
    mid: str, ticker: str, direction: str, pnl: float = 5.0,
) -> RegimeLabeledMemory:
    fp = RegimeFingerprint(
        vix_level="normal", yield_curve="flat",
        dxy_trend="strong", credit_spreads="tight",
        liquidity="normal", macro_phase="expansion",
    )
    return RegimeLabeledMemory(
        memory_id=mid,
        timestamp="2026-02-01T00:00:00Z",
        ticker=ticker,
        direction=direction,
        entry_price=100.0,
        exit_price=100.0 * (1 + pnl / 100),
        pnl_pct=pnl,
        holding_days=3,
        regime_at_entry=fp,
    )


def _seed_winning_memories(
    core: RisedualLearningCore,
    ticker: str,
    direction: str,
    n: int = 5,
) -> None:
    for i in range(n):
        core.add_resolved_memory(
            _winning_memory(f"M-{i}", ticker, direction, pnl=3.0 + i),
        )


# ─── shim regression: cluster ids match and dont double-append ──


def test_shim_does_not_double_append_to_canonical_engine():
    eng = RegimeClusteringEngine()
    mem = _winning_memory("MEM-X", "AAPL", "LONG", pnl=2.0)

    rid1 = eng.assign_regime_cluster(mem)
    rid2 = eng.assign_regime_cluster(mem)  # repeat
    pid1 = eng.detect_pretell_cluster(mem)
    _ = pid1

    assert rid1 == rid2
    # Internal: only one cluster, only one memory.
    report = eng.get_cluster_report()
    assert report["total_memories"] == 1
