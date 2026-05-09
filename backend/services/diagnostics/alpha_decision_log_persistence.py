"""Read-only probes for the alpha_decision_log persistence path.

Sibling to ``diagnose_alpha_retrain_join`` — investigates the
upstream cause of low ADL coverage. Strangler-split out of
``scripts/diagnose_alpha_decision_log_persistence`` (2026-05-09).
Same authority-boundary fence: read-only, no broker / executor /
pipeline imports, no env mutation, no Mongo writes.

Holds:
  * static call-site surveyor (which files call the persist hook?)
  * ADL write-volume probes (by lane, decision, stage, symbol)
  * paper-trade-vs-ADL gap analysis (which executors fire trades
    without producing a receipt?)
  * TTL / index inspector
  * recent-log scanner (counts ``[alpha_decision_log]`` /
    ``[ml.phase5a]`` warning patterns to detect swallowed
    exceptions).
  * dataclasses + reason-string constants
  * hypothesis classifier + recommendation builder
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── Constants ──────────────────────────────────────────────


SOURCE_TAG = "alpha_decision_log_persistence_diagnostic"

# Persist-hook entry points we hunt for in source. Anything that
# eventually leads to ``record_decision`` should appear here.
PERSIST_FUNCS = (
    "record_decision",
    "record_pipeline_decision",
    "run_shadow_pipeline",
)

# Reason buckets for missing-row classification.
REASON_HOOK_NOT_CALLED = "hook_not_called"
REASON_HOOK_CALLED_WRITE_SKIPPED = "hook_called_but_write_skipped"
REASON_EXCEPTION_SWALLOWED = "exception_swallowed"
REASON_ENV_FLAG_DISABLED = "env_flag_disabled"
REASON_LANE_MISSING = "lane_missing"
REASON_SYMBOL_MISSING = "symbol_missing"
REASON_DECISION_ID_MISSING = "decision_id_missing"
REASON_DUPLICATE_KEY_COLLISION = "duplicate_key_collision"
REASON_TTL_PURGE = "ttl_purge"

# Log patterns indicating the persist path emitted a warning.
WARNING_PATTERNS = (
    r"\[alpha_decision_log\] record_decision failed",
    r"\[alpha_decision_log\] ensure_indexes failed",
    r"\[alpha_decision_log\] summary failed",
    r"\[ml\.phase5a\] shadow pipeline failed",
    r"\[ml\.phase5a\] shadow wiring failed",
    r"\[ml\.phase5a\] roadguard_v2 eval failed",
    r"\[ml\.phase5b\] broker_wire failed",
)


# ── Dataclasses ──────────────────────────────────────────


@dataclass
class CallSite:
    file: str
    line: int
    snippet: str


@dataclass
class CallSiteSurvey:
    by_func: Dict[str, List[CallSite]] = field(default_factory=dict)

    def total_calls(self) -> int:
        return sum(len(v) for v in self.by_func.values())


@dataclass
class TTLProbe:
    indexes: List[Dict[str, Any]] = field(default_factory=list)
    ttl_seconds: Optional[int] = None
    ttl_days: Optional[float] = None
    fields: List[str] = field(default_factory=list)


@dataclass
class WriteBreakdown:
    total: int = 0
    by_decision: Dict[str, int] = field(default_factory=dict)
    by_lane: Dict[str, int] = field(default_factory=dict)
    by_stage: Dict[str, int] = field(default_factory=dict)
    distinct_symbols: int = 0
    distinct_lane_keys: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class GapAnalysis:
    """Per-lane comparison of paper-trade volume vs ADL volume."""
    lane: str
    paper_trades_in_window: int
    adl_in_window: int
    expected_minimum: int  # heuristic: 1 ADL per closed paper trade
    deficit: int
    deficit_ratio: float


@dataclass
class LogScanResult:
    pattern: str
    count: int


@dataclass
class PersistenceDiagnostic:
    window_hours: int
    git_sha: str
    timestamp: str
    call_sites: CallSiteSurvey = field(default_factory=CallSiteSurvey)
    ttl: Optional[TTLProbe] = None
    writes: WriteBreakdown = field(default_factory=WriteBreakdown)
    gaps_by_lane: List[GapAnalysis] = field(default_factory=list)
    log_scan: List[LogScanResult] = field(default_factory=list)
    hypotheses: Dict[str, str] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)


# ── Static call-site surveyor ────────────────────────────


def survey_call_sites(*, repo_root: Path) -> CallSiteSurvey:
    """grep the live source tree for every reference to the
    persist-hook entry points. Static, read-only.

    Returns one CallSite per call (file + line + snippet). Excludes
    the implementing modules themselves, comments, and tests.
    """
    survey = CallSiteSurvey(by_func={f: [] for f in PERSIST_FUNCS})
    backend = repo_root / "backend"
    if not backend.exists():
        return survey

    # Files we know are the implementations — excluded from the
    # "call site" tally so we count CALLERS, not the function defs.
    impl_files = {
        backend / "services/alpha_decision_log.py",
        backend / "services/ml/shadow_wiring.py",
    }

    for path in backend.rglob("*.py"):
        if path in impl_files:
            continue
        # Skip test trees and __pycache__
        if "__pycache__" in str(path):
            continue
        if "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        try:
            text = path.read_text()
        except Exception:  # noqa: BLE001
            continue
        for func in PERSIST_FUNCS:
            # Match ``func(`` or ``.func(`` (not ``def func``)
            for m in re.finditer(rf"(?<!def )\b{func}\(", text):
                line_no = text.count("\n", 0, m.start()) + 1
                snippet = (
                    text.splitlines()[line_no - 1].strip()
                    if 0 < line_no <= text.count("\n") + 1 else ""
                )
                # Skip comment-only lines
                if snippet.startswith("#") or '"""' in snippet[:3]:
                    continue
                survey.by_func.setdefault(func, []).append(CallSite(
                    file=str(path.relative_to(repo_root)),
                    line=line_no,
                    snippet=snippet[:200],
                ))
    return survey


