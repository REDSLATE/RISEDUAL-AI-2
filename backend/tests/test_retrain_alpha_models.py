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


# ──────────────────────────────────────────────────────────────────
# v2 schema — alpha_v2_fundamentals_technicals
# ──────────────────────────────────────────────────────────────────

from scripts.retrain_alpha_models import (  # noqa: E402
    DEFAULT_FEATURE_SCHEMA,
    FEATURE_DIM_V2,
    FEATURE_NAMES_BY_SCHEMA,
    FEATURE_NAMES_V1,
    FEATURE_NAMES_V2,
    FEATURE_NAMES_V2_FUNDAMENTALS,
    FEATURE_NAMES_V2_TECHNICALS,
    FEATURE_SCHEMA_V1,
    FEATURE_SCHEMA_V2,
    feature_dim_for,
    reconstruct_features_v2,
)


def test_v1_remains_default_schema():
    """If this fails, v1 production behaviour has shifted under us.
    The Strategist artifact (10-dim vector) requires v1 to stay
    canonical until a v2 inference adapter is wired."""
    assert DEFAULT_FEATURE_SCHEMA == FEATURE_SCHEMA_V1
    assert feature_dim_for(FEATURE_SCHEMA_V1) == FEATURE_DIM
    assert feature_dim_for(FEATURE_SCHEMA_V2) == FEATURE_DIM_V2
    assert FEATURE_DIM_V2 == 40
    assert FEATURE_NAMES_V2[:FEATURE_DIM] == FEATURE_NAMES_V1


def test_v2_feature_names_are_stable_and_ordered():
    """Manifest consumers depend on the order. Pin it explicitly so
    a future "tidy-up" reordering is caught immediately.
    """
    assert FEATURE_NAMES_V2_FUNDAMENTALS[0] == "fundamentals.pe_ratio"
    assert FEATURE_NAMES_V2_FUNDAMENTALS[-1] == "fundamentals.dividend_payer"
    assert FEATURE_NAMES_V2_TECHNICALS[0] == "technicals.rsi14"
    assert FEATURE_NAMES_V2_TECHNICALS[-1] == "technicals.macd_bearish"
    # Lookup table includes both schemas
    assert FEATURE_NAMES_BY_SCHEMA[FEATURE_SCHEMA_V1] == FEATURE_NAMES_V1
    assert FEATURE_NAMES_BY_SCHEMA[FEATURE_SCHEMA_V2] == FEATURE_NAMES_V2


def test_reconstruct_v2_equity_with_full_features_returns_40_dim():
    doc = _decision_doc(symbol="AAPL", lane="equity")
    doc["market"] = {
        "fundamentals": {
            "pe_ratio": 28.4, "forward_pe": 26.0, "peg_ratio": 2.1,
            "eps": 6.4, "dividend_yield": 0.005, "profit_margin": 0.25,
            "return_on_equity": 1.45, "price_to_book": 55.2, "beta": 1.25,
            "revenue_growth_yoy": 0.08, "earnings_growth_yoy": 0.12,
            "pe_in_value_band": 0, "pe_in_growth_band": 1,
            "pe_in_speculative": 0, "negative_eps": 0, "dividend_payer": 1,
        },
        "technicals": {
            "rsi14": 62.0, "macd": 0.5, "macd_signal": 0.3, "macd_hist": 0.2,
            "price_above_sma20": 1, "price_above_sma50": 1,
            "price_above_sma200": 1, "sma20_above_sma50": 1,
            "sma50_above_sma200": 1, "rsi_oversold": 0,
            "rsi_overbought": 0, "rsi_neutral": 1,
            "macd_bullish": 1, "macd_bearish": 0,
        },
    }
    result = reconstruct_features_v2(doc, "equity")
    assert result is not None
    feats, presence = result
    assert len(feats) == FEATURE_DIM_V2
    # Last dim is macd_bearish == 0
    assert feats[-1] == 0.0
    # Slot 11 (offset by 10 v1 features) is fundamentals.pe_ratio
    assert feats[FEATURE_DIM] == 28.4
    assert presence == {
        "fundamentals": True, "technicals": True,
        "expected_fundamentals": True,
    }


