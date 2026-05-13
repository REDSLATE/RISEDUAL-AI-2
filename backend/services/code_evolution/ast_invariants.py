"""AST + path invariants — the first gate every patch must clear.

Two complementary scans:
  1. Path-based — the patch's ``target_files`` are matched against
     three categorical lists (``PROTECTED_PATHS``,
     ``EXECUTION_PATHS``, ``RISK_DIRECTION_PATHS``).
  2. Content-based — the diff text is grepped for forbidden
     patterns; if the additions parse as Python, an AST walk
     looks for specific destructive Mongo calls
     (``delete_many`` / ``drop_collection`` / direct
     ``paper_trades.insert_*``).

This module performs ZERO writes and never imports the auditor /
promotion-policy / API surface. Pure functions only.
"""
from __future__ import annotations

import ast
import re
from typing import Iterable

from .schemas import ASTInvariantResult, CodePatchProposal


# ── Path categories ─────────────────────────────────────────────────


# A patch that touches ANY of these paths is BLOCKED_OPERATOR_ONLY —
# the gate can't be allowed to mutate itself via the gate.
PROTECTED_PATHS: tuple[str, ...] = (
    "backend/services/code_evolution/",
    "services/code_evolution/",
    "backend/routes/code_evolution",
    "routes/code_evolution",
)


# CRITICAL — touching these requires DUAL operator signatures.
EXECUTION_PATHS: tuple[str, ...] = (
    "execution",
    "broker",
    "public",
    "order",
    "trade_lifecycle",
    "adversarial_core",
    "council_risk_modulator",
    "prediction_tracker",
    "promotion_bridge",
    "promotion_gate",
)


# HIGH — touching these requires ONE operator signature.
RISK_DIRECTION_PATHS: tuple[str, ...] = (
    "risk_multiplier",
    "council_risk",
    "canonical_ai_dir",
    "direction_token",
    "direction_grading",
    "ml_paper_trader",
    "crypto_paper_trader",
    "kill_switch",
    "risk_guard",
    "calibration_layer",
    "alpha_decision_log",
)


# ── Forbidden text patterns (regex on full diff_text) ───────────────


FORBIDDEN_PATTERNS: tuple[str, ...] = (
    r"COUNCIL_RISK_MODULATOR_ENABLED\s*=\s*true",
    r"CRYPTO_ADVERSARIAL_PHASE\s*=\s*full",
    r"risk_multiplier\s*>\s*1\.25",
    r"risk_multiplier\s*<\s*0\.50",
    r"may_auto_promote\s*\([^)]*\)\s*->\s*bool\s*:\s*\n\s*return\s+True",
    r"\bdelete_many\b",
    r"\bdrop_collection\b",
)


# HOLD→BUY/SELL co-occurrence on the SAME LINE — too broad with
# re.DOTALL (matches unrelated tokens across a 200KB diff). Line
# scope catches the realistic patterns: ``if signal == 'HOLD':
# signal = 'BUY'`` or ``HOLD -> BUY``.
HOLD_PROMOTION_PATTERNS: tuple[str, ...] = (
    r"\bHOLD\b.*\b(BUY|SELL|LONG|SHORT)\b",
)


# ── Helpers ─────────────────────────────────────────────────────────


def _added_lines(diff_text: str) -> list[str]:
    """Extract the additions ('+' lines) from a unified diff. If
    ``diff_text`` doesn't look like a diff (no ``+++``/``---``
    headers and no leading ``+`` lines), return the full text
    split — the caller treats the whole blob as additions.
    """
    if "\n+" not in diff_text and not diff_text.lstrip().startswith("+"):
        return diff_text.splitlines()

    out: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            out.append(line[1:])
    return out


def _matches_any(needle_set: Iterable[str], target_files: Iterable[str]) -> tuple[str, ...]:
    """Return the subset of ``target_files`` that matches any
    needle (case-insensitive substring)."""
    hits: list[str] = []
    needles_lower = [n.lower() for n in needle_set]
    for f in target_files:
        f_lower = f.lower()
        if any(n in f_lower for n in needles_lower):
            hits.append(f)
    return tuple(hits)


def _ast_destructive_calls(added_text: str) -> tuple[str, ...]:
    """Walk the added Python text (best-effort parse) and surface
    any direct calls to destructive Mongo methods. Returns the
    string descriptions of each hit (e.g. ``"paper_trades.insert_one"``).
    """
    notes: list[str] = []
    try:
        tree = ast.parse(added_text)
    except SyntaxError:
        return tuple(notes)  # parse-fail → regex layer covers us.

    DESTRUCTIVE = {
        "delete_many", "drop_collection",
        "drop", "delete_one",
    }
    DIRECT_INSERT_COLLS = {
        "paper_trades", "crypto_paper_trades",
        "prediction_tracker", "live_orders",
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr in DESTRUCTIVE:
                notes.append(f"destructive call: .{attr}(...)")
            if attr in {"insert_one", "insert_many"}:
                # Detect ``<coll>.insert_one(...)`` where <coll> is one
                # of the protected collections.
                base = func.value
                if isinstance(base, ast.Attribute) and base.attr in DIRECT_INSERT_COLLS:
                    notes.append(f"direct insert into {base.attr}: .{attr}(...)")
                elif isinstance(base, ast.Name) and base.id in DIRECT_INSERT_COLLS:
                    notes.append(f"direct insert into {base.id}: .{attr}(...)")
    return tuple(notes)


# ── Public scan ─────────────────────────────────────────────────────


def scan_patch(proposal: CodePatchProposal) -> ASTInvariantResult:
    """Run all path + content invariants against ``proposal``.

    Never raises. The result is consumed by the auditor and the
    promotion policy — those layers do NOT know how to interpret
    raw diffs, so the work happens here once.
    """
    # Path categorisation.
    protected_hits = _matches_any(PROTECTED_PATHS, proposal.target_files)
    execution_hits = _matches_any(EXECUTION_PATHS, proposal.target_files)
    risk_hits = _matches_any(RISK_DIRECTION_PATHS, proposal.target_files)

    # Content scan — regex (whole diff).
    forbidden_hits: list[str] = []
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, proposal.diff_text, re.IGNORECASE | re.DOTALL):
            forbidden_hits.append(pat)
    # HOLD-promotion patterns are line-scoped (re.DOTALL would
    # match across the entire diff and false-positive on long
    # unrelated changes).
    for line in proposal.diff_text.splitlines():
        for pat in HOLD_PROMOTION_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                if pat not in forbidden_hits:
                    forbidden_hits.append(pat)
                break

    # Content scan — AST on additions.
    added_block = "\n".join(_added_lines(proposal.diff_text))
    ast_notes = _ast_destructive_calls(added_block)
    notes: list[str] = list(ast_notes)
    if forbidden_hits:
        notes.append(
            f"forbidden_pattern_matches={len(forbidden_hits)}"
        )
    if ast_notes:
        notes.append(f"ast_destructive_call_count={len(ast_notes)}")

    blocks_operator_only = bool(protected_hits)
    passed = (
        not blocks_operator_only
        and not forbidden_hits
        and not ast_notes
    )

    return ASTInvariantResult(
        patch_id=proposal.patch_id,
        passed=passed,
        blocks_operator_only=blocks_operator_only,
        forbidden_patterns_hit=tuple(forbidden_hits),
        protected_paths_hit=protected_hits,
        execution_paths_hit=execution_hits,
        risk_paths_hit=risk_hits,
        notes=tuple(notes),
    )
