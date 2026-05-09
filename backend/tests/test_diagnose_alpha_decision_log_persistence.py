"""Tests for the alpha_decision_log persistence diagnostic.

Pins:
  * pure helpers (call-site survey, log scan, hypothesis classifier,
    recommendation builder) behave as documented.
  * read-only firewall — no broker imports, no Mongo write verbs.
  * recommendations fire on the documented conditions.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.diagnostics.alpha_decision_log_persistence import (
    PERSIST_FUNCS,
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
    scan_recent_logs,
    survey_call_sites,
)


# ── Pure helpers ────────────────────────────────────────────


def test_persist_funcs_constant_includes_known_entry_points():
    """The trio we hunt for must remain stable. Adding a new persist
    helper should be a deliberate edit — pinning it here forces
    that decision."""
    assert "record_decision" in PERSIST_FUNCS
    assert "record_pipeline_decision" in PERSIST_FUNCS
    assert "run_shadow_pipeline" in PERSIST_FUNCS


def test_warning_patterns_cover_documented_failure_classes():
    """Each pattern must compile as valid regex (no accidental
    escape mistakes) and the set must include the documented
    persist-path warning prefixes."""
    must_include = (
        r"\[alpha_decision_log\] record_decision failed",
        r"\[ml\.phase5a\] shadow pipeline failed",
        r"\[ml\.phase5b\] broker_wire failed",
    )
    for needle in must_include:
        assert needle in WARNING_PATTERNS
    # All patterns must compile
    for p in WARNING_PATTERNS:
        re.compile(p)


def test_survey_call_sites_finds_known_callers(tmp_path: Path):
    """End-to-end probe with a synthetic source tree."""
    backend = tmp_path / "backend"
    backend.mkdir()
    # caller — should be picked up
    (backend / "services").mkdir()
    (backend / "services" / "caller_a.py").write_text(
        "def f():\n    record_decision(db, symbol='X')\n"
    )
    (backend / "services" / "caller_b.py").write_text(
        "import asyncio\nasyncio.create_task(run_shadow_pipeline(db))\n"
    )
    # implementing modules — must be EXCLUDED from caller count
    (backend / "services" / "ml").mkdir()
    (backend / "services" / "ml" / "shadow_wiring.py").write_text(
        "async def run_shadow_pipeline(db):\n    pass\n"
    )
    (backend / "services" / "alpha_decision_log.py").write_text(
        "async def record_decision(db, **kw):\n    pass\n"
    )
    # tests — must be EXCLUDED
    (backend / "tests").mkdir()
    (backend / "tests" / "test_x.py").write_text(
        "from services.alpha_decision_log import record_decision\n"
        "record_decision(db)\n"
    )
    # comment-only line — must be EXCLUDED
    (backend / "services" / "comment_only.py").write_text(
        "# example: record_decision(db, symbol='X')\n"
    )
    survey = survey_call_sites(repo_root=tmp_path)
    rd = survey.by_func["record_decision"]
    rsp = survey.by_func["run_shadow_pipeline"]
    assert any(s.file.endswith("caller_a.py") for s in rd)
    assert any(s.file.endswith("caller_b.py") for s in rsp)
    # impl files excluded
    for func in PERSIST_FUNCS:
        for s in survey.by_func.get(func, []):
            assert not s.file.endswith("alpha_decision_log.py")
            assert not s.file.endswith("shadow_wiring.py")
            # tests excluded
            assert "/tests/" not in s.file
            assert not s.file.endswith("test_x.py")


def test_scan_recent_logs_handles_missing_files(tmp_path: Path):
    """No log files → all counts = 0, no exceptions."""
    out = scan_recent_logs(log_paths=(str(tmp_path / "missing.log"),))
    assert all(r.count == 0 for r in out)
    assert {r.pattern for r in out} == set(WARNING_PATTERNS)


def test_scan_recent_logs_counts_pattern_hits(tmp_path: Path):
    log = tmp_path / "backend.err.log"
    log.write_text(
        "blah\n"
        "[alpha_decision_log] record_decision failed: oops\n"
        "[alpha_decision_log] record_decision failed: again\n"
        "[ml.phase5a] shadow pipeline failed: x\n"
        "irrelevant line\n"
    )
    out = scan_recent_logs(log_paths=(str(log),))
    by_pat = {r.pattern: r.count for r in out}
    assert by_pat[r"\[alpha_decision_log\] record_decision failed"] == 2
    assert by_pat[r"\[ml\.phase5a\] shadow pipeline failed"] == 1


# ── Hypothesis classifier ───────────────────────────────


def _build_diag(*, callers=None, writes=None, gaps=None,
                ttl_days=30.0, log_counts=None,
                window_hours=24) -> PersistenceDiagnostic:
    diag = PersistenceDiagnostic(
        window_hours=window_hours, git_sha="abc",
        timestamp="20260509T0000Z",
    )
    diag.call_sites = CallSiteSurvey(
        by_func=callers or {f: [] for f in PERSIST_FUNCS},
    )
    diag.writes = writes or WriteBreakdown()
    diag.gaps_by_lane = gaps or []
    diag.ttl = TTLProbe(ttl_seconds=int(ttl_days * 86400),
                        ttl_days=ttl_days)
    diag.log_scan = [
        LogScanResult(pattern=p, count=(log_counts or {}).get(p, 0))
        for p in WARNING_PATTERNS
    ]
    return diag


def test_classifier_detects_dead_persist_hook():
    diag = _build_diag(callers={f: [] for f in PERSIST_FUNCS})
    classify_hypotheses(diag)
    assert diag.hypotheses["A_persist_hook_never_called"].startswith("YES")
    assert "CRITICAL" in diag.hypotheses["B_single_call_site_chokepoint"]


def test_classifier_flags_single_chokepoint():
    """Mirrors the live state on 2026-05-09: one caller, ADL almost
    empty, all rows NO_TRADE."""
    diag = _build_diag(
        callers={
            "record_decision": [],
            "record_pipeline_decision": [
                CallSite(file="backend/routes/admin_ml/v2_pipeline.py",
                         line=78, snippet="..."),
            ],
            "run_shadow_pipeline": [
                CallSite(file="backend/services/trading_bot_service.py",
                         line=428, snippet="..."),
            ],
        },
        writes=WriteBreakdown(
            total=7, by_decision={"NO_TRADE": 7},
            by_lane={"equity": 4, "crypto": 3},
            by_stage={"perception": 7},
            distinct_symbols=2,
            distinct_lane_keys=[("AAPL", "equity"), ("BTC-USD", "crypto")],
        ),
        gaps=[
            GapAnalysis(lane="equity", paper_trades_in_window=185,
                        adl_in_window=4, expected_minimum=185,
                        deficit=181, deficit_ratio=0.0216),
            GapAnalysis(lane="crypto", paper_trades_in_window=214,
                        adl_in_window=3, expected_minimum=214,
                        deficit=211, deficit_ratio=0.014),
        ],
    )
    classify_hypotheses(diag)
    h = diag.hypotheses
    assert h["A_persist_hook_never_called"].startswith("NO")
    assert h["B_single_call_site_chokepoint"].startswith("YES")
    # Inverted: only NO_TRADE recorded
    assert h["C_no_trade_decisions_silently_dropped"].startswith("INVERTED")
    # Both lanes producing rows
    assert h["D_lane_imbalance"].startswith("NO")
    # Massive per-lane deficit
    assert h["E_executor_writes_trades_without_receipts"].startswith("YES")
    # TTL fine
    assert h["F_ttl_purge_pressure"].startswith("NO")
    # Narrow symbol coverage
    assert h["H_narrow_symbol_coverage"].startswith("YES")


def test_classifier_clean_state():
    diag = _build_diag(
        callers={
            "record_decision": [
                CallSite(file=f"f{i}.py", line=1, snippet="") for i in range(3)
            ],
            "record_pipeline_decision": [
                CallSite(file="x.py", line=1, snippet="")
            ],
            "run_shadow_pipeline": [
                CallSite(file=f"r{i}.py", line=1, snippet="") for i in range(4)
            ],
        },
        writes=WriteBreakdown(
            total=10000,
            by_decision={"APPROVED": 4000, "NO_TRADE": 6000},
            by_lane={"equity": 6000, "crypto": 4000},
            distinct_symbols=120,
        ),
        gaps=[
            GapAnalysis(lane="equity", paper_trades_in_window=2000,
                        adl_in_window=2400, expected_minimum=2000,
                        deficit=0, deficit_ratio=1.2),
            GapAnalysis(lane="crypto", paper_trades_in_window=1500,
                        adl_in_window=1700, expected_minimum=1500,
                        deficit=0, deficit_ratio=1.13),
        ],
    )
    classify_hypotheses(diag)
    h = diag.hypotheses
    assert h["A_persist_hook_never_called"].startswith("NO")
    assert h["B_single_call_site_chokepoint"].startswith("NO")
    assert h["C_no_trade_decisions_silently_dropped"].startswith("NO")
    assert h["D_lane_imbalance"].startswith("NO")
    assert h["E_executor_writes_trades_without_receipts"] == "NO"
    assert h["F_ttl_purge_pressure"].startswith("NO")
    assert h["H_narrow_symbol_coverage"].startswith("NO")


# ── Recommendations ────────────────────────────────────


def test_recommendations_fire_for_under_population():
    diag = _build_diag(
        callers={
            "record_decision": [],
            "record_pipeline_decision": [],
            "run_shadow_pipeline": [
                CallSite(file="backend/services/trading_bot_service.py",
                         line=428, snippet="..."),
            ],
        },
        writes=WriteBreakdown(
            total=7, by_decision={"NO_TRADE": 7},
            by_lane={"equity": 4, "crypto": 3},
            distinct_symbols=2,
        ),
        gaps=[
            GapAnalysis(lane="equity", paper_trades_in_window=185,
                        adl_in_window=4, expected_minimum=185,
                        deficit=181, deficit_ratio=0.0216),
            GapAnalysis(lane="crypto", paper_trades_in_window=214,
                        adl_in_window=3, expected_minimum=214,
                        deficit=211, deficit_ratio=0.014),
        ],
    )
    classify_hypotheses(diag)
    build_recommendations(diag)
    text = "\n".join(diag.recommendations)
    assert "PRIMARY" in text
    assert "run_shadow_pipeline" in text
    assert "crypto_paper_trader" in text
    assert "day_trade" in text


def test_recommendations_clean_state():
    diag = _build_diag(
        callers={
            "record_decision": [
                CallSite(file=f"f{i}.py", line=1, snippet="") for i in range(3)
            ],
            "record_pipeline_decision": [
                CallSite(file="x.py", line=1, snippet="")
            ],
            "run_shadow_pipeline": [
                CallSite(file=f"r{i}.py", line=1, snippet="") for i in range(4)
            ],
        },
        writes=WriteBreakdown(
            total=5000,
            by_decision={"APPROVED": 1500, "NO_TRADE": 3500},
            by_lane={"equity": 3000, "crypto": 2000},
            distinct_symbols=80,
        ),
        gaps=[
            GapAnalysis(lane="equity", paper_trades_in_window=1000,
                        adl_in_window=1100, expected_minimum=1000,
                        deficit=0, deficit_ratio=1.1),
            GapAnalysis(lane="crypto", paper_trades_in_window=800,
                        adl_in_window=900, expected_minimum=800,
                        deficit=0, deficit_ratio=1.125),
        ],
    )
    classify_hypotheses(diag)
    build_recommendations(diag)
    assert diag.recommendations == [
        "No blocking issues detected; ADL persistence is healthy."
    ]


# ── Read-only / no-write firewall ─────────────────────


_PROBES_PATH = Path(__file__).resolve().parent.parent.joinpath(
    "services/diagnostics/alpha_decision_log_persistence.py",
)
_SCRIPT_PATH = Path(__file__).resolve().parent.parent.joinpath(
    "scripts/diagnose_alpha_decision_log_persistence.py",
)


@pytest.mark.parametrize("path", [_PROBES_PATH, _SCRIPT_PATH],
                         ids=lambda p: p.name)
def test_diagnostic_has_no_authority_imports(path: Path):
    src = path.read_text()
    forbidden = [
        r"\bfrom services\.broker_service\b",
        r"\bfrom services\.trading_bot_service\b",
        r"\bfrom services\.crypto_paper_trader\b",
        r"\bfrom services\.paper_trading_service\b",
        r"\bfrom routes\.broker\b",
        r"\bfrom services\.ml\.executors\b",
        r"\bfrom services\.ml\.roadguard\b",
        r"\bfrom services\.ml\.fast_veto\b",
        r"\bfrom services\.ml\.shadow_wiring\b",
        r"\bfrom services\.ml\.pipeline\b",
        r"\bfrom services\.ml\.broker_wire\b",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, src), (
            f"{path.name} contains forbidden import {pattern}"
        )


@pytest.mark.parametrize("path", [_PROBES_PATH, _SCRIPT_PATH],
                         ids=lambda p: p.name)
def test_diagnostic_does_no_writes(path: Path):
    src = path.read_text()
    forbidden_verbs = [
        ".insert_one(", ".insert_many(", ".update_one(", ".update_many(",
        ".replace_one(", ".delete_one(", ".delete_many(",
        ".drop(", ".rename(", ".create_index(", ".bulk_write(",
        ".find_one_and_update(", ".find_one_and_delete(",
        ".find_one_and_replace(",
    ]
    for verb in forbidden_verbs:
        assert verb not in src, (
            f"{path.name} performs a write via {verb}"
        )


def test_diagnostic_does_not_mutate_env(monkeypatch):
    """Behavioural pin — running the classifiers must not touch env."""
    import os as _os
    sentinel = "SENTINEL_PERSISTENCE_DIAG_NOT_TOUCHED"
    monkeypatch.setenv("BROKER_LIVE_ORDER_ENABLED", sentinel)

    diag = PersistenceDiagnostic(
        window_hours=24, git_sha="x", timestamp="t",
    )
    classify_hypotheses(diag)
    build_recommendations(diag)
    assert _os.environ.get("BROKER_LIVE_ORDER_ENABLED") == sentinel


def test_runnable_diagnostic_can_run_without_db(monkeypatch):
    """``run_diagnostic`` must short-circuit cleanly when ``server.db``
    is unavailable. No exception escapes."""
    import asyncio
    import sys
    fake_server = type(sys)("server")
    fake_server.db = None
    sys.modules.pop("server", None)
    sys.modules["server"] = fake_server
    try:
        from scripts import diagnose_alpha_decision_log_persistence as mod
        diag = asyncio.run(mod.run_diagnostic(window_hours=12))
        assert diag.window_hours == 12
        # Static call-site survey works with no DB
        assert diag.call_sites.total_calls() >= 0
    finally:
        sys.modules.pop("server", None)
