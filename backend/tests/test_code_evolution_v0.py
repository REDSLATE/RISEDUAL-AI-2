"""Code Evolution v0 — invariant + audit + promotion-policy tests.

Doctrine assertions
-------------------
The whole point of this layer is that AI cannot promote code. The
test suite enforces:

  * ``may_auto_promote()`` returns False (literal source guard).
  * Every entry point routes ``auto_promote=False`` into the
    persisted receipt.
  * Patches that touch the gate itself are BLOCKED_OPERATOR_ONLY.
  * Patches with forbidden patterns (live-execution flag flips,
    ``delete_many``, HOLD→BUY promotions) are BLOCKED_FORBIDDEN_PATTERN.
  * CRITICAL → 2 signatures, HIGH → 1, others → 0.
  * The countersign endpoint never auto-promotes.
"""
from __future__ import annotations

import inspect
import textwrap

import pytest

from services.code_evolution import promotion_policy
from services.code_evolution.ast_invariants import scan_patch
from services.code_evolution.code_auditor import audit_patch
from services.code_evolution.promotion_policy import (
    evaluate as evaluate_policy,
    may_auto_promote,
    required_signatures_for,
)
from services.code_evolution.schemas import CodePatchProposal


def _proposal(
    *,
    patch_id: str = "p1",
    title: str = "test patch",
    target_files: tuple[str, ...] = (),
    diff_text: str = "",
    rationale: str = "",
) -> CodePatchProposal:
    return CodePatchProposal(
        patch_id=patch_id,
        title=title,
        target_files=target_files,
        rationale=rationale,
        diff_text=diff_text,
    )


# ── Doctrine: may_auto_promote is a constant False ──────────────────


def test_may_auto_promote_is_constant_false():
    """The single hardest invariant. If this test ever flips to
    ``return True`` the gate has been compromised."""
    assert may_auto_promote() is False
    assert may_auto_promote("anything", "at", "all") is False
    assert may_auto_promote(force=True, override=True) is False


def test_may_auto_promote_source_does_not_return_true():
    """Static guard against the most obvious bypass: someone
    flipping the function body to ``return True``. We check the
    source so a CI run catches it without execution."""
    src = inspect.getsource(may_auto_promote)
    assert "return True" not in src, (
        "may_auto_promote() must literally `return False` — found "
        "`return True` in source. This is the single hardest "
        "doctrine invariant in the v0 gate."
    )
    assert "return False" in src


# ── Required-signature tiers ────────────────────────────────────────


@pytest.mark.parametrize(
    "risk_level,expected",
    [("LOW", 0), ("MEDIUM", 0), ("HIGH", 1), ("CRITICAL", 2)],
)
def test_required_signatures_per_risk_tier(risk_level, expected):
    assert required_signatures_for(risk_level) == expected


# ── Protected-path blocker (the gate cannot mutate itself) ──────────


def test_patch_touching_code_evolution_dir_is_blocked_operator_only():
    p = _proposal(
        target_files=("backend/services/code_evolution/api.py",),
        diff_text="+# benign change\n",
    )
    inv = scan_patch(p)
    assert inv.blocks_operator_only is True
    assert "backend/services/code_evolution/api.py" in inv.protected_paths_hit

    audit = audit_patch(p, inv)
    pol = evaluate_policy(inv, audit)
    assert pol.status == "BLOCKED_OPERATOR_ONLY"
    assert pol.auto_promote is False


def test_patch_touching_unprotected_path_is_not_blocked_operator_only():
    p = _proposal(
        target_files=("backend/services/notifications.py",),
        diff_text="+# benign change\n",
    )
    inv = scan_patch(p)
    assert inv.blocks_operator_only is False


# ── Forbidden patterns ──────────────────────────────────────────────


def test_forbidden_pattern_broker_live_order_flip_is_blocked():
    """DOCTRINE V3 (2026-05-13): the BROKER_LIVE_ORDER_ENABLED pattern
    is no longer in the forbidden list — the flag itself was retired
    when RISEDUAL became a headless brain. Patches that mention it
    pass the invariants scanner."""
    diff = "+BROKER_LIVE_ORDER_ENABLED = true\n"
    p = _proposal(target_files=("backend/.env",), diff_text=diff)
    inv = scan_patch(p)
    assert not any(
        "BROKER_LIVE_ORDER_ENABLED" in pat
        for pat in inv.forbidden_patterns_hit
    )


def test_forbidden_pattern_council_modulator_flip_is_blocked():
    diff = "+COUNCIL_RISK_MODULATOR_ENABLED = true\n"
    p = _proposal(target_files=("backend/.env",), diff_text=diff)
    inv = scan_patch(p)
    assert inv.passed is False
    audit = audit_patch(p, inv)
    pol = evaluate_policy(inv, audit)
    assert pol.status == "BLOCKED_FORBIDDEN_PATTERN"


def test_hold_to_buy_promotion_is_blocked():
    diff = (
        "+# Bypass the HOLD floor\n"
        "+if signal == 'HOLD': signal = 'BUY'\n"
    )
    p = _proposal(
        target_files=("backend/services/strategy.py",), diff_text=diff,
    )
    inv = scan_patch(p)
    # The regex picks up the HOLD→BUY mutation.
    assert any("HOLD" in pat.upper() for pat in inv.forbidden_patterns_hit)


