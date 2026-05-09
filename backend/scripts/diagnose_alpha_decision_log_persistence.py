"""scripts/diagnose_alpha_decision_log_persistence.py — read-only.

Diagnoses why ``alpha_decision_log`` is barely populated despite
the live trading bots firing. Does NOT mutate, does NOT write,
does NOT touch the broker / executor / pipeline.

Usage::

    python -m scripts.diagnose_alpha_decision_log_persistence --window-hours 24
    python -m scripts.diagnose_alpha_decision_log_persistence --window-hours 168 --json out.json

Probes (read-only):
  * static call-site survey (which files invoke the persist hook?)
  * ADL TTL + index inspector
  * write breakdown by lane / decision / stage / symbol
  * per-lane gap analysis (paper trades vs ADL receipts)
  * recent supervisor-log scan for swallowed-exception warnings
  * 8-hypothesis classifier + actionable recommendations

Hard rules
----------
* READ-ONLY. No writes, no env mutation, no broker imports, no
  retrain execution, no Phase 6 changes.
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

from services.diagnostics.alpha_decision_log_persistence import (  # noqa: F401
    PERSIST_FUNCS,
    REASON_DECISION_ID_MISSING,
    REASON_DUPLICATE_KEY_COLLISION,
    REASON_ENV_FLAG_DISABLED,
    REASON_EXCEPTION_SWALLOWED,
    REASON_HOOK_CALLED_WRITE_SKIPPED,
    REASON_HOOK_NOT_CALLED,
    REASON_LANE_MISSING,
    REASON_SYMBOL_MISSING,
    REASON_TTL_PURGE,
    SOURCE_TAG,
    WARNING_PATTERNS,
    CallSite,
    CallSiteSurvey,
    GapAnalysis,
    LogScanResult,
    PersistenceDiagnostic,
    TTLProbe,
    WriteBreakdown,
    build_recommendations,
    classify_hypotheses,
    probe_gap_analysis,
    probe_ttl,
    probe_write_breakdown,
    scan_recent_logs,
    survey_call_sites,
)


DEFAULT_WINDOW_HOURS = 24
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # /app


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ── Async entry point ─────────────────────────────────────────


async def run_diagnostic(*, window_hours: int) -> PersistenceDiagnostic:
    """Build the full PersistenceDiagnostic.

    Connects via ``from server import db`` to pick up the live
    .env-loaded connection. Lazy import keeps unit tests independent.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    diag = PersistenceDiagnostic(
        window_hours=window_hours,
        git_sha=_git_sha(),
        timestamp=_utc_stamp(),
    )
    diag.call_sites = survey_call_sites(repo_root=REPO_ROOT)
    diag.log_scan = scan_recent_logs()

    try:
        from server import db as _server_db  # type: ignore
    except Exception:
        _server_db = None
    if _server_db is not None:
        diag.ttl = await probe_ttl(_server_db)
        diag.writes = await probe_write_breakdown(_server_db, cutoff=cutoff)
        diag.gaps_by_lane = await probe_gap_analysis(
            _server_db, cutoff=cutoff, writes=diag.writes,
        )

    classify_hypotheses(diag)
    build_recommendations(diag)
    return diag


# ── Output formatting ─────────────────────────────────────────


def _diag_to_dict(d: PersistenceDiagnostic) -> Dict[str, Any]:
    return {
        "source": SOURCE_TAG,
        "git_sha": d.git_sha,
        "timestamp": d.timestamp,
        "window_hours": d.window_hours,
        "call_sites": {
            func: [
                {"file": s.file, "line": s.line, "snippet": s.snippet}
                for s in sites
            ]
            for func, sites in d.call_sites.by_func.items()
        },
        "ttl": ({
            "ttl_seconds": d.ttl.ttl_seconds,
            "ttl_days": d.ttl.ttl_days,
            "fields": d.ttl.fields,
            "index_count": len(d.ttl.indexes),
        } if d.ttl else None),
        "writes": {
            "total": d.writes.total,
            "by_decision": d.writes.by_decision,
            "by_lane": d.writes.by_lane,
            "by_stage": d.writes.by_stage,
            "distinct_symbols": d.writes.distinct_symbols,
            "distinct_lane_keys": [
                {"symbol": s, "lane": ln}
                for s, ln in d.writes.distinct_lane_keys
            ],
        },
        "gaps_by_lane": [
            {"lane": g.lane,
             "paper_trades_in_window": g.paper_trades_in_window,
             "adl_in_window": g.adl_in_window,
             "expected_minimum": g.expected_minimum,
             "deficit": g.deficit,
             "deficit_ratio": g.deficit_ratio}
            for g in d.gaps_by_lane
        ],
        "log_scan": [
            {"pattern": r.pattern, "count": r.count} for r in d.log_scan
        ],
        "hypotheses": dict(d.hypotheses),
        "recommendations": list(d.recommendations),
    }