# ── DB probes ────────────────────────────────────────────


async def probe_ttl(db: Any) -> TTLProbe:
    """Inspect the alpha_decision_log indexes. Read-only."""
    probe = TTLProbe()
    if db is None:
        return probe
    try:
        async for idx in db.alpha_decision_log.list_indexes():
            probe.indexes.append(dict(idx))
            ttl = idx.get("expireAfterSeconds")
            if ttl is not None:
                probe.ttl_seconds = int(ttl)
                probe.ttl_days = ttl / 86400.0
            keys = idx.get("key") or {}
            for k in keys:
                if k not in probe.fields:
                    probe.fields.append(k)
    except Exception:  # noqa: BLE001
        pass
    return probe


async def probe_write_breakdown(
    db: Any, *, cutoff: datetime,
) -> WriteBreakdown:
    """Aggregate counts for ADL rows in the window. Read-only."""
    out = WriteBreakdown()
    if db is None:
        return out
    try:
        out.total = await db.alpha_decision_log.count_documents({
            "created_at": {"$gte": cutoff},
        })
        if out.total == 0:
            return out

        pipeline = [
            {"$match": {"created_at": {"$gte": cutoff}}},
            {"$group": {
                "_id": {"decision": "$decision",
                        "lane": "$lane",
                        "blocked_at": "$blocked_at"},
                "n": {"$sum": 1},
            }},
        ]
        async for d in db.alpha_decision_log.aggregate(pipeline):
            grp = d["_id"]
            count = int(d["n"])
            dec = grp.get("decision") or "UNKNOWN"
            lane = grp.get("lane") or "UNKNOWN"
            stage = grp.get("blocked_at") or "<approved>"
            out.by_decision[dec] = out.by_decision.get(dec, 0) + count
            out.by_lane[lane] = out.by_lane.get(lane, 0) + count
            out.by_stage[stage] = out.by_stage.get(stage, 0) + count

        # Distinct symbols
        sym_pipeline = [
            {"$match": {"created_at": {"$gte": cutoff}}},
            {"$group": {"_id": "$symbol"}},
        ]
        out.distinct_symbols = 0
        async for _ in db.alpha_decision_log.aggregate(sym_pipeline):
            out.distinct_symbols += 1

        # Distinct (symbol, lane)
        lane_pipeline = [
            {"$match": {"created_at": {"$gte": cutoff}}},
            {"$group": {"_id": {"symbol": "$symbol", "lane": "$lane"}}},
        ]
        async for d in db.alpha_decision_log.aggregate(lane_pipeline):
            grp = d["_id"]
            sym = str(grp.get("symbol") or "").upper()
            lane = str(grp.get("lane") or "").lower()
            if sym:
                out.distinct_lane_keys.append((sym, lane))
    except Exception:  # noqa: BLE001
        pass
    return out


