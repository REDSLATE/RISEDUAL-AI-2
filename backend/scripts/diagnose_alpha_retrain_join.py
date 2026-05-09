"""scripts/diagnose_alpha_retrain_join.py — read-only join diagnostic.

Diagnoses why ``scripts/retrain_alpha_models.py`` finds so few
trainable rows. Probes every hypothesis the operator listed
(2026-05-09) without writing to any collection, mutating env, or
touching the broker / executor / pipeline.

Usage::

    python -m scripts.diagnose_alpha_retrain_join --window-days 30
    python -m scripts.diagnose_alpha_retrain_join --window-days 7 --json out.json

Hard rules
----------
* READ-ONLY. No writes. No env mutation. No broker imports. No
  artifact writes. No retrain execution. No `client.drop_*`.
* Idempotent — run as often as you want.
* Reports raw counts + distributions plus a layered match-attempt
  with reason classification + sample rows (safe fields only).

The probe + classification + recommendation engine lives in the
sibling ``scripts/_diagnose_alpha_join_probes.py`` (split out
2026-05-09 to keep this script under the 400-line preferred
ceiling). Re-exported below so existing tests + CLI keep working.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.diagnostics.alpha_retrain_join import (  # noqa: F401
    REASON_DECISION_TOO_RECENT,
    REASON_LANE_MISMATCH,
    REASON_NO_DECISION_LOG,
    REASON_NO_SYMBOL,
    REASON_NORMALIZATION_MISS,
    REASON_PNL_MISSING,
    SAMPLES_PER_REASON,
    SOURCE_TAG,
    SYMBOL_FIELDS,
    CollectionProbe,
    DiagnosticResult,
    _attempt_match,
    _build_decision_log_candidates,
    _build_recommendations,
    _classify_hypotheses,
    _crypto_symbol_candidates,
    _detect_symbol_field,
    _normalize_symbol,
    _probe_collection,
    _probe_decision_log,
    _safe_field,
    _trade_lane,
    _trade_pnl,
)


DEFAULT_WINDOW_DAYS = 30


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ── Async entry point ─────────────────────────────────────────


async def run_diagnostic(*, window_days: int) -> DiagnosticResult:
    """Build the full DiagnosticResult.

    Connects via ``from server import db`` so we pick up the
    .env-loaded connection without re-loading dotenv ourselves.
    Lazy import keeps the test path independent of motor.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    result = DiagnosticResult(
        window_days=window_days,
        git_sha=_git_sha(),
        timestamp=_utc_stamp(),
    )

    try:
        from server import db as _server_db  # type: ignore
    except Exception:
        return result
    if _server_db is None:
        return result
    db = _server_db

    result.paper_trades = await _probe_collection(
        db, "paper_trades", cutoff,
    )
    result.crypto_paper_trades = await _probe_collection(
        db, "crypto_paper_trades", cutoff,
    )
    (
        result.decision_log,
        adl_oldest,
        adl_newest,
        distinct_keys,
    ) = await _probe_decision_log(db, cutoff)
    result.decision_log_oldest = (
        adl_oldest.isoformat() if isinstance(adl_oldest, datetime) else None
    )
    result.decision_log_newest = (
        adl_newest.isoformat() if isinstance(adl_newest, datetime) else None
    )
    if isinstance(adl_oldest, datetime) and isinstance(adl_newest, datetime):
        result.decision_log_span_days = (
            (adl_newest - adl_oldest).total_seconds() / 86400.0
        )
    result.decision_log_distinct_keys = distinct_keys

    decision_lookup = await _build_decision_log_candidates(db, cutoff)
    for coll in ("paper_trades", "crypto_paper_trades"):
        await _attempt_match(
            db, coll=coll, cutoff=cutoff,
            decision_lookup=decision_lookup,
            decision_log_oldest=adl_oldest,
            result=result,
        )

    _classify_hypotheses(result)
    _build_recommendations(result)
    return result


# ── Output formatting ─────────────────────────────────────────


