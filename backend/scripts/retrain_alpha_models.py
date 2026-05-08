"""scripts/retrain_alpha_models.py — dry-run alpha retraining scaffold.

Phase 6 prep step. Generates fresh ``.joblib`` artifacts for the
Strategist + Auditor MLs from accumulated session data, prints a
one-page calibration report, and writes a training manifest. Does
**NOT** promote the artifacts — operator manually points
``STRATEGIST_ARTIFACT`` / ``AUDITOR_ARTIFACT`` at the new paths
after reviewing the report.

Usage::

    python -m scripts.retrain_alpha_models                         # dry-run
    python -m scripts.retrain_alpha_models --write-artifact        # actually persist
    python -m scripts.retrain_alpha_models --window-days 7         # custom window
    python -m scripts.retrain_alpha_models --json /tmp/report.json # machine-readable

Strict invariants (NO promotion path):
  * Default mode is dry-run.
  * Never reads or writes ``STRATEGIST_ARTIFACT``, ``AUDITOR_ARTIFACT``,
    Phase 6 flags, or broker flags.
  * Never calls a broker, executor, or pipeline.
  * Outputs use ``alpha_retrain`` source tag + git sha + timestamp.
  * Versioned paths: ``data/models/strategist_<sha>_<ts>.joblib``.

Inputs (read-only from Mongo):
  * ``paper_trades`` + ``crypto_paper_trades`` — outcome labels
    (realised P&L sign).
  * ``phase5b_intents`` — broker-wire receipts with pipeline context.
  * ``alpha_decision_log`` — Camaro/shadow pipeline receipts with the
    per-run perception/strategist diagnostics (the feature source).

The script is intentionally conservative: a row is included only
when we can reconstruct ALL 10 strategist features from the
decision-log entry and pair it with an outcome label.
"""
from __future__ import annotations

__domain__ = "PRD"

import argparse
import asyncio
import json
import logging
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np

logger = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────


SOURCE_TAG = "alpha_retrain"

# Strategist three-class label space (kept in lock-step with
# services/ml/strategist/base.py::_LABELS).
LABEL_SELL = 0
LABEL_NO_TRADE = 1
LABEL_BUY = 2

# Auditor two-class label space: 1 = block (audit failed),
# 0 = approve (audit ok).
AUDIT_OK = 0
AUDIT_BLOCK = 1

DEFAULT_WINDOW_DAYS = 14
DEFAULT_MIN_ROWS = 50
DEFAULT_OUT_DIR = Path("/app/backend/data/models")
FEATURE_DIM = 10


# ── Status strings (stable schema) ──────────────────────────────


STATUS_OK = "OK"
STATUS_NOT_ENOUGH_ROWS = "NOT_ENOUGH_ROWS"
STATUS_NO_DB = "NO_DB"


# ── Pure helpers (testable) ─────────────────────────────────────


@dataclass
class TrainingRow:
    """Single (features, strategist_label, auditor_label) tuple
    extracted from one (paper_trade, decision_log) pair."""
    features: List[float]
    strategist_label: int
    auditor_label: int
    symbol: str
    lane: str


@dataclass
class ExtractionResult:
    rows: List[TrainingRow] = field(default_factory=list)
    rows_scanned: int = 0
    rows_used: int = 0
    skip_reasons: Dict[str, int] = field(default_factory=dict)

    def add_skip(self, reason: str) -> None:
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, cwd="/app", timeout=5,
        )
        return out.decode().strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return f


def reconstruct_features(decision_doc: Dict[str, Any]) -> Optional[List[float]]:
    """Reconstruct the 10-dim Strategist feature vector from a
    persisted ``alpha_decision_log`` row.

    Returns ``None`` when any feature dim cannot be recovered
    (counted as ``missing_features`` upstream).
    """
    perception = (decision_doc.get("perception")
                  or decision_doc.get("perception_diagnostics"))
    if not isinstance(perception, dict):
        return None
    scores = perception.get("scores") or {}
    if not isinstance(scores, dict):
        return None

    keys = ("event_shock", "regime", "drawdown",
            "liquidity", "system_health", "pacing")
    feats: List[float] = []
    confs: List[float] = []
    for k in keys:
        sub = scores.get(k)
        if not isinstance(sub, dict):
            return None
        feats.append(_safe_float(sub.get("score")))
        confs.append(_safe_float(sub.get("confidence")))
    avg_conf = sum(confs) / len(confs) if confs else 0.0

    recall = decision_doc.get("shelly_recall") or {}
    found = max(1, int(_safe_float(recall.get("episodes_found"), 0)))
    pos_ratio = _safe_float(recall.get("positive_count")) / found
    neg_ratio = _safe_float(recall.get("negative_count")) / found

    intent = str((decision_doc.get("extra") or {}).get("intent_hint")
                 or decision_doc.get("intent_hint") or "BUY").upper()
    intent_encoded = 1.0 if intent == "BUY" else 0.0

    feats.extend([avg_conf, pos_ratio, neg_ratio, intent_encoded])
    if len(feats) != FEATURE_DIM:
        return None
    return feats