async def probe_gap_analysis(
    db: Any, *, cutoff: datetime, writes: WriteBreakdown,
) -> List[GapAnalysis]:
    """Compare paper-trade volume vs ADL volume per lane."""
    if db is None:
        return []
    gaps: List[GapAnalysis] = []
    coll_to_lane = {
        "paper_trades": "equity",
        "crypto_paper_trades": "crypto",
    }
    for coll, lane in coll_to_lane.items():
        try:
            n_trades = await db[coll].count_documents({
                "$or": [
                    {"closed_at": {"$gte": cutoff}},
                    {"updated_at": {"$gte": cutoff}},
                    {"opened_at": {"$gte": cutoff}},
                    {"created_at": {"$gte": cutoff}},
                ],
            })
        except Exception:  # noqa: BLE001
            n_trades = 0
        n_adl = writes.by_lane.get(lane, 0)
        # Heuristic minimum: 1 ADL per paper trade entry.
        # Reality is even higher (NO_TRADE decisions should also produce
        # receipts), but this is the minimum floor — if we don't even
        # have one ADL per entered trade, persistence is broken.
        expected_min = n_trades
        deficit = max(0, expected_min - n_adl)
        ratio = (n_adl / n_trades) if n_trades > 0 else 0.0
        gaps.append(GapAnalysis(
            lane=lane,
            paper_trades_in_window=n_trades,
            adl_in_window=n_adl,
            expected_minimum=expected_min,
            deficit=deficit,
            deficit_ratio=ratio,
        ))
    return gaps