def test_reconstruct_v2_crypto_without_fundamentals_fills_zero():
    """Crypto rows MUST NOT require fundamentals — missing fields
    fill 0.0, presence flags fundamentals=False but the row is
    NOT lost. ``expected_fundamentals`` is False so the
    missing-fundamentals counter never bumps for crypto.
    """
    doc = _decision_doc(symbol="BTC-USD", lane="crypto")
    doc["market"] = {
        "technicals": {
            "rsi14": 55.0, "macd": 0.1, "macd_signal": 0.05, "macd_hist": 0.05,
            "price_above_sma20": 1, "price_above_sma50": 0,
            "price_above_sma200": 0, "sma20_above_sma50": 1,
            "sma50_above_sma200": 0, "rsi_oversold": 0,
            "rsi_overbought": 0, "rsi_neutral": 1,
            "macd_bullish": 1, "macd_bearish": 0,
        },
        # No fundamentals key at all — common on crypto rows
    }
    result = reconstruct_features_v2(doc, "crypto")
    assert result is not None
    feats, presence = result
    assert len(feats) == FEATURE_DIM_V2
    # All 16 fundamentals slots → 0.0
    fund_offset = FEATURE_DIM
    for i in range(16):
        assert feats[fund_offset + i] == 0.0, \
            f"crypto fundamentals slot {i} should be 0.0"
    assert presence["fundamentals"] is False
    assert presence["expected_fundamentals"] is False


def test_reconstruct_v2_equity_without_fundamentals_fills_zero():
    """Equity rows whose decision log lacks fundamentals are still
    trainable — the row is NOT skipped, slots fill 0.0, and the
    caller bumps ``missing_fundamentals_count``."""
    doc = _decision_doc(symbol="AAPL", lane="equity")
    doc["market"] = {"technicals": {"rsi14": 50.0}}
    result = reconstruct_features_v2(doc, "equity")
    assert result is not None
    feats, presence = result
    assert len(feats) == FEATURE_DIM_V2
    assert presence["fundamentals"] is False
    assert presence["expected_fundamentals"] is True


def test_reconstruct_v2_no_market_keys_at_all_fills_zero():
    """Old rows that pre-date the feature-builder wiring have no
    ``frame.market`` payload at all. They must still train under v2
    (slots fill 0.0). Ensures the v2 retrain doesn't suddenly
    abandon historical training data."""
    doc = _decision_doc(symbol="AAPL", lane="equity")
    # No "market" / "market_features" / "feature_frame" key
    result = reconstruct_features_v2(doc, "equity")
    assert result is not None
    feats, presence = result
    assert len(feats) == FEATURE_DIM_V2
    assert feats[FEATURE_DIM:] == [0.0] * 30
    assert presence == {
        "fundamentals": False, "technicals": False,
        "expected_fundamentals": True,
    }


def test_reconstruct_v2_returns_none_when_v1_base_unrecoverable():
    """If the perception sub-scores are missing, v2 must fail the
    SAME way v1 fails — return None, caller bumps
    ``missing_features``. v2 does NOT silently lower the bar.
    """
    doc = {"symbol": "AAPL", "lane": "equity"}  # no perception key
    assert reconstruct_features_v2(doc, "equity") is None