def _safe_for_json(d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _result_to_dict(r: DiagnosticResult) -> Dict[str, Any]:
    """JSON-friendly export. No bson, no datetimes."""
    def _probe_dict(p: CollectionProbe) -> Dict[str, Any]:
        return {
            "name": p.name, "total": p.total, "in_window": p.in_window,
            "symbol_field": p.symbol_field,
            "has_lane_field": p.has_lane_field,
            "pnl_field": p.pnl_field,
            "sample_keys": p.sample_keys,
        }
    return {
        "source": SOURCE_TAG,
        "git_sha": r.git_sha,
        "timestamp": r.timestamp,
        "window_days": r.window_days,
        "paper_trades": _probe_dict(r.paper_trades),
        "crypto_paper_trades": _probe_dict(r.crypto_paper_trades),
        "alpha_decision_log": _probe_dict(r.decision_log),
        "alpha_decision_log_oldest": r.decision_log_oldest,
        "alpha_decision_log_newest": r.decision_log_newest,
        "alpha_decision_log_span_days": r.decision_log_span_days,
        "alpha_decision_log_distinct_keys": [
            {"symbol": s, "lane": ln} for s, ln in r.decision_log_distinct_keys
        ],
        "matched": r.matched,
        "unmatched": r.unmatched,
        "unmatched_by_reason": dict(r.unmatched_by_reason),
        "samples_by_reason": {
            k: [_safe_for_json(d) for d in v]
            for k, v in r.samples_by_reason.items()
        },
        "hypotheses": dict(r.hypotheses),
        "recommendations": list(r.recommendations),
    }


def print_report(r: DiagnosticResult) -> None:
    total_paper = r.paper_trades.in_window + r.crypto_paper_trades.in_window
    matched_pct = (r.matched / total_paper * 100.0) if total_paper else 0.0

    print()
    print("─" * 72)
    print(f" alpha-retrain join diagnostic · {SOURCE_TAG}")
    print(f" sha={r.git_sha}  ts={r.timestamp}  window_days={r.window_days}")
    print("─" * 72)
    print(" Paper-trade collections (in window):")
    for p in (r.paper_trades, r.crypto_paper_trades):
        print(f"   {p.name:<22s} total={p.total:<6d} in_window={p.in_window:<6d}"
              f"  symbol_field={p.symbol_field}  has_lane={p.has_lane_field}"
              f"  pnl_field={p.pnl_field}")
    print(f"   {'TOTAL_IN_WINDOW':<22s} {total_paper}")
    print()
    print(" alpha_decision_log:")
    print(f"   total       = {r.decision_log.total}")
    print(f"   in_window   = {r.decision_log.in_window}")
    print(f"   oldest      = {r.decision_log_oldest}")
    print(f"   newest      = {r.decision_log_newest}")
    if r.decision_log_span_days is not None:
        print(f"   span_days   = {r.decision_log_span_days:.2f}")
    distinct_str = ", ".join(f"{s}/{ln}" for s, ln in r.decision_log_distinct_keys[:8])
    if len(r.decision_log_distinct_keys) > 8:
        distinct_str += f" ...(+{len(r.decision_log_distinct_keys) - 8} more)"
    print(f"   distinct_keys = {distinct_str or '(none)'}")
    print()
    print(" ── HYPOTHESIS PROBES ──")
    for k, v in r.hypotheses.items():
        print(f"   [{k}]  {v}")
    print()
    print(" ── MATCH ATTEMPT ──")
    print(f"   matched     = {r.matched}  ({matched_pct:.2f}% of in_window)")
    print(f"   unmatched   = {r.unmatched}")
    if r.unmatched_by_reason:
        for reason, count in sorted(r.unmatched_by_reason.items(),
                                    key=lambda kv: -kv[1]):
            print(f"     · {reason:<32s} {count}")
    print()
    if r.samples_by_reason:
        print(" ── SAMPLE UNMATCHED ROWS (safe fields only) ──")
        for reason, samples in r.samples_by_reason.items():
            print(f"   reason='{reason}':")
            for s in samples:
                line = "  ".join(
                    f"{k}={v!r}" for k, v in s.items()
                    if k in ("_collection", "symbol", "ticker", "pair",
                             "lane", "asset_class", "closed_at",
                             "pnl_usd", "pnl", "status", "schema_version")
                )
                print(f"     · {line}")
        print()
    print(" ── RECOMMENDATIONS ──")
    for i, rec in enumerate(r.recommendations, 1):
        print(f"   ({i}) {rec}")
    print("─" * 72)
    print(" READ-ONLY diagnostic. No writes performed. No env mutation.")
    print(" No retrain, no broker calls, no artifact writes.")
    print("─" * 72)


# ── CLI ──────────────────────────────────────────────────────


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    p.add_argument("--json", default=None,
                   help="Write the diagnostic JSON to this path.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result = asyncio.run(run_diagnostic(window_days=args.window_days))
    if args.json:
        Path(args.json).write_text(
            json.dumps(_result_to_dict(result), indent=2, default=str),
        )
    print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