def scan_recent_logs(
    *, log_paths: Tuple[str, ...] = (
        "/var/log/supervisor/backend.err.log",
        "/var/log/supervisor/backend.out.log",
    ),
    max_bytes: int = 2_000_000,
) -> List[LogScanResult]:
    """grep the recent supervisor logs for known persist-path
    warnings. Read-only file reads, capped by max_bytes per file.
    """
    counts: Dict[str, int] = {p: 0 for p in WARNING_PATTERNS}
    for log_path in log_paths:
        p = Path(log_path)
        if not p.exists():
            continue
        try:
            sz = p.stat().st_size
            with p.open("rb") as fh:
                if sz > max_bytes:
                    fh.seek(sz - max_bytes)
                data = fh.read().decode(errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for pat in WARNING_PATTERNS:
            counts[pat] += len(re.findall(pat, data))
    return [LogScanResult(pattern=p, count=n) for p, n in counts.items()]


# ── Hypothesis classifier ─────────────────────────────────


def classify_hypotheses(d: PersistenceDiagnostic) -> None:
    h: Dict[str, str] = {}
    total_calls = d.call_sites.total_calls()

    # 1. Hook never called?
    if total_calls == 0:
        h["A_persist_hook_never_called"] = (
            "YES — no source file calls record_decision / "
            "record_pipeline_decision / run_shadow_pipeline. The "
            "persistence path is dead at the source."
        )
    else:
        n_run_shadow = len(d.call_sites.by_func.get("run_shadow_pipeline", []))
        n_record = len(d.call_sites.by_func.get("record_decision", []))
        h["A_persist_hook_never_called"] = (
            f"NO — {total_calls} call sites total "
            f"(run_shadow_pipeline={n_run_shadow}, record_decision={n_record}). "
            "The hook IS reachable."
        )

    # 2. Single chokepoint?
    n_run_shadow = len(d.call_sites.by_func.get("run_shadow_pipeline", []))
    if n_run_shadow == 1:
        h["B_single_call_site_chokepoint"] = (
            "YES — only ONE caller invokes run_shadow_pipeline. Every "
            "executor that bypasses that call site (separate paper "
            "traders, day-trade pipeline, crypto bot) produces ZERO "
            "ADL coverage."
        )
    elif n_run_shadow == 0:
        h["B_single_call_site_chokepoint"] = (
            "CRITICAL — ZERO callers invoke run_shadow_pipeline. The "
            "shadow wiring is orphaned."
        )
    else:
        h["B_single_call_site_chokepoint"] = (
            f"NO — run_shadow_pipeline is invoked from {n_run_shadow} "
            "call sites, so coverage is broader than a single chokepoint."
        )

    # 3. Decision distribution skew (NO_TRADE vs APPROVED)?
    approved = d.writes.by_decision.get("APPROVED", 0)
    no_trade = d.writes.by_decision.get("NO_TRADE", 0)
    if d.writes.total > 0:
        if no_trade == 0 and approved > 0:
            h["C_no_trade_decisions_silently_dropped"] = (
                "LIKELY — every recorded receipt is APPROVED. The hook "
                "may only fire on the executor's success path, so "
                "NO_TRADE / HOLD decisions never produce receipts."
            )
        elif approved == 0 and no_trade > 0:
            h["C_no_trade_decisions_silently_dropped"] = (
                "INVERTED — every recorded receipt is NO_TRADE. "
                "APPROVED rows missing — possibly the executor's "
                "success branch isn't hitting the persist call."
            )
        else:
            h["C_no_trade_decisions_silently_dropped"] = (
                f"NO — {approved} APPROVED + {no_trade} NO_TRADE receipts present"
            )
    else:
        h["C_no_trade_decisions_silently_dropped"] = "UNKNOWN — no ADL rows"

    # 4. Lane drop?
    eq = d.writes.by_lane.get("equity", 0)
    cr = d.writes.by_lane.get("crypto", 0)
    if d.writes.total > 0:
        if eq == 0 or cr == 0:
            missing = "crypto" if cr == 0 else "equity"
            h["D_lane_imbalance"] = (
                f"YES — zero ADL receipts for the '{missing}' lane "
                "in window. That executor's persist path is broken."
            )
        else:
            h["D_lane_imbalance"] = (
                f"NO — equity={eq}, crypto={cr} (both lanes producing rows)"
            )
    else:
        h["D_lane_imbalance"] = "UNKNOWN — no ADL rows"

    # 5. Per-lane gap deficit
    gap_msgs = []
    for g in d.gaps_by_lane:
        if g.paper_trades_in_window == 0:
            continue
        if g.adl_in_window < g.paper_trades_in_window:
            pct = (g.adl_in_window / g.paper_trades_in_window) * 100.0
            gap_msgs.append(
                f"{g.lane}: {g.adl_in_window}/{g.paper_trades_in_window} "
                f"({pct:.1f}% — deficit {g.deficit})"
            )
    if gap_msgs:
        h["E_executor_writes_trades_without_receipts"] = (
            "YES — per-lane gap: " + "; ".join(gap_msgs) +
            ". This means at least one executor is firing trades that "
            "never reach the shadow_wiring hook."
        )
    else:
        h["E_executor_writes_trades_without_receipts"] = "NO"

    # 6. TTL pressure
    if d.ttl is None or d.ttl.ttl_seconds is None:
        h["F_ttl_purge_pressure"] = "UNKNOWN — no TTL probe"
    else:
        h["F_ttl_purge_pressure"] = (
            f"NO — TTL is {d.ttl.ttl_days:.1f} days, well above the "
            f"{d.window_hours/24:.1f}d window we're measuring"
            if d.ttl.ttl_days >= max(7, d.window_hours / 24)
            else f"YES — TTL {d.ttl.ttl_days:.1f}d may be deleting "
                 "rows before they're observed"
        )

    # 7. Swallowed exceptions in recent logs
    log_total = sum(r.count for r in d.log_scan)
    if log_total > 0:
        breakdown = "; ".join(
            f"{r.count}x {r.pattern[:50]}" for r in d.log_scan if r.count > 0
        )
        h["G_swallowed_exceptions_in_recent_logs"] = (
            f"YES — {log_total} warnings observed: {breakdown}"
        )
    else:
        h["G_swallowed_exceptions_in_recent_logs"] = (
            "NO — no persist-path warnings in recent supervisor logs "
            "(but the path swallows exceptions silently — absence of "
            "warnings is NOT proof of correctness)"
        )

    # 8. Coverage breadth
    if d.writes.total > 0 and d.writes.distinct_symbols < 5:
        h["H_narrow_symbol_coverage"] = (
            f"YES — only {d.writes.distinct_symbols} distinct symbols "
            "produced receipts. The persist path is firing for a tiny "
            "subset of the live universe."
        )
    elif d.writes.total > 0:
        h["H_narrow_symbol_coverage"] = (
            f"NO — {d.writes.distinct_symbols} distinct symbols covered"
        )
    else:
        h["H_narrow_symbol_coverage"] = "UNKNOWN — no ADL rows"

    d.hypotheses = h


def build_recommendations(d: PersistenceDiagnostic) -> None:
    recs: List[str] = []

    n_run_shadow = len(d.call_sites.by_func.get("run_shadow_pipeline", []))
    if n_run_shadow <= 1:
        recs.append(
            "PRIMARY: ``run_shadow_pipeline`` has only "
            f"{n_run_shadow} caller(s). Identify every executor that "
            "writes paper trades (signal-bot dispatcher, crypto paper "
            "trader, day-trade scanner/executor, options paper bot) "
            "and add a fire-and-forget call to the shadow wiring at "
            "each of those entry points. The hook MUST run on every "
            "decision, not just the legacy execute_signal path."
        )

    # Gap-driven recs
    crypto_gap = next(
        (g for g in d.gaps_by_lane if g.lane == "crypto"), None,
    )
    equity_gap = next(
        (g for g in d.gaps_by_lane if g.lane == "equity"), None,
    )
    if crypto_gap and crypto_gap.deficit_ratio < 0.1:
        recs.append(
            "Crypto executor (services/crypto_paper_trader.py) does "
            "NOT call run_shadow_pipeline — confirm by inspection, "
            "then add the same fire-and-forget pattern used in "
            "trading_bot_service.execute_signal."
        )
    if equity_gap and equity_gap.deficit_ratio < 0.1:
        recs.append(
            "Equity day-trade executor likely bypasses the shadow "
            "hook — check services/day_trade_*.py and the signal-bot "
            "dispatcher path. Same fire-and-forget call pattern."
        )

    no_trade = d.writes.by_decision.get("NO_TRADE", 0)
    approved = d.writes.by_decision.get("APPROVED", 0)
    if d.writes.total > 0 and no_trade == 0 and approved > 0:
        recs.append(
            "The hook only fires on the executor's success branch "
            "(``execute_signal`` after pre-flight gates pass). NO_TRADE "
            "decisions blocked at upstream gates (kill_switch, risk "
            "guard, sector cap, max_trades_per_day, RoadGuard ENFORCE) "
            "produce ZERO receipts. Move the persist call EARLIER — "
            "ideally after the pipeline runs but before any "
            "short-circuit return."
        )

    log_total = sum(r.count for r in d.log_scan)
    if log_total > 50:
        recs.append(
            f"Recent supervisor logs show {log_total} warnings on the "
            "persist path. Inspect /var/log/supervisor/backend.*.log "
            "for the exception messages — likely candidates: Mongo "
            "validation failure, FeatureFrame build failure, "
            "ensure_indexes never ran on a fresh DB."
        )

    if d.ttl and d.ttl.ttl_days is not None and d.ttl.ttl_days < 7:
        recs.append(
            f"TTL is only {d.ttl.ttl_days:.1f}d — historical context "
            "for retraining gets deleted aggressively. Consider raising "
            "to 30d (current code default) or 90d."
        )

    if not recs:
        recs.append("No blocking issues detected; ADL persistence is healthy.")
    d.recommendations = recs
