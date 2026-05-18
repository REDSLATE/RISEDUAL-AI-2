"""CI tripwire — fails the build if old/duplicate execution-gate
logic ever leaks back into the codebase.

Scoped to the backend Python tree only. The allowlist intentionally
covers this file and the survival layer itself (where these tokens
exist as the *definition* of what is forbidden — not as live gate
logic).
"""
from pathlib import Path

FORBIDDEN = [
    "local_execution_authority = True",
    '"local_execution_authority": true',
    "may_execute = True",
    "can_execute = True",
    "if live_enabled",
    "if paper_only",
    "if observe_only",
    "operator_lock_default",
]

ALLOWLIST = {
    "shared/runtime/platform_survival.py",
    "tests/test_no_duplicate_execution_gates.py",
}


def test_no_duplicate_execution_gate_logic():
    # Scan the backend tree from the repo root (this test runs out
    # of /app/backend so the rglob naturally stays scoped).
    root = Path(".")
    offenders = []

    for path in root.rglob("*.py"):
        rel = str(path).lstrip("./")
        if rel in ALLOWLIST:
            continue
        if any(skip in rel for skip in (".venv", "__pycache__", "node_modules", ".git")):
            continue

        try:
            text = path.read_text(errors="ignore")
        except (OSError, PermissionError):
            continue

        for token in FORBIDDEN:
            if token in text:
                offenders.append((rel, token))

    assert not offenders, f"Duplicate/old gate logic found: {offenders}"
