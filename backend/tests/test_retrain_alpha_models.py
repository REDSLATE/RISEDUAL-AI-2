"""Tests for scripts/retrain_alpha_models.py.

Acceptance:
  1. no rows → clean NOT_ENOUGH_ROWS
  2. missing features skipped (counted in skip_reasons)
  3. artifact path is versioned (sha + timestamp)
  4. dry-run does not write artifact unless --write-artifact
  5. report schema stable
  6. no env mutation
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from scripts.retrain_alpha_models import (
    DEFAULT_MIN_ROWS,
    FEATURE_DIM,
    STATUS_NOT_ENOUGH_ROWS,
    STATUS_OK,
    SOURCE_TAG,
    ExtractionResult,
    TrainingRow,
    build_report,
    class_balance,
    expected_calibration_error,
    extract_rows,
    fit_classifier,
    label_from_outcome,
    parse_args,
    reconstruct_features,
    run_retrain,
)


# ── Helpers ──────────────────────────────────────────────────────


def _decision_doc(symbol="AAPL", lane="equity",
                  pos=0, neg=0, found=0,
                  intent="BUY", health=0.8) -> Dict[str, Any]:
    """Build a decision-log document mock with all 6 perception
    sub-scores so reconstruct_features can recover the 10-dim vector."""
    return {
        "symbol": symbol,
        "lane": lane,
        "perception": {
            "scores": {
                "event_shock":   {"score": 0.1, "confidence": 0.9},
                "regime":        {"score": 0.7, "confidence": 0.8},
                "drawdown":      {"score": 0.2, "confidence": 0.7},
                "liquidity":     {"score": 0.8, "confidence": 0.85},
                "system_health": {"score": health, "confidence": 0.9},
                "pacing":        {"score": 0.6, "confidence": 0.75},
            },
        },
        "shelly_recall": {"episodes_found": found,
                          "positive_count": pos,
                          "negative_count": neg},
        "extra": {"intent_hint": intent},
    }


def _trade(symbol="AAPL", lane="equity", pnl=10.0):
    return {"symbol": symbol, "lane": lane,
            "realized_pnl_usd": pnl,
            "closed_at": "2026-05-08T12:00:00+00:00"}


def _build_dataset(n: int) -> ExtractionResult:
    """Build n training rows with mixed outcomes so the RF actually
    has multi-class targets."""
    extraction = ExtractionResult()
    extraction.rows_scanned = n
    extraction.rows_used = n
    rng = np.random.default_rng(0)
    for i in range(n):
        # Vary features so the classifier has signal.
        feats = rng.uniform(0, 1, size=FEATURE_DIM).tolist()
        # Roughly balance labels: alternate BUY/SELL/NO_TRADE.
        strategist = i % 3
        auditor = 1 if strategist == 0 else 0  # SELL → BLOCK
        extraction.rows.append(TrainingRow(
            features=feats, strategist_label=strategist,
            auditor_label=auditor,
            symbol=f"SYM{i:03d}", lane="equity",
        ))
    return extraction


# ── (1) no rows → NOT_ENOUGH_ROWS ────────────────────────────────


def test_no_rows_returns_not_enough_rows(tmp_path):
    report = run_retrain(
        extraction=ExtractionResult(),
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=False,
    )
    assert report["status"] == STATUS_NOT_ENOUGH_ROWS
    assert report["rows_scanned"] == 0
    assert report["rows_used"] == 0
    assert report["strategist"] is None
    assert report["auditor"] is None
    assert report["artifacts"]["strategist_path"] is None


def test_below_min_rows_returns_not_enough_rows(tmp_path):
    extraction = _build_dataset(DEFAULT_MIN_ROWS - 1)
    report = run_retrain(
        extraction=extraction,
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=True,  # even with --write, status gates it
    )
    assert report["status"] == STATUS_NOT_ENOUGH_ROWS
    assert report["artifacts"]["strategist_path"] is None
    assert report["artifacts"]["auditor_path"] is None
    # Nothing must be written to disk.
    assert not (tmp_path / "models").exists() or \
        list((tmp_path / "models").iterdir()) == []


# ── (2) missing features skipped ─────────────────────────────────


def test_extract_rows_counts_missing_features():
    # decision doc with only 5 of the 6 perception keys → reconstruct
    # returns None, so the row must be classified as missing_features.
    bad_doc = {
        "symbol": "AAPL", "lane": "equity",
        "perception": {
            "scores": {
                "event_shock":   {"score": 0.1, "confidence": 0.9},
                "regime":        {"score": 0.7, "confidence": 0.8},
                "drawdown":      {"score": 0.2, "confidence": 0.7},
                "liquidity":     {"score": 0.8, "confidence": 0.85},
                "system_health": {"score": 0.9, "confidence": 0.9},
                # pacing missing → return None
            },
        },
    }
    trades = [_trade()]
    lookup = {("AAPL", "equity"): bad_doc}
    out = extract_rows(paper_trades=trades, decision_lookup=lookup)
    assert out.rows_scanned == 1
    assert out.rows_used == 0
    assert out.skip_reasons.get("missing_features") == 1


def test_extract_rows_classifies_other_skip_reasons():
    # missing_symbol + no_decision_log + missing_pnl
    trades = [
        {"realized_pnl_usd": 5.0},                                 # missing_symbol
        {"symbol": "TSLA", "lane": "equity", "realized_pnl_usd": 5.0},  # no_decision_log
        {"symbol": "AAPL", "lane": "equity"},                      # missing_pnl
    ]
    lookup = {("AAPL", "equity"): _decision_doc("AAPL")}
    out = extract_rows(paper_trades=trades, decision_lookup=lookup)
    assert out.rows_scanned == 3
    assert out.rows_used == 0
    assert out.skip_reasons == {
        "missing_symbol": 1,
        "no_decision_log": 1,
        "missing_pnl": 1,
    }


def test_reconstruct_features_returns_10_dim_vector():
    feats = reconstruct_features(_decision_doc(found=10, pos=4, neg=2))
    assert feats is not None
    assert len(feats) == FEATURE_DIM
    # All in [0, 1].
    assert all(0.0 <= f <= 1.0 for f in feats)


# ── (3) artifact path is versioned ───────────────────────────────


def test_artifact_path_is_versioned(tmp_path):
    extraction = _build_dataset(DEFAULT_MIN_ROWS + 5)
    report = run_retrain(
        extraction=extraction,
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=True,
    )
    assert report["status"] == STATUS_OK
    sp = report["artifacts"]["strategist_path"]
    ap = report["artifacts"]["auditor_path"]
    mp = report["artifacts"]["manifest_path"]
    assert sp and ap and mp
    # Format: strategist_<sha>_<ts>.joblib (sha alphanumeric, ts UTC stamp).
    assert re.search(
        r"strategist_[a-f0-9A-Z_]+_\d{8}T\d{6}Z\.joblib$", sp,
    ), sp
    assert re.search(
        r"auditor_[a-f0-9A-Z_]+_\d{8}T\d{6}Z\.joblib$", ap,
    ), ap
    assert Path(sp).exists()
    assert Path(ap).exists()
    # Manifest tags source.
    manifest = json.loads(Path(mp).read_text())
    assert manifest["source"] == SOURCE_TAG
    assert manifest["git_sha"] == report["git_sha"]
    assert manifest["timestamp"] == report["timestamp"]
    assert manifest["window_days"] == 14
    assert manifest["rows_used"] == extraction.rows_used


# ── (4) dry-run does not write artifact ──────────────────────────


def test_dry_run_does_not_write_artifact(tmp_path):
    extraction = _build_dataset(DEFAULT_MIN_ROWS + 5)
    report = run_retrain(
        extraction=extraction,
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=False,
    )
    assert report["status"] == STATUS_OK
    assert report["artifacts"]["write_artifact"] is False
    assert report["artifacts"]["strategist_path"] is None
    assert report["artifacts"]["auditor_path"] is None
    assert report["artifacts"]["manifest_path"] is None
    # Nothing written.
    assert not (tmp_path / "models").exists()


def test_cli_dry_run_is_default():
    args = parse_args(["--window-days", "7"])
    assert args.write_artifact is False
    assert args.window_days == 7

    args = parse_args(["--write-artifact"])
    assert args.write_artifact is True


# ── (5) report schema stable ─────────────────────────────────────


_REQUIRED_KEYS = {
    "status", "source", "git_sha", "timestamp", "window_days",
    "rows_scanned", "rows_used", "rows_skipped", "skip_reasons",
    "feature_count", "class_balance_strategist", "class_balance_auditor",
    "strategist", "auditor", "artifacts",
}
_REQUIRED_ARTIFACT_KEYS = {
    "write_artifact", "strategist_path", "auditor_path", "manifest_path",
}


def _assert_schema(report: Dict[str, Any]):
    assert _REQUIRED_KEYS.issubset(set(report.keys())), \
        f"missing keys: {_REQUIRED_KEYS - set(report.keys())}"
    assert _REQUIRED_ARTIFACT_KEYS.issubset(set(report["artifacts"].keys()))
    assert report["source"] == SOURCE_TAG
    assert isinstance(report["rows_scanned"], int)
    assert isinstance(report["rows_used"], int)
    assert isinstance(report["rows_skipped"], int)
    assert isinstance(report["skip_reasons"], dict)
    assert report["feature_count"] == FEATURE_DIM


def test_report_schema_stable_for_empty_input(tmp_path):
    report = run_retrain(
        extraction=ExtractionResult(),
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=False,
    )
    _assert_schema(report)


def test_report_schema_stable_for_dry_run(tmp_path):
    extraction = _build_dataset(DEFAULT_MIN_ROWS + 2)
    report = run_retrain(
        extraction=extraction,
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=False,
    )
    _assert_schema(report)
    assert report["strategist"] is not None
    assert report["auditor"] is not None
    for m in (report["strategist"], report["auditor"]):
        assert {"n_train", "n_test", "accuracy",
                "precision_weighted", "recall_weighted", "ece_10bin"}.issubset(m)


def test_report_schema_stable_for_write_artifact(tmp_path):
    extraction = _build_dataset(DEFAULT_MIN_ROWS + 2)
    report = run_retrain(
        extraction=extraction,
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=True,
    )
    _assert_schema(report)
    assert report["artifacts"]["strategist_path"] is not None


# ── (6) no env mutation ──────────────────────────────────────────


def test_no_env_mutation(tmp_path, monkeypatch):
    """Run a full training cycle and assert that the env vars the
    operator manually points later (STRATEGIST_ARTIFACT,
    AUDITOR_ARTIFACT, Phase 6 flags, broker flags) are untouched."""
    sentinel_keys = [
        "STRATEGIST_ARTIFACT",
        "AUDITOR_ARTIFACT",
        "PHASE6_ENFORCE_EQUITY",
        "PHASE6_ENFORCE_CRYPTO",
        "ALPACA_API_KEY",
        "KRAKEN_API_KEY",
    ]
    for k in sentinel_keys:
        monkeypatch.setenv(k, f"sentinel-{k}")
    before = {k: os.environ.get(k) for k in sentinel_keys}

    extraction = _build_dataset(DEFAULT_MIN_ROWS + 3)
    report = run_retrain(
        extraction=extraction,
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=True,
    )
    after = {k: os.environ.get(k) for k in sentinel_keys}
    assert before == after, f"env mutated! before={before} after={after}"
    # Report itself must not include any env values.
    blob = json.dumps(report)
    for k in sentinel_keys:
        assert f"sentinel-{k}" not in blob


# ── Bonus: pure helpers ─────────────────────────────────────────


def test_label_from_outcome():
    assert label_from_outcome({"realized_pnl_usd": 12.0}) == (2, 0)  # BUY, OK
    assert label_from_outcome({"realized_pnl_usd": -5.0}) == (0, 1)  # SELL, BLOCK
    assert label_from_outcome({"realized_pnl_usd": 0.0}) == (1, 0)  # NO_TRADE, OK
    assert label_from_outcome({"pnl_usd": 7.5}) == (2, 0)
    assert label_from_outcome({}) is None
    assert label_from_outcome({"realized_pnl_usd": "bad"}) is None
    assert label_from_outcome({"realized_pnl_usd": float("nan")}) is None


def test_class_balance():
    assert class_balance([0, 0, 1, 2, 2, 2]) == {"0": 2, "1": 1, "2": 3}
    assert class_balance([]) == {}


def test_expected_calibration_error_returns_float():
    probs = np.array([[0.7, 0.2, 0.1], [0.1, 0.8, 0.1], [0.3, 0.3, 0.4]])
    y_true = np.array([0, 1, 2])
    ece = expected_calibration_error(probs, y_true)
    assert ece is not None
    assert 0.0 <= ece <= 1.0


def test_fit_classifier_metrics_well_formed():
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(40, FEATURE_DIM))
    y = (X[:, 0] > 0.5).astype(int)
    _, m = fit_classifier(X=X, y=y, seed=0)
    assert m["n_train"] + m["n_test"] == 40
    assert m["accuracy"] is None or 0.0 <= m["accuracy"] <= 1.0


def test_build_report_handles_none_metrics(tmp_path):
    rep = build_report(
        status=STATUS_OK,
        extraction=ExtractionResult(),
        strategist_metrics=None, auditor_metrics=None,
        strategist_path=None, auditor_path=None, manifest_path=None,
        window_days=14, git_sha="abc", timestamp="20260508T120000Z",
        write_artifact=False,
    )
    _assert_schema(rep)