def label_from_outcome(trade: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Returns ``(strategist_label, auditor_label)``.

    Strategist label uses realised P&L sign:
      pnl > 0  → BUY
      pnl < 0  → SELL
      pnl ≈ 0  → NO_TRADE

    Auditor label maps any losing trade to BLOCK (the auditor's job
    is to spot losers before they fire). Returns ``None`` when the
    trade has no usable P&L signal.
    """
    pnl = trade.get("realized_pnl_usd")
    if pnl is None:
        pnl = trade.get("pnl_usd")
    if pnl is None:
        pnl = trade.get("pnl")
    if pnl is None:
        return None
    try:
        pnl_f = float(pnl)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pnl_f):
        return None
    if pnl_f > 1e-6:
        return LABEL_BUY, AUDIT_OK
    if pnl_f < -1e-6:
        return LABEL_SELL, AUDIT_BLOCK
    return LABEL_NO_TRADE, AUDIT_OK


def extract_rows(
    *,
    paper_trades: List[Dict[str, Any]],
    decision_lookup: Dict[Tuple[str, str], Dict[str, Any]],
) -> ExtractionResult:
    """Pure extractor. ``decision_lookup`` is keyed by
    ``(symbol_upper, lane)`` → most-recent matching decision doc.

    Counts every input row, classifies skips by reason, and
    returns the assembled training rows.
    """
    out = ExtractionResult()
    for trade in paper_trades:
        out.rows_scanned += 1
        symbol = str(trade.get("symbol") or "").upper()
        lane = str(trade.get("lane") or trade.get("market") or "equity").lower()
        if lane not in ("equity", "crypto"):
            lane = "equity"
        if not symbol:
            out.add_skip("missing_symbol")
            continue
        decision = decision_lookup.get((symbol, lane))
        if not decision:
            out.add_skip("no_decision_log")
            continue
        feats = reconstruct_features(decision)
        if feats is None:
            out.add_skip("missing_features")
            continue
        labels = label_from_outcome(trade)
        if labels is None:
            out.add_skip("missing_pnl")
            continue
        strategist_label, auditor_label = labels
        out.rows.append(TrainingRow(
            features=feats,
            strategist_label=strategist_label,
            auditor_label=auditor_label,
            symbol=symbol,
            lane=lane,
        ))
        out.rows_used += 1
    return out


def class_balance(labels: List[int]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for lbl in labels:
        counts[str(lbl)] = counts.get(str(lbl), 0) + 1
    return counts


def expected_calibration_error(probs: np.ndarray, y_true: np.ndarray,
                               *, bins: int = 10) -> Optional[float]:
    """Standard ECE on the predicted-class confidence."""
    if probs.ndim != 2 or probs.shape[0] == 0:
        return None
    pred_conf = probs.max(axis=1)
    pred_lbl = probs.argmax(axis=1)
    n = len(y_true)
    ece = 0.0
    for i in range(bins):
        lo = i / bins
        hi = (i + 1) / bins
        mask = (pred_conf > lo) & (pred_conf <= hi) if i > 0 else (pred_conf <= hi)
        if not mask.any():
            continue
        bin_acc = (pred_lbl[mask] == y_true[mask]).mean()
        bin_conf = pred_conf[mask].mean()
        ece += (mask.sum() / n) * abs(bin_acc - bin_conf)
    return float(ece)


def fit_classifier(
    *,
    X: np.ndarray, y: np.ndarray,
    seed: int = 4242,
    test_size: float = 0.25,
) -> Tuple[Any, Dict[str, Any]]:
    """Fit a single random-forest classifier and compute metrics."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import (
        accuracy_score, precision_recall_fscore_support,
    )
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(X))
    cut = max(1, int(len(X) * (1 - test_size)))
    train_idx, test_idx = perm[:cut], perm[cut:]
    X_tr, X_te = X[train_idx], X[test_idx]
    y_tr, y_te = y[train_idx], y[test_idx]

    clf = RandomForestClassifier(n_estimators=64, random_state=seed)
    clf.fit(X_tr, y_tr)

    if len(X_te) > 0:
        y_pred = clf.predict(X_te)
        proba = clf.predict_proba(X_te)
        acc = float(accuracy_score(y_te, y_pred))
        precision, recall, _, _ = precision_recall_fscore_support(
            y_te, y_pred, average="weighted", zero_division=0,
        )
        ece = expected_calibration_error(proba, y_te)
    else:
        acc = None
        precision = None
        recall = None
        ece = None
    metrics = {
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "accuracy": acc,
        "precision_weighted": float(precision) if precision is not None else None,
        "recall_weighted": float(recall) if recall is not None else None,
        "ece_10bin": ece,
    }
    return clf, metrics


