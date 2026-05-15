"""
Source-file size lint — keep code reviewable.

Two-tier policy
───────────────
**Hard ceiling** (failing): files past this size stop being read end-
to-end during review, so subtle bugs accumulate silently.
  * Python  (.py)              — 800 lines.
  * Frontend (.jsx/.js/.ts/.tsx) — 500 lines.

Override the hard ceiling by adding the path to ``ALLOWLIST`` with a
one-line justification (reviewers gate the addition).

**Preferred ceiling** (drift indicator): module-type aware,
intentionally tighter than the hard cap. Reflects authority-boundary
expectations:

  * api-route          — 500 (one route file = one domain)
  * ui-tile            — 400 (one tile = one read-only view)
  * ui-component       — 500
  * ui-admin-component — 500
  * core-governance    — 600 (orchestrators that coordinate lanes)
  * shared-utility     — 300 (helper modules stay focused)
  * script             — 400
  * test               — 800
  * default            — 800

Existing files that already breach their preferred ceiling are
captured in ``PREFERRED_BASELINE``; the lint fails on **new**
breaches but grandfathers the snapshot. As old files shrink below
their preferred ceiling, baseline entries become stale and the
lint asks for them to be removed — ratcheting the bar tighter
over time.

Hard rules:
  * No allowlist expansion unless clearly justified.
  * Compatibility exports keep imports stable across splits.
  * No behavior changes from this lint itself — only diagnostics.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# ── Configuration: hard ceilings (failing) ─────────────────────────

PY_MAX_LINES: int = 800
FRONTEND_MAX_LINES: int = 500

_FRONTEND_EXTS = {".jsx", ".js", ".ts", ".tsx"}

# Directory components to skip entirely.
_SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    ".yarn",
    "build",
    "dist",
    ".next",
    ".pytest_cache",
    "__pycache__",
    ".emergent",
    "local",  # memory/local/ — gitignored
    "risedual_core.egg-info",
    "coverage",
}


# ── Configuration: preferred ceilings (drift indicator) ────────────


@dataclass(frozen=True)
class ModuleClass:
    label: str
    preferred: int
    matcher: Callable[[str], bool]


# Order matters — first match wins. More-specific patterns first.
_MODULE_CLASSES: tuple[ModuleClass, ...] = (
    ModuleClass(
        "api-route", 500,
        lambda p: p.startswith("backend/routes/"),
    ),
    ModuleClass(
        "ui-tile", 400,
        lambda p: (p.startswith("frontend/src/components/admin/")
                   and p.endswith("Tile.jsx")),
    ),
    ModuleClass(
        "ui-admin-component", 500,
        lambda p: p.startswith("frontend/src/components/admin/"),
    ),
    ModuleClass(
        "ui-component", 500,
        lambda p: p.startswith("frontend/src/components/"),
    ),
    ModuleClass(
        "core-governance", 600,
        lambda p: p.startswith("backend/services/"),
    ),
    ModuleClass(
        "script", 400,
        lambda p: p.startswith("backend/scripts/"),
    ),
    ModuleClass(
        "test", 800,
        lambda p: p.startswith("backend/tests/"),
    ),
    ModuleClass(
        "default", 800,
        lambda _p: True,
    ),
)


def _classify(rel_path: str) -> ModuleClass:
    for cls in _MODULE_CLASSES:
        if cls.matcher(rel_path):
            return cls
    # Last entry is the default catch-all; loop above always matches.
    return _MODULE_CLASSES[-1]


# ── Exempt patterns ───────────────────────────────────────────────
#
# Files matching any predicate here are skipped by BOTH lints
# (hard cap + preferred ceiling). Use sparingly — only for files
# whose size is structurally meaningless: generated code, schema
# snapshots, migration timelines, static constant registries.
#
# Each predicate must come with a one-line comment.
_EXEMPT_PREDICATES: tuple[Callable[[str], bool], ...] = (
    # Generated egg-info metadata (when not skipped by directory).
    lambda p: p.endswith(".egg-info"),
    # Migration timelines — chronological by design.
    lambda p: "/migrations/" in p,
    # Schema snapshot files — auto-generated and immutable.
    lambda p: p.endswith("_schema.py") or p.endswith(".schema.json"),
)


def _is_exempt(rel_path: str) -> bool:
    return any(pred(rel_path) for pred in _EXEMPT_PREDICATES)


# ── Hard-cap allowlist (existing) ─────────────────────────────────
#
# Files allowed to exceed the HARD ceiling. Reviewers gate every
# addition. Entries documenting refactor debt should track the
# planned split so the debt doesn't drift.
ALLOWLIST: dict[str, str] = {
    # ── Backend Python — refactor debt (planned splits) ────────────
    "backend/routes/admin.py": (
        "REFACTOR DEBT: 3480 lines of multi-domain owner endpoints "
        "(users, billing, terminal, shadow, adversarial, sovereign). "
        "Planned split into routes/admin/{domain}.py per the "
        "scalable-production-ready roadmap."
    ),
    "backend/server.py": (
        "REFACTOR DEBT: monolithic FastAPI bootstrap + lifespan + "
        "scheduler wiring. Planned split into server/{app,scheduler,"
        "lifespan}.py — explicitly listed in the system-prompt "
        "refactoring section."
    ),
    # ── Backend Python — mature multi-endpoint route modules ───────
    "backend/routes/broker.py": (
        "Multi-broker route surface (Alpaca, Kraken, Coinbase, "
        "paper) — per-broker endpoint clusters share request "
        "validation that's awkward to duplicate across files."
    ),
    "backend/routes/ai.py": (
        "AI-chat + prediction + multi-model-consensus endpoint "
        "cluster — shares per-request rate-limit + cost-tracking "
        "helpers tightly enough that splitting would force circular "
        "imports."
    ),
    "backend/routes/options_trading.py": (
        "Options-flow routes (chain, greeks, narrative, IV surface) "
        "share an Alpaca-options client init + cache that doesn't "
        "split cleanly."
    ),
    # ── Backend Python — mature domain services ────────────────────
    "backend/services/ml_paper_trader.py": (
        "Single-purpose equity ML paper-trade orchestrator that "
        "sequences ~12 mandatory gates (options, abandonment, "
        "confidence, regime, brake, sovereign, etc.) plus the row "
        "write + idempotency. Splitting fragments the gate-order "
        "discipline that the trade-correctness invariants depend on."
    ),
    "backend/services/trading_bot_service.py": (
        "Bot lifecycle + tick loop + risk-budget accounting. "
        "Tightly coupled — splitting would require exposing internal "
        "state across module boundaries."
    ),
    "backend/services/model_adaptation.py": (
        "ML adaptation engine — parametric scan, weight-update "
        "policy, shadow-mode logger. Single cohesive subsystem; "
        "splitting fragments the parametric-scan invariants."
    ),
    "backend/services/market_memory_service.py": (
        "Vector-memory store with multiple retrieval strategies "
        "(semantic, recency, regime) sharing the same embedding "
        "cache."
    ),
    "backend/services/broker_service.py": (
        "Multi-broker abstraction (Alpaca + Kraken + paper) — "
        "per-broker adapters share order-state normalisation that "
        "splitting would duplicate."
    ),
    "backend/services/prediction_tracker.py": (
        "Source of truth for canonical_ai_dir + grade_prediction + "
        "STRONG_*/WEAK_* taxonomy. Single ownership boundary by "
        "design (see test_no_local_direction_tuples ALLOWLIST entry)."
    ),
    "backend/services/crypto_paper_trader.py": (
        "Crypto entry/exit/SL-TP/sizing pipeline with "
        "Bull/Bear/Commander integration — splitting fragments the "
        "trade-row schema invariants the closer reads."
    ),
    "backend/services/terminal_aggregator.py": (
        "Top-actions aggregator — single read-only fan-out across "
        "signals + positions + adversarial + catalyst. Splitting "
        "would create N nearly-identical Mongo-projection helpers."
    ),
    "backend/services/sec_13f_service.py": (
        "SEC 13F ingestion + smart-money score + per-institution "
        "dedup. Single ETL pipeline; splitting would scatter the "
        "schema-version handling."
    ),
    "backend/services/natural_language_trading.py": (
        "Self-contained single-file NL trading layer (debate engine "
        "+ command parser + FastAPI router). Documented as a "
        "drop-in module in its own header comment."
    ),
    "backend/services/ml_retrain_service.py": (
        "Nightly retrain pipeline — feature builder + walk-forward "
        "trainer + champion/challenger evaluator. Splits awkwardly "
        "around shared sklearn pipeline objects."
    ),
    "backend/services/research_shadow_stats.py": (
        "Tier-3 readiness aggregator — single owner-readable "
        "endpoint payload. Splitting would force re-aggregation "
        "across helpers."
    ),
    "backend/services/email_service.py": (
        "Multi-template transactional email router (Resend) — each "
        "template's HTML lives inline as a string, which is the "
        "majority of the line count."
    ),
    # ── Backend Python — comprehensive test suite ─────────────────
    "backend/tests/test_research_shadow.py": (
        "Comprehensive shadow-engine test suite — pins the entire "
        "tier-3 readiness contract end-to-end across 50+ scenarios."
    ),
    # ── Top-level scripts ─────────────────────────────────────────
    "generate_pdf.py": (
        "One-off PDF generator with marketing/spec content as inline "
        "string blobs. Refactoring it just hides the content."
    ),
    # ── Frontend JSX — known-large composite UIs ───────────────────
    "frontend/src/components/BrokerConnect.jsx": (
        "Per-broker connection flow (Alpaca, Kraken, Coinbase, "
        "Robinhood) with broker-specific OAuth + form variants in a "
        "single switch-driven component. Splitting would duplicate "
        "the shared connection-state hook."
    ),
    "frontend/src/components/UserWorkspace.jsx": (
        "Top-level authenticated workspace — tab router + global "
        "shortcuts + onboarding overlay. Single mount point by "
        "design; tab contents are already separate components."
    ),
    "frontend/src/components/LandingPage.jsx": (
        "Marketing landing — multiple inline content sections "
        "(hero, pricing, FAQ, testimonials) intentionally colocated "
        "for SEO/SSR consistency."
    ),
    "frontend/src/components/AgentActivityFeed.jsx": (
        "Live agent activity feed — handles 6+ event types with "
        "per-type rendering + ordering. Splitting per-event would "
        "fragment the unified scroll-virtualisation."
    ),
    "frontend/src/components/LegalPages.jsx": (
        "ToS/Privacy/Risk-Disclosure content as inline JSX — legal "
        "review pins the wording, not the structure."
    ),
    "frontend/src/components/admin/MemoryDriftCard.jsx": (
        "Composite admin diagnostics card with 5 sub-sections "
        "(drift score, retrain history, shadow lag, vector "
        "freshness, alerts). Sub-sections share the same fetch."
    ),
    "frontend/src/components/admin/ModelAdaptationsPanel.jsx": (
        "Admin panel for ML adaptation shadow stats — single fetch, "
        "multiple chart sections."
    ),
    "frontend/src/components/admin/GuardShadowPanel.jsx": (
        "Multi-engine guard-shadow status panel — Council, "
        "Adversarial, Sovereign, Regime weights. Splitting per-"
        "engine would fragment the shared readiness fetch."
    ),
    "frontend/src/components/admin/MLHealthStrip.jsx": (
        "Composite ML-health strip — sparklines + alert chips + "
        "retrain-cadence indicator share one polling loop."
    ),
}


# ── Preferred-ceiling baseline ────────────────────────────────────
#
# Snapshot of files that breach their preferred (module-type-aware)
# ceiling at the moment this lint was tightened. New files in the
# corresponding paths must respect the preferred ceiling — the
# baseline grandfathers existing tech debt without expanding the
# hard-cap allowlist.
#
# The shape is ``rel_path: (line_count_at_snapshot, label)``.
#
# As old files are split or shrink below their preferred ceiling,
# the lint will fail with a "stale baseline entry" message and
# remind you to remove it — ratcheting the bar tighter over time.
PREFERRED_BASELINE: dict[str, tuple[int, str]] = {
    # ── api-route (preferred 500) ────────────────────────────────
    "backend/routes/admin.py":              (1689, "api-route"),
    "backend/routes/broker.py":             (1061, "api-route"),
    "backend/routes/ai.py":                 (931,  "api-route"),
    "backend/routes/options_trading.py":    (855,  "api-route"),
    "backend/routes/auth.py":               (710,  "api-route"),
    "backend/routes/market_data.py":        (693,  "api-route"),
    "backend/routes/ml_orchestrator.py":    (594,  "api-route"),
    "backend/routes/public_api.py":         (580,  "api-route"),
    "backend/routes/risk_calculator.py":    (575,  "api-route"),
    "backend/routes/analytics.py":          (533,  "api-route"),
    "backend/routes/admin_news.py":         (527,  "api-route"),
    "backend/routes/admin_conviction.py":   (522,  "api-route"),

    # ── core-governance (preferred 600) ──────────────────────────
    "backend/services/trading_bot_service.py":         (1575, "core-governance"),
    "backend/services/market_memory_service.py":       (1357, "core-governance"),
    "backend/services/model_adaptation.py":            (1315, "core-governance"),
    "backend/services/broker_service.py":              (1302, "core-governance"),
    "backend/services/prediction_tracker.py":          (1296, "core-governance"),
    "backend/services/crypto_paper_trader.py":         (1232, "core-governance"),
    "backend/services/terminal_aggregator.py":         (1084, "core-governance"),
    "backend/services/sec_13f_service.py":             (1075, "core-governance"),
    "backend/services/natural_language_trading.py":    (1047, "core-governance"),
    "backend/services/ml_retrain_service.py":          (951,  "core-governance"),
    "backend/services/research_shadow_stats.py":       (914,  "core-governance"),
    "backend/services/ml_paper_trader.py":             (842,  "core-governance"),
    "backend/services/email_service.py":               (835,  "core-governance"),
    "backend/services/sovereign_ai_core.py":           (746,  "core-governance"),
    "backend/services/digest_service.py":              (701,  "core-governance"),
    "backend/services/kraken_equity_shadow_service.py":(699,  "core-governance"),
    "backend/services/research_shadow_engines.py":     (695,  "core-governance"),
    "backend/services/options_universe_service.py":    (684,  "core-governance"),
    "backend/services/agent_activity_service.py":      (665,  "core-governance"),
    "backend/services/risedual_ip_logic.py":           (659,  "core-governance"),
    "backend/services/position_reconciler.py":         (654,  "core-governance"),
    "backend/services/adversarial_core.py":            (640,  "core-governance"),
    "backend/services/ai_core_engine.py":              (629,  "core-governance"),
    "backend/services/top_universe_service.py":        (613,  "core-governance"),
    "backend/services/tier3_readiness.py":             (608,  "core-governance"),  # 2026-05-15: hold for high-conf WR daily-mean smoothing; split scheduled after admin UI port lands.
    "backend/services/price_provider.py":              (608,  "core-governance"),

    # ── default (preferred 800) ───────────────────────────────────
    "backend/server.py":                               (2003, "default"),
    "generate_pdf.py":                                 (883,  "default"),

    # ── script (preferred 400) ────────────────────────────────────
    "backend/scripts/backfill_historical.py":          (743,  "script"),
    "backend/scripts/backfill_sentiment.py":           (683,  "script"),
    "backend/scripts/retrain_alpha_models.py":         (606,  "script"),
    "backend/scripts/backfill_insider_edgar.py":       (524,  "script"),
    "backend/scripts/train_signal_model.py":           (472,  "script"),
    "backend/scripts/backtest.py":                     (471,  "script"),

    # ── test (preferred 800) ──────────────────────────────────────
    "backend/tests/test_research_shadow.py":           (1098, "test"),

    # ── ui-admin-component (preferred 500) ───────────────────────
    "frontend/src/components/admin/MemoryDriftCard.jsx":      (787, "ui-admin-component"),
    "frontend/src/components/admin/ModelAdaptationsPanel.jsx":(589, "ui-admin-component"),
    "frontend/src/components/admin/GuardShadowPanel.jsx":     (579, "ui-admin-component"),
    "frontend/src/components/admin/MLHealthStrip.jsx":        (511, "ui-admin-component"),

    # ── ui-component (preferred 500) ─────────────────────────────
    "frontend/src/components/BrokerConnect.jsx":      (899, "ui-component"),
    "frontend/src/components/UserWorkspace.jsx":      (800, "ui-component"),
    "frontend/src/components/LandingPage.jsx":        (797, "ui-component"),
    "frontend/src/components/AgentActivityFeed.jsx":  (703, "ui-component"),
    "frontend/src/components/LegalPages.jsx":         (570, "ui-component"),

    # ── ui-tile (preferred 400) ──────────────────────────────────
    "frontend/src/components/admin/FastVetoTile.jsx":         (485, "ui-tile"),
    "frontend/src/components/admin/ShellyDiagnosticTile.jsx": (477, "ui-tile"),
    "frontend/src/components/admin/RoadGuardTile.jsx":        (412, "ui-tile"),
}


# ── Helpers ────────────────────────────────────────────────────────


def _repo_root() -> Path:
    # backend/tests/ → repo root is two levels up.
    return Path(__file__).resolve().parent.parent.parent


def _python_files_to_scan() -> list[Path]:
    """Return every (.py / frontend) file the lints should consider."""
    root = _repo_root()
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for fn in filenames:
            ext = Path(fn).suffix
            if ext == ".py" or ext in _FRONTEND_EXTS:
                out.append(Path(dirpath) / fn)
    return out


def _hard_ceiling(path: Path) -> int:
    return PY_MAX_LINES if path.suffix == ".py" else FRONTEND_MAX_LINES


def _line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open("r", encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return 0


# ── Tier 1: hard-ceiling lint (failing) ────────────────────────────


def test_source_files_under_max_lines():
    """Every tracked source file outside the allowlist must stay
    under its per-language hard ceiling.

    To fix a failure: split the file (preferred), or add it to
    ``ALLOWLIST`` with a one-line justification documenting why
    splitting would degrade clarity (or, for refactor debt, naming
    the planned split).
    """
    root = _repo_root()
    failures: list[str] = []

    for path in _python_files_to_scan():
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWLIST:
            continue
        if _is_exempt(rel):
            continue
        ceiling = _hard_ceiling(path)
        n = _line_count(path)
        if n > ceiling:
            failures.append(
                f"{rel}: {n} lines (>{ceiling}). "
                f"Split it or add to ALLOWLIST with a justification."
            )

    assert not failures, (
        "Oversized source files detected:\n  " + "\n  ".join(failures)
    )


# ── Tier 2: preferred-ceiling lint (drift indicator) ──────────────


def test_no_new_preferred_ceiling_breaches():
    """Module-type aware preferred-ceiling lint.

    Fails when:
      * a NEW file (not in PREFERRED_BASELINE) exceeds its
        module-type preferred ceiling
      * a baseline entry no longer exceeds its preferred ceiling
        (ratchet — remove the entry)
      * a baseline entry no longer exists at the recorded path

    Does NOT fail when an existing baseline entry's line count
    grows; that's a soft drift signal we can revisit if it becomes
    a problem.

    Hard cap (PY_MAX_LINES / FRONTEND_MAX_LINES) is enforced
    separately by ``test_source_files_under_max_lines``.
    """
    root = _repo_root()
    new_breaches: list[str] = []
    stale_baseline_now_clean: list[str] = []
    stale_baseline_missing: list[str] = []

    seen_baseline_keys: set[str] = set()

    for path in _python_files_to_scan():
        rel = path.relative_to(root).as_posix()
        if _is_exempt(rel):
            continue
        cls = _classify(rel)
        n = _line_count(path)
        baseline_entry = PREFERRED_BASELINE.get(rel)
        if baseline_entry is not None:
            seen_baseline_keys.add(rel)
            # Ratchet: shrunk below the bar — drop the entry.
            if n <= cls.preferred:
                stale_baseline_now_clean.append(
                    f"{rel}: now {n} lines (≤{cls.preferred} for "
                    f"{cls.label}). Remove from PREFERRED_BASELINE."
                )
            continue
        # File not in baseline → must respect the preferred ceiling.
        if n > cls.preferred:
            new_breaches.append(
                f"{rel}: {n} lines (>{cls.preferred} for "
                f"{cls.label}). Split per the authority-boundary "
                f"pattern, or document why and add to "
                f"PREFERRED_BASELINE with a one-line justification."
            )

    # Baseline entries pointing at files that no longer exist.
    for rel in PREFERRED_BASELINE.keys():
        if rel in seen_baseline_keys:
            continue
        if not (root / rel).is_file():
            stale_baseline_missing.append(
                f"{rel}: file missing — remove from PREFERRED_BASELINE."
            )

    failures = new_breaches + stale_baseline_now_clean + stale_baseline_missing
    assert not failures, (
        "Module-type preferred-ceiling drift:\n  "
        + "\n  ".join(failures)
    )


# ── Self-tests for the allowlist / baseline ────────────────────────


def test_allowlist_entries_all_exist():
    """Stale allowlist rows would silently weaken the lint."""
    root = _repo_root()
    missing = [p for p in ALLOWLIST if not (root / p).is_file()]
    assert not missing, f"Stale ALLOWLIST entries: {missing}"


def test_allowlist_justifications_are_non_empty():
    """Anyone adding a new allowlist entry without a real
    justification fails review here."""
    bad = [p for p, j in ALLOWLIST.items() if not j or len(j.strip()) < 30]
    assert not bad, (
        f"ALLOWLIST entries without a real justification: {bad}. "
        f"Each entry needs a one-line explanation."
    )


def test_preferred_baseline_labels_match_classifier():
    """The label recorded in PREFERRED_BASELINE must match what
    the classifier produces today — otherwise a path drifted
    classes (e.g. moved directories) and the snapshot is stale."""
    mismatches: list[str] = []
    for rel, (_n, recorded_label) in PREFERRED_BASELINE.items():
        actual = _classify(rel).label
        if actual != recorded_label:
            mismatches.append(
                f"{rel}: baseline label '{recorded_label}' "
                f"vs current classifier '{actual}'."
            )
    assert not mismatches, (
        "PREFERRED_BASELINE labels out of sync:\n  "
        + "\n  ".join(mismatches)
    )
