"""
Source-file size lint — keep code reviewable.

Why this exists
───────────────
Same discipline as ``test_docs_size.py``, applied to the codebase.
Files past a certain size stop being read end-to-end during review,
so subtle bugs and stale logic accumulate in them silently.

Per-language ceilings (chosen for review-velocity, not aesthetics):

* **Python** (``.py``)         — 800 lines.
* **JSX/JS/TS/TSX** (frontend) — 500 lines. React components past
  this size almost always have multiple responsibilities that should
  be extracted into sub-components.

How to fix a failure
────────────────────
1. **Preferred** — split the file. For routes, split per-domain
   (e.g. ``routes/admin/{users,billing,terminal}.py``). For services,
   pull cohesive sub-systems into their own module. For React,
   extract sub-components.
2. **If splitting genuinely doesn't help** (e.g. a single SQL/HTML
   blob, a one-off generator script, a comprehensive test suite for
   one cohesive subsystem) — add the path to ``ALLOWLIST`` with a
   one-line justification. Reviewers gate that addition.

Existing oversized files are allowlisted with a justification that
either documents known refactor debt (``admin.py``, ``server.py``)
or explains why the file is legitimately large (``generate_pdf``,
mature multi-endpoint service modules). Refactors land one file at
a time — this lint pins the size invariant going forward and stops
new code from inheriting the same pattern.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────

PY_MAX_LINES: int = 800
FRONTEND_MAX_LINES: int = 500

_FRONTEND_EXTS = {".jsx", ".js", ".ts", ".tsx"}

# Directory components to skip entirely. Mirrors ``test_docs_size``
# plus generated/build directories that legitimately contain large
# bundled output.
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

# Allowlist — each entry MUST have a one-line justification.
# Reviewers gate additions. Entries documenting refactor debt should
# track the planned split so the debt doesn't drift.
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


# ── Helpers ────────────────────────────────────────────────────────


def _repo_root() -> Path:
    # backend/tests/ → repo root is two levels up.
    return Path(__file__).resolve().parent.parent.parent


def _source_files() -> list[tuple[Path, int]]:
    """Return (path, ceiling) for every file the lint should scan."""
    root = _repo_root()
    out: list[tuple[Path, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for fn in filenames:
            ext = Path(fn).suffix
            full = Path(dirpath) / fn
            if ext == ".py":
                out.append((full, PY_MAX_LINES))
            elif ext in _FRONTEND_EXTS:
                out.append((full, FRONTEND_MAX_LINES))
    return out


def _line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open("r", encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return 0


# ── The actual test ────────────────────────────────────────────────


def test_source_files_under_max_lines():
    """Every tracked source file outside the allowlist must stay
    under its per-language ceiling.

    To fix a failure: split the file (preferred), or add it to
    ``ALLOWLIST`` with a one-line justification documenting why
    splitting would degrade clarity (or, for refactor debt, naming
    the planned split).
    """
    root = _repo_root()
    failures: list[str] = []

    for path, ceiling in _source_files():
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWLIST:
            continue
        n = _line_count(path)
        if n > ceiling:
            failures.append(
                f"{rel}: {n} lines (>{ceiling}). "
                f"Split it or add to ALLOWLIST with a justification."
            )

    assert not failures, (
        "Oversized source files detected:\n  " + "\n  ".join(failures)
    )


# ── Self-tests for the allowlist itself ────────────────────────────


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
