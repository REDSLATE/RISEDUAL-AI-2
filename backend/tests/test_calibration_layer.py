"""Calibration Layer (Option A — IsotonicRegression) contract tests.

Pins the operator-decreed hard rules for the post-hoc confidence
calibration layer.

Hard rails pinned by these tests
--------------------------------
  1. Quarantined rows MUST NEVER reach the fit set.
  2. Non-binary outcomes (UNRESOLVED / NEUTRAL) are filtered out.
  3. Insufficient samples → fit refused, active calibrator
     untouched.
  4. ``apply()`` is total — never raises, always returns a fully
     populated metadata dict.
  5. Missing / stale / failing calibrator → fallback to raw
     confidence, ``calibration_applied=False``.
  6. The artifact directory layout is versioned (one joblib per
     fit, ``active.txt`` pointer).
  7. Module performs NO Mongo writes (static check).
  8. Module imports NO broker / executor / Strategist / Auditor /
     Commander / RoadGuard / FastVeto / kill-switch (static check).
  9. Reliability bins / ECE / Brier helpers are pure functions
     (no I/O).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
import pytest


@pytest.fixture
def tmp_calibration_dir(tmp_path, monkeypatch):
    """Point CALIBRATION_ARTIFACT_DIR at a fresh tmp dir per test
    so we never collide with the shipped models/calibrators tree.
    """
    target = tmp_path / "calibrators"
    target.mkdir()
    monkeypatch.setenv("CALIBRATION_ARTIFACT_DIR", str(target))
    monkeypatch.setenv("CALIBRATION_MIN_SAMPLES", "10")
    # Force-reimport so module-level path constants pick up env.
    import services.calibration_layer as cl
    importlib.reload(cl)
    cl._reset_active_cache()
    yield target
    # Cleanup: reload again so subsequent tests get the default.
    monkeypatch.delenv("CALIBRATION_ARTIFACT_DIR", raising=False)
    monkeypatch.delenv("CALIBRATION_MIN_SAMPLES", raising=False)
    importlib.reload(cl)


def _good_paper_row(
    *, confidence: float, win: bool,
    opened_offset_days: int = 1,
    source: str = "crypto_paper_bot",
    symbol: str = "BTC-USD",
    lane: str = "crypto",
    **overrides,
) -> dict:
    now = datetime.now(timezone.utc)
    base = {
        "trade_id": f"t-{confidence}-{win}",
        "symbol": symbol,
        "lane": lane,
        "source": source,
        "opened_at": (now - timedelta(days=opened_offset_days)).isoformat(),
        "closed_at": (
            now - timedelta(days=opened_offset_days - 1)
        ).isoformat(),
        "confidence": confidence,
        "direction": "LONG",
        "regime": "TREND_UP",
        "pnl_usd": 10.0 if win else -10.0,
    }
    base.update(overrides)
    return base


# ── Pure helpers ──────────────────────────────────────────────────────────────


def test_reliability_bins_match_input_count():
    from services.calibration_layer import reliability_bins
    raw = [0.05, 0.15, 0.25, 0.55, 0.95]
    out = [0,    0,    1,    1,    1]
    bins = reliability_bins(raw, out, n_bins=10)
    # At minimum, one bin per non-empty bucket.
    assert sum(b.count for b in bins) == len(raw)
    assert all(0.0 <= b.fraction_positive <= 1.0 for b in bins)


def test_reliability_bins_empty_input_returns_empty():
    from services.calibration_layer import reliability_bins
    assert reliability_bins([], []) == []


def test_ece_zero_for_perfectly_calibrated_input():
    from services.calibration_layer import expected_calibration_error
    # Predict 0.0 for losses, 1.0 for wins → 0 calibration error.
    raw = [0.0] * 100 + [1.0] * 100
    out = [0] * 100 + [1] * 100
    assert expected_calibration_error(raw, out) == pytest.approx(0.0)


def test_ece_high_for_overconfident_input():
    """Predict 0.95 for 100 outcomes that are 50/50 — ECE close to 0.45."""
    from services.calibration_layer import expected_calibration_error
    raw = [0.95] * 100
    out = [0, 1] * 50
    ece = expected_calibration_error(raw, out)
    assert ece > 0.4


def test_brier_zero_for_perfect_predictions():
    from services.calibration_layer import brier_score
    raw = [0.0, 1.0, 0.0, 1.0]
    out = [0,   1,   0,   1]
    assert brier_score(raw, out) == 0.0


def test_brier_max_for_inverted_predictions():
    from services.calibration_layer import brier_score
    # Predict 1.0 for 0-outcomes and 0.0 for 1-outcomes → Brier = 1.0.
    raw = [1.0, 0.0]
    out = [0,   1]
    assert brier_score(raw, out) == pytest.approx(1.0)


# ── Firewall integration ──────────────────────────────────────────────────────


def test_extract_training_pairs_excludes_quarantined_rows():
    """Quarantined rows (missing source / timestamps / symbol) MUST
    NEVER appear in the fit set."""
    from services.calibration_layer import extract_training_pairs
    good = _good_paper_row(confidence=0.8, win=True)
    bad_no_source = dict(good)
    bad_no_source["source"] = ""
    bad_no_symbol = dict(good)
    bad_no_symbol["symbol"] = ""
    rows = [good, bad_no_source, bad_no_symbol]
    raws, outs, total = extract_training_pairs(rows)
    assert total == 3
    # Only the well-formed row survives.
    assert len(raws) == 1
    assert raws[0] == pytest.approx(0.8)
    assert outs[0] == 1


def test_extract_training_pairs_excludes_unresolved_outcomes():
    """A trade with no pnl_usd / no closed_at is UNRESOLVED — must
    NOT contribute to the binary fit."""
    from services.calibration_layer import extract_training_pairs
    open_trade = _good_paper_row(confidence=0.7, win=True)
    open_trade["pnl_usd"] = None
    open_trade["status"] = "open"
    open_trade["closed_at"] = None
    raws, outs, total = extract_training_pairs([open_trade])
    assert total == 1
    assert len(raws) == 0


def test_extract_training_pairs_drops_out_of_range_confidence():
    from services.calibration_layer import extract_training_pairs
    weird = _good_paper_row(confidence=1.5, win=True)  # invalid
    rows = [weird, _good_paper_row(confidence=0.6, win=False)]
    raws, _outs, total = extract_training_pairs(rows)
    assert total == 2
    assert len(raws) == 1
    assert raws[0] == pytest.approx(0.6)


# ── Fit + persist ─────────────────────────────────────────────────────────────


def test_fit_rejects_below_min_samples(tmp_calibration_dir, monkeypatch):
    monkeypatch.setenv("CALIBRATION_MIN_SAMPLES", "100")
    import services.calibration_layer as cl
    importlib.reload(cl)
    rows = [_good_paper_row(confidence=0.7, win=True) for _ in range(5)]
    result = cl.fit_and_persist(rows)
    assert result.success is False
    assert "insufficient_samples" in result.rejected_reason
    # No artifact written.
    assert list(tmp_calibration_dir.iterdir()) == []
    # Active pointer absent.
    assert not (tmp_calibration_dir / "active.txt").exists()


def test_fit_succeeds_above_min_samples(tmp_calibration_dir):
    import services.calibration_layer as cl
    rows = []
    # 60 rows: confidence increases monotonically with win rate.
    for i in range(60):
        conf = 0.1 + (i / 60.0) * 0.8
        win = i >= 30
        rows.append(_good_paper_row(
            confidence=conf, win=win,
        ))
    result = cl.fit_and_persist(rows)
    assert result.success is True
    assert result.sample_count == 60
    assert result.model_version is not None
    assert Path(result.artifact_path).exists()
    # Active pointer is set.
    pointer = tmp_calibration_dir / "active.txt"
    assert pointer.exists()
    assert pointer.read_text().strip() == result.model_version


def test_fit_persists_metrics_pre_and_post(tmp_calibration_dir):
    import services.calibration_layer as cl
    rows = [
        _good_paper_row(confidence=0.9, win=False)
        for _ in range(30)
    ] + [
        _good_paper_row(confidence=0.9, win=True)
        for _ in range(30)
    ]
    result = cl.fit_and_persist(rows)
    assert result.success is True
    metrics = result.metrics
    assert "pre_ece" in metrics
    assert "post_ece" in metrics
    assert "pre_brier" in metrics
    assert "post_brier" in metrics
    # Post-fit ECE on the training set should never be worse than
    # pre-fit (isotonic is a Pareto improvement on the training
    # distribution by construction).
    assert metrics["post_ece"] <= metrics["pre_ece"] + 1e-9


# ── Apply (total, never raises) ───────────────────────────────────────────────


def test_apply_falls_back_when_no_calibrator(tmp_calibration_dir):
    """Empty artifact dir → apply returns calibrated == raw with
    calibration_applied=False."""
    import services.calibration_layer as cl
    cl._reset_active_cache()
    result = cl.apply(0.73)
    assert result.calibration_applied is False
    assert result.fallback_reason == "no_active_calibrator"
    assert result.calibrated_confidence == pytest.approx(0.73)
    assert result.raw_confidence == pytest.approx(0.73)


def test_apply_returns_calibrated_after_fit(tmp_calibration_dir):
    import services.calibration_layer as cl
    rows = (
        [_good_paper_row(confidence=0.2, win=False) for _ in range(30)]
        + [_good_paper_row(confidence=0.8, win=True) for _ in range(30)]
    )
    cl.fit_and_persist(rows)
    result = cl.apply(0.5)
    assert result.calibration_applied is True
    assert result.fallback_reason is None
    assert result.calibration_method == "isotonic"
    assert result.calibration_model_version is not None
    assert result.calibration_sample_count == 60
    assert 0.0 <= result.calibrated_confidence <= 1.0


def test_apply_clips_to_unit_interval(tmp_calibration_dir):
    """Out-of-range raw input falls back to raw=clipped, applied=False."""
    import services.calibration_layer as cl
    r1 = cl.apply(1.5)
    assert r1.calibration_applied is False
    assert r1.fallback_reason == "raw_out_of_range"
    r2 = cl.apply(-0.1)
    assert r2.calibration_applied is False
    assert r2.fallback_reason == "raw_out_of_range"


def test_apply_handles_non_finite_input(tmp_calibration_dir):
    import services.calibration_layer as cl
    r = cl.apply(float("nan"))
    assert r.calibration_applied is False
    assert r.fallback_reason == "raw_non_finite"


def test_apply_handles_unparseable_input(tmp_calibration_dir):
    import services.calibration_layer as cl
    r = cl.apply("not a number")
    assert r.calibration_applied is False
    assert r.fallback_reason == "raw_unparseable"


def test_apply_falls_back_when_calibrator_stale(
    tmp_calibration_dir, monkeypatch,
):
    """A stale artifact (>STALE_AFTER_HOURS) → fallback to raw."""
    import services.calibration_layer as cl
    rows = (
        [_good_paper_row(confidence=0.2, win=False) for _ in range(30)]
        + [_good_paper_row(confidence=0.8, win=True) for _ in range(30)]
    )
    cl.fit_and_persist(rows)
    # Hand-edit the persisted artifact so its fit_at is ancient.
    version = (tmp_calibration_dir / "active.txt").read_text().strip()
    artifact = tmp_calibration_dir / f"calibrator_{version}.joblib"
    payload = joblib.load(artifact)
    payload["fit_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=720)
    ).isoformat()
    joblib.dump(payload, artifact)
    cl._reset_active_cache()
    r = cl.apply(0.5)
    assert r.calibration_applied is False
    assert r.fallback_reason == "calibrator_stale"


def test_apply_falls_back_on_predict_exception(
    tmp_calibration_dir, monkeypatch,
):
    """Inject a fitted calibrator whose predict() raises — apply
    must catch it and fall back to raw."""
    import services.calibration_layer as cl
    rows = (
        [_good_paper_row(confidence=0.2, win=False) for _ in range(30)]
        + [_good_paper_row(confidence=0.8, win=True) for _ in range(30)]
    )
    cl.fit_and_persist(rows)
    # Replace the cached active payload with a model that raises.
    class _Boom:
        def predict(self, _):
            raise RuntimeError("synthetic failure")
    cl._active_payload_cache = {
        "model": _Boom(),
        "method": "isotonic",
        "version": "v-test",
        "fit_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": 60,
    }
    r = cl.apply(0.5)
    assert r.calibration_applied is False
    assert r.fallback_reason == "apply_exception"


# ── Reliability snapshot (Patent J card payload) ──────────────────────────────


def test_reliability_snapshot_zero_state(tmp_calibration_dir):
    import services.calibration_layer as cl
    snap = cl.reliability_snapshot([])
    assert snap["sample_count"] == 0
    assert snap["bins"] == []
    assert snap["apply_health"]["calibrator_loaded"] is False
    assert snap["apply_health"]["stale"] is True


def test_reliability_snapshot_after_fit(tmp_calibration_dir):
    import services.calibration_layer as cl
    rows = (
        [_good_paper_row(confidence=0.3, win=False) for _ in range(30)]
        + [_good_paper_row(confidence=0.7, win=True) for _ in range(30)]
    )
    cl.fit_and_persist(rows)
    snap = cl.reliability_snapshot(rows)
    assert snap["sample_count"] == 60
    assert snap["apply_health"]["calibrator_loaded"] is True
    assert snap["apply_health"]["stale"] is False
    assert len(snap["bins"]) > 0
    # Each bin is JSON-serializable scalars (no numpy types leaking).
    import json
    json.dumps(snap)


# ── Static authority firewall ─────────────────────────────────────────────────


def test_calibration_layer_has_no_mongo_writes():
    src = Path(
        "/app/backend/services/calibration_layer.py"
    ).read_text(encoding="utf-8")
    forbidden = [
        ".insert_one(", ".insert_many(", ".update_one(",
        ".update_many(", ".replace_one(", ".delete_one(",
        ".delete_many(", ".find_one_and_update(", ".bulk_write(",
        ".drop(",
    ]
    found = [t for t in forbidden if t in src]
    assert not found, f"calibration_layer attempted DB writes: {found}"


def test_calibration_layer_imports_no_execution_modules():
    src = Path(
        "/app/backend/services/calibration_layer.py"
    ).read_text(encoding="utf-8")
    forbidden = [
        "from services.broker_service",
        "from services.trading_bot_service",
        "from services.crypto_paper_trader",
        "from services.paper_trading_service",
        "from services.trading_agents",
        "from services.day_trade_scanner",
        "from routes.broker",
        "from routes.trading",
        "from services.ml.executors",
        "from services.ml.broker_wire",
        "from services.ml.shadow_wiring",
        "from services.ml.strategist",
        "from services.ml.auditor",
        "from services.fast_veto_layer",
        "from services.roadguard",
        "from services.confidence_gate",
        "from services.commander_decision_stream",
        "from ai_core.kill_switch",
        ".place_order(",
        "broker.execute(",
    ]
    found = [t for t in forbidden if t in src]
    assert not found, (
        f"calibration_layer imported forbidden token(s): {found}"
    )


def test_calibration_layer_does_not_emit_verdicts():
    src = Path(
        "/app/backend/services/calibration_layer.py"
    ).read_text(encoding="utf-8")
    # Cleared on docstring mentions (we use forbidden_tokens to
    # search code, not prose). Anything ending in ( is a call site.
    forbidden = [
        "set_active_strategy(",
        "promote_now(",
        "Verdict.BUY",
        "Verdict.SELL",
    ]
    found = [t for t in forbidden if t in src]
    assert not found, f"verdict-shaped tokens leaked: {found}"