def test_extract_rows_v2_counts_missing_fundamentals_for_equity_only():
    """Build a mixed dataset and verify that:
      * equity rows without fundamentals bump ``missing_fundamentals``
      * crypto rows without fundamentals do NOT bump it
      * both lanes bump ``missing_technicals`` when technicals absent
    """
    from scripts.retrain_alpha_models import extract_rows

    decisions = {
        ("AAPL", "equity"): _decision_doc(symbol="AAPL", lane="equity"),
        ("MSFT", "equity"): _decision_doc(symbol="MSFT", lane="equity"),
        ("BTC-USD", "crypto"): _decision_doc(symbol="BTC-USD", lane="crypto"),
        ("ETH-USD", "crypto"): _decision_doc(symbol="ETH-USD", lane="crypto"),
    }
    # AAPL has both nested dicts; MSFT has only technicals;
    # BTC-USD has only technicals; ETH-USD has nothing.
    decisions[("AAPL", "equity")]["market"] = {
        "fundamentals": {"pe_ratio": 28.0},
        "technicals": {"rsi14": 50.0},
    }
    decisions[("MSFT", "equity")]["market"] = {
        "technicals": {"rsi14": 55.0},
    }
    decisions[("BTC-USD", "crypto")]["market"] = {
        "technicals": {"rsi14": 60.0},
    }
    # ETH-USD: no market key at all

    trades = [
        {"symbol": "AAPL", "lane": "equity", "realized_pnl_usd": 10,
         "closed_at": "2026-05-08"},
        {"symbol": "MSFT", "lane": "equity", "realized_pnl_usd": -5,
         "closed_at": "2026-05-08"},
        {"symbol": "BTC-USD", "lane": "crypto", "realized_pnl_usd": 100,
         "closed_at": "2026-05-08"},
        {"symbol": "ETH-USD", "lane": "crypto", "realized_pnl_usd": -50,
         "closed_at": "2026-05-08"},
    ]
    extraction = extract_rows(
        paper_trades=trades, decision_lookup=decisions,
        feature_schema=FEATURE_SCHEMA_V2,
    )
    # All 4 rows trained
    assert extraction.rows_used == 4
    # Only the 1 equity row missing fundamentals (MSFT) bumps the counter
    assert extraction.missing_fundamentals_count == 1
    # 1 row is missing technicals (ETH-USD)
    assert extraction.missing_technicals_count == 1


def test_extract_rows_v1_default_does_not_set_v2_counters():
    """v1 path leaves v2 counters at zero — pinning the
    additive-only contract."""
    from scripts.retrain_alpha_models import extract_rows

    decisions = {("AAPL", "equity"): _decision_doc(symbol="AAPL", lane="equity")}
    trades = [{"symbol": "AAPL", "lane": "equity",
               "realized_pnl_usd": 10, "closed_at": "2026-05-08"}]
    extraction = extract_rows(
        paper_trades=trades, decision_lookup=decisions,
    )  # no feature_schema arg → v1 default
    assert extraction.rows_used == 1
    assert extraction.missing_fundamentals_count == 0
    assert extraction.missing_technicals_count == 0


def test_v2_report_includes_schema_metadata(tmp_path):
    """Report payload exposes schema_version, count, names, counters."""
    extraction = _build_dataset(DEFAULT_MIN_ROWS + 2)
    # Force the v2 dim on the rows so run_retrain can fit
    for r in extraction.rows:
        r.features = r.features + [0.0] * (FEATURE_DIM_V2 - FEATURE_DIM)
    extraction.missing_fundamentals_count = 7
    extraction.missing_technicals_count = 3
    report = run_retrain(
        extraction=extraction,
        out_dir=tmp_path / "models",
        window_days=14,
        write_artifact=False,
        feature_schema=FEATURE_SCHEMA_V2,
    )
    assert report["status"] == STATUS_OK
    assert report["feature_schema_version"] == FEATURE_SCHEMA_V2
    assert report["feature_count"] == FEATURE_DIM_V2
    assert report["feature_count_v1_baseline"] == FEATURE_DIM
    assert report["feature_names"][:FEATURE_DIM] == FEATURE_NAMES_V1
    assert report["feature_names"][-1] == "technicals.macd_bearish"
    assert report["missing_fundamentals_count"] == 7
    assert report["missing_technicals_count"] == 3


