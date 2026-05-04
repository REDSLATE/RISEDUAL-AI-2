"""
Doc-size lint — keep markdown docs reviewable.

Why this exists
───────────────
The Tier 3 Activation Playbook hit 362 lines, which broke the GitHub
UI editor for the operator. Slimming it to 171 lines fixed the
immediate problem; this test prevents the regression class — any new
``.md`` file or unchecked growth of an existing one fails the build
once it crosses ``MAX_LINES``.

How to fix a failure
────────────────────
1. **Preferred** — slim the doc. Pull dense reference material into
   a ``memory/local/<name>_DETAILED.md`` (gitignored) and keep the
   tracked copy as an operator playbook.
2. **If genuinely append-only** (CHANGELOG, deployment-event log) —
   add the path to ``ALLOWLIST`` below with a one-line justification.
   Reviewers gate that addition.

This is **not** a stylistic preference. It's a code-review-velocity
gate — anything past ~800 lines stops being read end-to-end and
becomes a place where stale rules quietly accumulate.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────

# Hard ceiling for any single tracked .md file. Picked to match the
# user's review limit (operator can paste the entire doc into a
# single GitHub UI edit field at this size).
MAX_LINES: int = 800

# Files allowed to exceed ``MAX_LINES``. Each entry MUST have a
# one-line justification — anyone who adds a new entry without one
# fails the allowlist invariant test below.
ALLOWLIST: dict[str, str] = {
    # Append-only changelog by definition; size grows with releases.
    "memory/CHANGELOG.md": (
        "Append-only release history; size is expected to grow."
    ),
    # Historical deployment-event log; new agents read top-to-bottom
    # and append. Splitting it would lose the "scroll the history"
    # affordance the operator relies on.
    "memory/DEPLOYMENT_NOTES.md": (
        "Append-only deployment-event log; chronological scrollability "
        "is the entire point."
    ),
    # Living product-requirements doc. Latest entries land at the top
    # via the agent's finish-tool contract; size grows monotonically.
    # If this passes ~5000 lines the older sections will be split into
    # CHANGELOG.md/ROADMAP.md per the playbook.
    "memory/PRD.md": (
        "Living product-requirements doc — latest entries at top, "
        "older sections are split into CHANGELOG.md/ROADMAP.md once "
        "size warrants it."
    ),
    # Historical record of changes since the original spec PDF;
    # frozen reference, not edited day-to-day.
    "memory/CHANGES_SINCE_PDF.md": (
        "Frozen historical record of changes since the original "
        "spec PDF; not actively edited."
    ),
}

# Directory components to skip entirely. ``memory/local`` is the
# gitignored escape hatch for detailed-doc copies and shouldn't be
# linted (see ``TIER3_ACTIVATION_PLAYBOOK.md``).
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
    "local",  # memory/local/ — gitignored, by design
}


def _repo_root() -> Path:
    # backend/tests/ → repo root is two levels up.
    return Path(__file__).resolve().parent.parent.parent


def _markdown_files() -> list[Path]:
    root = _repo_root()
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for fn in filenames:
            if fn.endswith(".md"):
                out.append(Path(dirpath) / fn)
    return out


def _line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open("r", encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return 0


# ── The actual test ────────────────────────────────────────────────


def test_docs_under_max_lines():
    """Every tracked ``.md`` outside the allowlist must stay under
    ``MAX_LINES``.

    To fix a failure: slim the doc (preferred), or — if it's
    genuinely append-only — add the relative path + justification to
    ``ALLOWLIST`` above.
    """
    root = _repo_root()
    failures: list[str] = []

    for path in _markdown_files():
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWLIST:
            continue
        n = _line_count(path)
        if n > MAX_LINES:
            failures.append(
                f"{rel}: {n} lines (>{MAX_LINES}). "
                f"Slim it or add to ALLOWLIST with a justification."
            )

    assert not failures, "Oversized docs detected:\n  " + "\n  ".join(failures)


# ── Self-tests for the allowlist itself ────────────────────────────


def test_allowlist_entries_all_exist():
    """Stale allowlist rows would silently weaken the lint."""
    root = _repo_root()
    missing = [p for p in ALLOWLIST if not (root / p).is_file()]
    assert not missing, f"Stale ALLOWLIST entries: {missing}"


def test_allowlist_justifications_are_non_empty():
    """Anyone adding a new allowlist entry without a real
    justification fails review here."""
    bad = [p for p, j in ALLOWLIST.items() if not j or len(j.strip()) < 20]
    assert not bad, (
        f"ALLOWLIST entries without a real justification: {bad}. "
        f"Each entry needs a one-line explanation of why the file "
        f"is legitimately append-only."
    )