# ── Mongo I/O (no env mutation) ─────────────────────────────────


async def _fetch_inputs(
    db: Any, *, window_days: int,
) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]]]:
    """Pull paper_trades + alpha_decision_log within window. Pure
    read-only; never modifies the database."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    trades: List[Dict[str, Any]] = []
    for coll in ("paper_trades", "crypto_paper_trades"):
        try:
            cursor = db[coll].find(
                {"$or": [
                    {"closed_at": {"$gte": cutoff}},
                    {"updated_at": {"$gte": cutoff}},
                    {"created_at": {"$gte": cutoff}},
                ]},
                projection={"_id": 0},
            )
            async for doc in cursor:
                trades.append(doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[retrain] read %s failed: %s", coll, exc)

    decision_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    try:
        cursor = db["alpha_decision_log"].find(
            {"created_at": {"$gte": cutoff}},
            projection={"_id": 0},
        ).sort("created_at", -1)
        async for doc in cursor:
            symbol = str(doc.get("symbol") or "").upper()
            lane = str(doc.get("lane") or "equity").lower()
            key = (symbol, lane)
            # Keep newest entry per (symbol, lane).
            decision_lookup.setdefault(key, doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[retrain] read alpha_decision_log failed: %s", exc)
    return trades, decision_lookup


# ── Report formatter ─────────────────────────────────────────────


def build_report(
    *,
    status: str,
    extraction: ExtractionResult,
    strategist_metrics: Optional[Dict[str, Any]],
    auditor_metrics: Optional[Dict[str, Any]],
    strategist_path: Optional[str],
    auditor_path: Optional[str],
    manifest_path: Optional[str],
    window_days: int,
    git_sha: str,
    timestamp: str,
    write_artifact: bool,
) -> Dict[str, Any]:
    """Stable schema. Never crashes on missing fields."""
    return {
        "status": status,
        "source": SOURCE_TAG,
        "git_sha": git_sha,
        "timestamp": timestamp,
        "window_days": window_days,
        "rows_scanned": extraction.rows_scanned,
        "rows_used": extraction.rows_used,
        "rows_skipped": extraction.rows_scanned - extraction.rows_used,
        "skip_reasons": dict(extraction.skip_reasons),
        "feature_count": FEATURE_DIM,
        "class_balance_strategist": class_balance(
            [r.strategist_label for r in extraction.rows],
        ),
        "class_balance_auditor": class_balance(
            [r.auditor_label for r in extraction.rows],
        ),
        "strategist": strategist_metrics,
        "auditor": auditor_metrics,
        "artifacts": {
            "write_artifact": bool(write_artifact),
            "strategist_path": strategist_path,
            "auditor_path": auditor_path,
            "manifest_path": manifest_path,
        },
    }


def print_report(report: Dict[str, Any]) -> None:
    print()
    print("─" * 64)
    print(f" alpha-retrain · {report['source']} · sha={report['git_sha']} · {report['timestamp']}")
    print(f" status: {report['status']}    window_days={report['window_days']}")
    print("─" * 64)
    print(f" rows scanned   : {report['rows_scanned']}")
    print(f" rows used      : {report['rows_used']}")
    print(f" rows skipped   : {report['rows_skipped']}")
    if report["skip_reasons"]:
        for reason, count in sorted(report["skip_reasons"].items(),
                                    key=lambda kv: -kv[1]):
            print(f"   · {reason:<24s} {count}")
    print(f" feature count  : {report['feature_count']}")
    print(f" class balance  : strategist={report['class_balance_strategist']}")
    print(f"                  auditor={report['class_balance_auditor']}")
    if report["strategist"]:
        m = report["strategist"]
        print(f" strategist     : train={m['n_train']} test={m['n_test']}")
        print(f"                  acc={m['accuracy']} "
              f"P={m['precision_weighted']} R={m['recall_weighted']} "
              f"ECE={m['ece_10bin']}")
    if report["auditor"]:
        m = report["auditor"]
        print(f" auditor        : train={m['n_train']} test={m['n_test']}")
        print(f"                  acc={m['accuracy']} "
              f"P={m['precision_weighted']} R={m['recall_weighted']} "
              f"ECE={m['ece_10bin']}")
    print(f" write_artifact : {report['artifacts']['write_artifact']}")
    if report["artifacts"]["strategist_path"]:
        print(f"   strategist : {report['artifacts']['strategist_path']}")
    if report["artifacts"]["auditor_path"]:
        print(f"   auditor    : {report['artifacts']['auditor_path']}")
    if report["artifacts"]["manifest_path"]:
        print(f"   manifest   : {report['artifacts']['manifest_path']}")
    print("─" * 64)
    print(" ⚠️  artifacts NOT promoted. Operator must manually point")
    print("    STRATEGIST_ARTIFACT / AUDITOR_ARTIFACT after review.")
    print("─" * 64)


# ── Orchestrator ─────────────────────────────────────────────────


def run_retrain(
    *,
    extraction: ExtractionResult,
    out_dir: Path,
    window_days: int,
    seed: int = 4242,
    write_artifact: bool = False,
) -> Dict[str, Any]:
    """Pure-ish entry point used by both the CLI and the tests.

    Accepts a pre-built ExtractionResult so unit tests can supply
    rows without going through Mongo.
    """
    git_sha = _git_sha()
    timestamp = _utc_stamp()

    if extraction.rows_used < DEFAULT_MIN_ROWS:
        return build_report(
            status=STATUS_NOT_ENOUGH_ROWS,
            extraction=extraction,
            strategist_metrics=None,
            auditor_metrics=None,
            strategist_path=None,
            auditor_path=None,
            manifest_path=None,
            window_days=window_days,
            git_sha=git_sha,
            timestamp=timestamp,
            write_artifact=write_artifact,
        )

    X = np.array([r.features for r in extraction.rows], dtype=float)
    y_strat = np.array([r.strategist_label for r in extraction.rows], dtype=int)
    y_audit = np.array([r.auditor_label for r in extraction.rows], dtype=int)

    strat_clf, strat_metrics = fit_classifier(X=X, y=y_strat, seed=seed)
    audit_clf, audit_metrics = fit_classifier(X=X, y=y_audit, seed=seed + 1)

    strategist_path: Optional[str] = None
    auditor_path: Optional[str] = None
    manifest_path: Optional[str] = None
    if write_artifact:
        out_dir.mkdir(parents=True, exist_ok=True)
        strategist_path = str(out_dir / f"strategist_{git_sha}_{timestamp}.joblib")
        auditor_path = str(out_dir / f"auditor_{git_sha}_{timestamp}.joblib")
        manifest_path = str(out_dir / f"manifest_{git_sha}_{timestamp}.json")
        joblib.dump(strat_clf, strategist_path)
        joblib.dump(audit_clf, auditor_path)
        manifest = {
            "source": SOURCE_TAG,
            "git_sha": git_sha,
            "timestamp": timestamp,
            "window_days": window_days,
            "feature_count": FEATURE_DIM,
            "rows_used": extraction.rows_used,
            "strategist_path": strategist_path,
            "auditor_path": auditor_path,
            "strategist_metrics": strat_metrics,
            "auditor_metrics": audit_metrics,
        }
        Path(manifest_path).write_text(json.dumps(manifest, indent=2))

    return build_report(
        status=STATUS_OK,
        extraction=extraction,
        strategist_metrics=strat_metrics,
        auditor_metrics=audit_metrics,
        strategist_path=strategist_path,
        auditor_path=auditor_path,
        manifest_path=manifest_path,
        window_days=window_days,
        git_sha=git_sha,
        timestamp=timestamp,
        write_artifact=write_artifact,
    )


# ── CLI ─────────────────────────────────────────────────────────


async def _main_async(args: argparse.Namespace) -> int:
    # Lazy DB import so the test file can run without motor.
    db = None
    try:
        from server import db as _server_db  # type: ignore
        db = _server_db
    except Exception as exc:  # noqa: BLE001
        logger.warning("[retrain] could not import server db: %s", exc)

    if db is None:
        report = build_report(
            status=STATUS_NO_DB,
            extraction=ExtractionResult(),
            strategist_metrics=None, auditor_metrics=None,
            strategist_path=None, auditor_path=None, manifest_path=None,
            window_days=args.window_days,
            git_sha=_git_sha(),
            timestamp=_utc_stamp(),
            write_artifact=args.write_artifact,
        )
    else:
        trades, decision_lookup = await _fetch_inputs(
            db, window_days=args.window_days,
        )
        extraction = extract_rows(
            paper_trades=trades, decision_lookup=decision_lookup,
        )
        report = run_retrain(
            extraction=extraction,
            out_dir=Path(args.out_dir),
            window_days=args.window_days,
            seed=args.seed,
            write_artifact=args.write_artifact,
        )

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
    print_report(report)
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    p.add_argument("--seed", type=int, default=4242)
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--write-artifact", action="store_true",
                   help="Persist the trained .joblib files. Default is dry-run.")
    p.add_argument("--json", default=None,
                   help="Write the report JSON to this path.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("ALPHA_RETRAIN_LOG_LEVEL", "INFO"),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