def test_v2_artifact_manifest_records_schema_and_blocks_promotion(tmp_path):
    """Writing a v2 artifact must:
      * tag the filename with the schema name
      * include feature_schema_version + feature_names in manifest
      * set ``promotion_blocked_reason`` to a non-null operator-readable
        string so an inventory tool can NEVER mistake it for a v1
        artifact safe to promote.
    """
    extraction = _build_dataset(DEFAULT_MIN_ROWS + 2)
    for r in extraction.rows:
        r.features = r.features + [0.0] * (FEATURE_DIM_V2 - FEATURE_DIM)

    out_dir = tmp_path / "models"
    report = run_retrain(
        extraction=extraction,
        out_dir=out_dir,
        window_days=14,
        write_artifact=True,
        feature_schema=FEATURE_SCHEMA_V2,
    )
    manifest_path = report["artifacts"]["manifest_path"]
    assert manifest_path is not None
    manifest = json.loads(Path(manifest_path).read_text())
    assert manifest["feature_schema_version"] == FEATURE_SCHEMA_V2
    assert manifest["feature_count"] == FEATURE_DIM_V2
    assert manifest["feature_names"][-1] == "technicals.macd_bearish"
    assert manifest["promotion_blocked_reason"] == \
        "v2_schema_requires_matching_inference_adapter"
    # Filenames carry the schema tag (operator-readable)
    assert FEATURE_SCHEMA_V2 in manifest["strategist_path"]
    assert FEATURE_SCHEMA_V2 in manifest["auditor_path"]
    # v1 manifest field promotion_blocked_reason is None
    extraction_v1 = _build_dataset(DEFAULT_MIN_ROWS + 2)
    report_v1 = run_retrain(
        extraction=extraction_v1,
        out_dir=out_dir,
        window_days=14,
        write_artifact=True,
        # default v1
    )
    manifest_v1 = json.loads(Path(report_v1["artifacts"]["manifest_path"]).read_text())
    assert manifest_v1["feature_schema_version"] == FEATURE_SCHEMA_V1
    assert manifest_v1["promotion_blocked_reason"] is None
    # And the v1 filename does NOT carry the schema tag (back-compat)
    assert FEATURE_SCHEMA_V2 not in manifest_v1["strategist_path"]


def test_v2_cli_flag_parses():
    args = parse_args(["--feature-schema", FEATURE_SCHEMA_V2,
                       "--write-artifact"])
    assert args.feature_schema == FEATURE_SCHEMA_V2
    assert args.write_artifact is True

    args_default = parse_args([])
    assert args_default.feature_schema == FEATURE_SCHEMA_V1


def test_v2_no_broker_or_executor_imports():
    """The retrain script (and v2 reconstructor) MUST NOT import
    anything from the broker/executor/RoadGuard chain. Pinned by
    static check — same fence the feature builders sit behind.
    """
    src = Path(__file__).resolve().parent.parent.joinpath(
        "scripts/retrain_alpha_models.py"
    ).read_text()
    forbidden = [
        "from services.broker_service",
        "from services.trading_bot_service",
        "from services.crypto_paper_trader",
        "from services.paper_trading_service",
        "from routes.broker",
        "from services.ml.executors",
        "from services.ml.roadguard",
        "from services.ml.fast_veto",
        "from services.ml.shadow_wiring",
        "from services.ml.pipeline",
        "from services.ml.broker_wire",
        # Decision-issuing / authority verbs. Note: BUY / SELL /
        # NO_TRADE bare-string tokens ARE legitimate here — the
        # script encodes them as integer labels (intent_encoded
        # in v1; persisted decision-log lookup). It's the
        # ``Verdict.X``/``set_active``/``promote_now`` verbs
        # below that signal "this script issues decisions" —
        # which it MUST NEVER do.
        "Verdict.BUY", "Verdict.SELL", "Verdict.NO_TRADE",
        "set_active", "promote_now", ".place_order",
    ]
    for needle in forbidden:
        assert needle not in src, (
            f"retrain_alpha_models.py contains forbidden authority "
            f"marker '{needle}'"
        )


def test_v2_does_not_mutate_strategist_artifact_env(monkeypatch):
    """Pinned: running v2 retrain MUST NOT touch
    ``STRATEGIST_ARTIFACT`` / ``AUDITOR_ARTIFACT`` env vars even
    on --write-artifact. The artifact is written to disk only;
    pointing env at it is a separate, manual operator step.
    """
    sentinel_strat = "SENTINEL_STRAT_PATH"
    sentinel_audit = "SENTINEL_AUDIT_PATH"
    monkeypatch.setenv("STRATEGIST_ARTIFACT", sentinel_strat)
    monkeypatch.setenv("AUDITOR_ARTIFACT", sentinel_audit)

    extraction = _build_dataset(DEFAULT_MIN_ROWS + 2)
    for r in extraction.rows:
        r.features = r.features + [0.0] * (FEATURE_DIM_V2 - FEATURE_DIM)

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        run_retrain(
            extraction=extraction,
            out_dir=Path(td),
            window_days=14,
            write_artifact=True,
            feature_schema=FEATURE_SCHEMA_V2,
        )

    assert os.environ.get("STRATEGIST_ARTIFACT") == sentinel_strat
    assert os.environ.get("AUDITOR_ARTIFACT") == sentinel_audit