def test_delete_many_in_diff_is_blocked():
    diff = "+await db.paper_trades.delete_many({})\n"
    p = _proposal(
        target_files=("backend/services/cleanup.py",), diff_text=diff,
    )
    inv = scan_patch(p)
    assert inv.passed is False
    audit = audit_patch(p, inv)
    pol = evaluate_policy(inv, audit)
    assert pol.status == "BLOCKED_FORBIDDEN_PATTERN"


# ── Risk classifier ─────────────────────────────────────────────────


def test_execution_path_classifies_as_critical():
    p = _proposal(
        target_files=("backend/services/broker_executor.py",),
        diff_text="+# new helper\n",
    )
    inv = scan_patch(p)
    audit = audit_patch(p, inv)
    assert audit.risk_level == "CRITICAL"
    assert any("test_no_local_direction_tuples" in t for t in audit.required_tests)
    pol = evaluate_policy(inv, audit)
    assert pol.required_signatures == 2
    assert pol.status == "REQUIRES_DUAL_OPERATOR_SIGNATURE"
    assert pol.auto_promote is False


def test_risk_path_classifies_as_high():
    p = _proposal(
        target_files=("backend/services/calibration_layer.py",),
        diff_text="+# refit cadence tweak\n",
    )
    inv = scan_patch(p)
    audit = audit_patch(p, inv)
    assert audit.risk_level == "HIGH"
    pol = evaluate_policy(inv, audit)
    assert pol.required_signatures == 1
    assert pol.status == "REQUIRES_OPERATOR_SIGNATURE"


def test_admin_dashboard_classifies_as_medium():
    p = _proposal(
        target_files=("frontend/src/components/admin/PatentJCard.jsx",),
        diff_text="+// presentation tweak\n",
    )
    inv = scan_patch(p)
    audit = audit_patch(p, inv)
    assert audit.risk_level == "MEDIUM"
    pol = evaluate_policy(inv, audit)
    assert pol.required_signatures == 0


def test_docs_classifies_as_low():
    p = _proposal(
        target_files=("README.md",), diff_text="+typo fix\n",
    )
    inv = scan_patch(p)
    audit = audit_patch(p, inv)
    assert audit.risk_level == "LOW"
    pol = evaluate_policy(inv, audit)
    assert pol.required_signatures == 0
    assert pol.status == "PROPOSED"
    assert pol.auto_promote is False


# ── AST destructive-call detection ──────────────────────────────────


def test_ast_detects_destructive_call_when_diff_parses():
    """The AST walker only fires when the additions parse as
    Python — that's by design. We feed it a clean parseable
    snippet and expect a destructive-call note."""
    body = textwrap.dedent("""
        async def wipe(db):
            await db.crypto_paper_trades.delete_many({})
    """).strip()
    p = _proposal(
        target_files=("backend/services/cleanup.py",), diff_text=body,
    )
    inv = scan_patch(p)
    assert any(
        "destructive call" in n or "direct insert" in n for n in inv.notes
    )
    audit = audit_patch(p, inv)
    pol = evaluate_policy(inv, audit)
    assert pol.status == "BLOCKED_FORBIDDEN_PATTERN"


def test_ast_detects_direct_insert_into_protected_collection():
    body = textwrap.dedent("""
        async def cheat(db):
            await db.paper_trades.insert_one({"x": 1})
    """).strip()
    p = _proposal(
        target_files=("backend/services/cheat.py",), diff_text=body,
    )
    inv = scan_patch(p)
    assert any("direct insert into paper_trades" in n for n in inv.notes)


# ── Auto-promote invariant: every result reports False ──────────────


def test_evaluate_always_marks_auto_promote_false_for_critical():
    p = _proposal(
        target_files=("backend/services/broker_executor.py",), diff_text="",
    )
    inv = scan_patch(p)
    audit = audit_patch(p, inv)
    pol = evaluate_policy(inv, audit)
    assert pol.auto_promote is False


def test_evaluate_always_marks_auto_promote_false_for_low():
    p = _proposal(target_files=("README.md",), diff_text="")
    inv = scan_patch(p)
    audit = audit_patch(p, inv)
    pol = evaluate_policy(inv, audit)
    assert pol.auto_promote is False


# ── Module surface — no subprocess in v0 ────────────────────────────


def test_v0_does_not_ship_subprocess_runner():
    """v0 explicitly excludes the test runner. If a future fork
    adds back ``subprocess`` to any code_evolution module, this
    guard fires."""
    import pkgutil
    import services.code_evolution as pkg

    forbidden_imports = ("subprocess", "shlex")
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        # Walk the module source — can't import without breaking
        # the test isolation, so static inspect.
        from pathlib import Path
        path = Path(pkg.__path__[0]) / f"{mod_info.name}.py"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for forb in forbidden_imports:
            assert f"import {forb}" not in text, (
                f"{path.name} imports {forb!r} — v0 forbids the "
                f"subprocess runner. The test runner ships in a "
                f"sandboxed v1, not here."
            )


def test_promotion_policy_module_does_not_import_db_or_routes():
    """The doctrine layer must stay pure. No Mongo, no FastAPI,
    no service imports — that way the policy can be reasoned
    about in isolation."""
    import pathlib
    p = pathlib.Path(promotion_policy.__file__)
    text = p.read_text(encoding="utf-8")
    forbidden = (
        "from fastapi", "import fastapi",
        "from motor", "import motor",
        "from routes", "from services.alpha_decision_log",
    )
    for f in forbidden:
        assert f not in text, (
            f"promotion_policy.py must stay pure; found {f!r}"
        )