def print_report(d: PersistenceDiagnostic) -> None:
    print()
    print("─" * 72)
    print(f" alpha_decision_log persistence diagnostic · {SOURCE_TAG}")
    print(f" sha={d.git_sha}  ts={d.timestamp}  window_hours={d.window_hours}")
    print("─" * 72)

    print(" Static call-site survey:")
    for func, sites in d.call_sites.by_func.items():
        print(f"   {func}() — {len(sites)} caller(s)")
        for s in sites[:5]:
            print(f"     · {s.file}:{s.line}  {s.snippet[:90]}")
        if len(sites) > 5:
            print(f"     · ... (+{len(sites) - 5} more)")
    print()

    if d.ttl:
        ttl_str = (f"{d.ttl.ttl_days:.1f}d ({d.ttl.ttl_seconds}s)"
                   if d.ttl.ttl_seconds else "NONE")
        print(f" TTL index: {ttl_str}  fields={d.ttl.fields}  "
              f"total_indexes={len(d.ttl.indexes)}")
    else:
        print(" TTL index: <not probed — DB unavailable>")
    print()

    print(" Write breakdown (in window):")
    print(f"   total                  = {d.writes.total}")
    print(f"   distinct_symbols       = {d.writes.distinct_symbols}")
    if d.writes.by_decision:
        print(f"   by_decision            = {d.writes.by_decision}")
    if d.writes.by_lane:
        print(f"   by_lane                = {d.writes.by_lane}")
    if d.writes.by_stage:
        print(f"   by_stage               = {d.writes.by_stage}")
    if d.writes.distinct_lane_keys:
        keys_str = ", ".join(
            f"{s}/{ln}" for s, ln in d.writes.distinct_lane_keys[:8]
        )
        if len(d.writes.distinct_lane_keys) > 8:
            keys_str += f" ...(+{len(d.writes.distinct_lane_keys) - 8})"
        print(f"   distinct_keys          = {keys_str}")
    print()

    if d.gaps_by_lane:
        print(" Per-lane gap analysis (paper_trades vs ADL):")
        for g in d.gaps_by_lane:
            pct = g.deficit_ratio * 100.0
            print(f"   {g.lane:<8s} paper={g.paper_trades_in_window:<6d} "
                  f"adl={g.adl_in_window:<6d}  coverage={pct:.2f}%  "
                  f"deficit={g.deficit}")
        print()

    print(" Recent log scan (supervisor backend.*.log):")
    log_total = sum(r.count for r in d.log_scan)
    if log_total == 0:
        print("   (no persist-path warnings observed in recent logs)")
    else:
        for r in d.log_scan:
            if r.count > 0:
                print(f"   {r.count}x  {r.pattern}")
    print()

    print(" ── HYPOTHESIS PROBES ──")
    for k, v in d.hypotheses.items():
        print(f"   [{k}]  {v}")
    print()
    print(" ── RECOMMENDATIONS ──")
    for i, rec in enumerate(d.recommendations, 1):
        print(f"   ({i}) {rec}")
    print("─" * 72)
    print(" READ-ONLY diagnostic. No writes performed. No env mutation.")
    print(" No retrain, no broker calls, no artifact writes.")
    print("─" * 72)


# ── CLI ───────────────────────────────────────────────────────


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS)
    p.add_argument("--json", default=None,
                   help="Write the diagnostic JSON to this path.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    diag = asyncio.run(run_diagnostic(window_hours=args.window_hours))
    if args.json:
        Path(args.json).write_text(
            json.dumps(_diag_to_dict(diag), indent=2, default=str),
        )
    print_report(diag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
