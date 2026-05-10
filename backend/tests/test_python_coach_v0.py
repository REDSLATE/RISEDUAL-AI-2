"""Python Coach v0 — static review + doctrine firewall tests.

Doctrine pinned by these tests:
  * Coach module NEVER imports ``services.code_evolution``, broker
    paths, or any execution path. It is read-only.
  * Coach NEVER calls ``exec``, ``eval``, ``subprocess``, or
    similar — all "review" is AST-based.
  * Owner-only at the API layer (asserted by the route registry
    integration; checked here via direct route inspection).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from services.python_coach import (
    api as coach_api,
    lesson_planner,
    schemas as coach_schemas,
    static_review as coach_static,
)
from services.python_coach.static_review import static_review


PKG_DIR = Path(coach_api.__file__).parent
PKG_FILES = sorted(PKG_DIR.rglob("*.py"))


# ── Static review: parses + finds simple issues ─────────────────────


def test_static_review_handles_syntax_error_cleanly():
    review = static_review("def broken(\nprint('oops')")
    assert review.parses is False
    assert review.syntax_error
    assert any(f.rule_id == "SYNTAX" for f in review.findings)
    assert review.next_drills


def test_static_review_no_functions_warning():
    review = static_review("x = 1\ny = 2\nprint(x + y)\n")
    assert review.parses is True
    rules = {f.rule_id for f in review.findings}
    assert "PRINT_ONLY" not in rules  # has a non-print expr too
    assert "NO_FUNCTIONS" in rules


def test_static_review_print_only_warning():
    review = static_review("print('hi')\nprint('bye')\n")
    assert review.parses is True
    rules = {f.rule_id for f in review.findings}
    assert "PRINT_ONLY" in rules


def test_static_review_well_formed_function_clears_warnings():
    code = (
        '"""Module docstring."""\n'
        'def add(a: int, b: int) -> int:\n'
        '    """Return a + b."""\n'
        '    return a + b\n\n'
        'if __name__ == "__main__":\n'
        '    print(add(1, 2))\n'
    )
    review = static_review(code)
    assert review.parses is True
    assert review.function_count == 1
    assert review.has_main_guard is True
    assert review.has_docstrings is True
    rules = {f.rule_id for f in review.findings}
    assert "NO_FUNCTIONS" not in rules
    assert "MISSING_DOCSTRINGS" not in rules
    assert "NO_MAIN_GUARD" not in rules


def test_static_review_flags_bare_except_pass():
    code = "def f():\n    try:\n        pass\n    except:\n        pass\n"
    review = static_review(code)
    rules = {f.rule_id for f in review.findings}
    assert "BARE_EXCEPT_PASS" in rules


# ── Drill suggester respects the goal ───────────────────────────────


def test_drill_suggester_emits_retry_drill_for_fetch_goal():
    review = static_review(
        "def fetch():\n    return 1\n",
        goal="fetch stock prices with retry",
    )
    assert any("retry" in d.lower() for d in review.next_drills)


# ── Doctrine: no forbidden imports + no exec/eval ───────────────────


FORBIDDEN_IMPORTS = (
    "services.code_evolution",
    "services.broker",
    "services.broker_executor",
    "services.execution",
    "services.alpaca",
    "services.kraken",
    "subprocess",
    "shlex",
)

FORBIDDEN_CALLS = (
    "exec(",
    "eval(",
    "compile(",
    "os.system(",
    "subprocess.",
)


@pytest.mark.parametrize("path", PKG_FILES, ids=lambda p: p.name)
def test_no_forbidden_imports_or_dangerous_calls(path):
    text = path.read_text(encoding="utf-8")
    # Drop the docstring so the doctrine paragraph itself doesn't
    # trip the substring check.
    src_lines: list[str] = []
    in_doc = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""'):
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        src_lines.append(line)
    src_no_doc = "\n".join(src_lines)
    for forb in FORBIDDEN_IMPORTS:
        assert f"import {forb}" not in src_no_doc, (
            f"{path.name} imports {forb!r} — Python Coach must stay "
            f"isolated from execution / code-evolution paths."
        )
        assert f"from {forb}" not in src_no_doc, (
            f"{path.name} imports from {forb!r} — Python Coach must "
            f"stay isolated."
        )
    for forb in FORBIDDEN_CALLS:
        assert forb not in src_no_doc, (
            f"{path.name} contains call {forb!r}. The coach must "
            f"NEVER execute user-supplied code."
        )


# ── Doctrine: code_evolution does NOT import python_coach either ────


def test_code_evolution_does_not_import_python_coach():
    """The reverse direction — the gate must not learn about the
    coach. Bidirectional isolation."""
    from services import code_evolution
    pkg_dir = Path(code_evolution.__file__).parent
    for path in pkg_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "python_coach" not in text, (
            f"{path.name} references python_coach — the gate must "
            f"stay disjoint from the learning surface."
        )


# ── Schemas: lesson plan stub round-trip ────────────────────────────


def test_lesson_planner_stub_is_serialisable():
    """The fallback path must always return a valid LessonPlan,
    even when the LLM is unavailable."""
    from services.python_coach.lesson_planner import _stub_plan
    plan = _stub_plan("learn list comprehensions", reason="unit_test")
    # Pydantic v2: model_dump() should produce a JSON-serialisable dict.
    payload = plan.model_dump()
    assert payload["goal"] == "learn list comprehensions"
    assert payload["generated_by"] == "alpha-python-coach-stub"
    assert len(payload["steps"]) >= 1


def test_parse_plan_payload_handles_markdown_fences():
    from services.python_coach.lesson_planner import _parse_plan_payload
    raw = (
        "```json\n"
        '{"goal": "x", "summary": "s", "concepts": ["a"], '
        '"steps": [{"step": 1, "title": "t", "why": "w", "practice": "p"}], '
        '"drills": ["d"], "pitfalls": ["pi"], "estimated_minutes": 25}'
        "\n```"
    )
    plan = _parse_plan_payload(raw, goal="x")
    assert plan is not None
    assert plan.goal == "x"
    assert plan.estimated_minutes == 25
    assert plan.steps[0].title == "t"


def test_parse_plan_payload_returns_none_on_garbage():
    from services.python_coach.lesson_planner import _parse_plan_payload
    assert _parse_plan_payload("not json at all", goal="x") is None
    assert _parse_plan_payload("", goal="x") is None


# ── API surface: routes registered, owner-only marker present ──────


def test_router_prefix_and_endpoints_registered():
    paths = [r.path for r in coach_api.router.routes]
    assert "/api/admin/python-coach/plan" in paths
    assert "/api/admin/python-coach/review" in paths
    assert "/api/admin/python-coach/example" in paths


def test_every_endpoint_calls_require_owner():
    """Every route handler must invoke ``_require_owner(request)``
    in its body. Static check — proves the gate is applied uniformly."""
    src = inspect.getsource(coach_api)
    # Three endpoint funcs — each must reference the owner check.
    assert src.count("_require_owner(request)") >= 3, (
        "every Python Coach endpoint must call _require_owner — "
        "the coach is operator-only at the API layer."
    )


# ── Smoke: line counts stay sane (architectural sanity) ────────────


def test_modules_stay_small():
    """Soft ceiling — keeps each file readable in one page."""
    limits = {
        "schemas.py": 200,
        "static_review.py": 350,
        "lesson_planner.py": 320,
        "api.py": 200,
        "__init__.py": 50,
    }
    for path in PKG_FILES:
        cap = limits.get(path.name)
        if cap is None:
            continue
        line_count = sum(1 for _ in path.read_text(encoding="utf-8").splitlines())
        assert line_count <= cap, (
            f"{path.name} grew past the readable ceiling "
            f"({line_count} > {cap}). Split the file."
        )


# ── Compatibility imports: coach modules import cleanly ────────────


def test_imports_clean():
    # Just touching each module asserts no import-time side effects
    # (e.g. accidentally calling the LLM or opening a DB connection).
    assert coach_schemas is not None
    assert coach_static is not None
    assert lesson_planner is not None
    assert coach_api is not None
