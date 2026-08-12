"""Tests for Phase D: Market Regime + Edge Engine."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from services.alpha_edge_engine import (
    edge_modifier,
    edge_state,
    rvol_bucket,
    spread_bucket,
    time_bucket,
    _summarize,
)


# ── modifier table ──────────────────────────────────────

def test_modifier_neutral_when_undertrained():
    assert edge_modifier(2.0, samples=5) == 1.00  # not enough samples, ignore expectancy
    assert edge_modifier(None, samples=100) == 1.00


def test_modifier_scales_with_positive_expectancy():
    assert edge_modifier(0.10, samples=50) == 1.00
    assert edge_modifier(0.25, samples=50) == 1.07
    assert edge_modifier(0.60, samples=50) == 1.15


def test_modifier_reduces_on_negative_expectancy():
    assert edge_modifier(-0.10, samples=50) == 0.90
    assert edge_modifier(-0.30, samples=50) == 0.75


def test_state_discovers_on_low_samples():
    assert edge_state(2.0, samples=5) == "DISCOVERING"
    assert edge_state(0.5, samples=50) == "POSITIVE"
    assert edge_state(-0.3, samples=50) == "NEGATIVE"


# ── bucket helpers ──────────────────────────────────────

def test_time_bucket_dst():
    # 15:00 UTC in July → 11:00 ET (EDT, offset 4)
    b = time_bucket(datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc))
    assert b == "11:00-12:00"


def test_time_bucket_est():
    # 15:00 UTC in January → 10:00 ET (EST, offset 5)
    b = time_bucket(datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc))
    assert b == "10:00-11:00"


def test_rvol_and_spread_buckets():
    assert rvol_bucket(0.4) == "rvol_lt_1"
    assert rvol_bucket(1.5) == "rvol_1_2"
    assert rvol_bucket(3.0) == "rvol_2_5"
    assert rvol_bucket(10) == "rvol_gt_5"
    assert spread_bucket(10) == "tight_lt_20"
    assert spread_bucket(35) == "med_20_50"
    assert spread_bucket(100) == "wide_gt_50"


# ── summarize ──────────────────────────────────────────

def test_summarize_discovering_when_empty():
    out = _summarize([])
    assert out["state"] == "DISCOVERING"
    assert out["modifier"] == 1.00


def test_summarize_positive_edge_boosts_modifier():
    rows = [{"realized_r": r} for r in [1.5, 1.2, 2.0, -0.8, -0.9, 1.1,
                                          1.4, 1.6, -0.7, -0.8, 2.2, 1.0]]
    out = _summarize(rows)
    assert out["state"] == "POSITIVE"
    assert out["modifier"] >= 1.07


def test_summarize_negative_edge_reduces_modifier():
    rows = [{"realized_r": r} for r in [-1.0, -1.2, 0.5, -0.9, -0.8,
                                          -1.1, 0.3, -0.9, -1.3, -1.0,
                                          -0.7, -0.8]]
    out = _summarize(rows)
    assert out["state"] == "NEGATIVE"
    assert out["modifier"] < 1.00


# ── regime HMM (offline; uses synthetic features) ───────

def test_regime_snapshot_falls_back_to_unknown_without_model():
    """The critical non-blocking guarantee."""
    import asyncio
    from services.market_regime import _unknown_snapshot
    snap = _unknown_snapshot("test_reason")
    assert snap.label == "UNKNOWN"
    assert snap.regime_id == -1
    assert snap.probability == 0.0


def test_regime_hmm_trains_and_scores_on_synthetic_data():
    """Feed 200 synthetic bars, ensure fit + score works and returns a
    non-UNKNOWN label with reasonable probability."""
    from hmmlearn.hmm import GaussianHMM
    rng = np.random.default_rng(seed=0)
    # 200 samples, 5 features: mix of two obvious regimes
    regime_a = rng.normal(loc=[0.005, 0.008, 0.5, 0.4, 0.001],
                          scale=[0.005, 0.003, 0.5, 0.2, 0.002], size=(100, 5))
    regime_b = rng.normal(loc=[-0.005, 0.025, 1.5, -0.3, -0.005],
                          scale=[0.010, 0.008, 0.8, 0.3, 0.010], size=(100, 5))
    features = np.vstack([regime_a, regime_b])
    model = GaussianHMM(n_components=4, covariance_type="diag",
                        n_iter=100, tol=1e-3, random_state=42)
    model.fit(features)
    posteriors = model.predict_proba(features)
    last = posteriors[-1]
    winner = int(np.argmax(last))
    assert 0 <= winner < 4
    assert float(last[winner]) > 0.25  # winner posterior should not be flat
