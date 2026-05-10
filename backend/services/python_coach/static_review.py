"""Static AST-based Python code review.

PURE function module — no I/O, no LLM, no execution. Given a
string of Python source, returns a structured ``CodeReview`` with
findings the operator can act on. This is deliberately limited
to the kind of feedback the demo artifact described: missing
colons (SyntaxError), no functions, print-only logic, no
docstrings, etc.

The LLM-driven "deep feedback" is a separate path in
``lesson_planner.py``. Static review is always cheap and always
run first — even if the LLM is offline, the operator gets
useful feedback.
"""
from __future__ import annotations

import ast
import re
from typing import Optional

from .schemas import CodeFinding, CodeReview


# ── Pure helpers ────────────────────────────────────────────────────


def _safe_parse(code: str) -> tuple[Optional[ast.AST], Optional[str]]:
    try:
        return ast.parse(code), None
    except SyntaxError as e:  # noqa: PERF203
        return None, f"line {e.lineno}: {e.msg}"


def _line_count(code: str) -> int:
    return len([ln for ln in code.splitlines() if ln.strip()])


def _has_main_guard(tree: ast.AST) -> bool:
    """Detect ``if __name__ == "__main__":`` at module level."""
    if not isinstance(tree, ast.Module):
        return False
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Compare):
            left = test.left
            if (
                isinstance(left, ast.Name) and left.id == "__name__"
                and len(test.comparators) == 1
            ):
                comp = test.comparators[0]
                if isinstance(comp, ast.Constant) and comp.value == "__main__":
                    return True
    return False


def _walk_callables(tree: ast.AST) -> tuple[list[ast.FunctionDef], list[ast.AsyncFunctionDef], list[ast.ClassDef]]:
    funcs: list[ast.FunctionDef] = []
    afuncs: list[ast.AsyncFunctionDef] = []
    classes: list[ast.ClassDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            funcs.append(node)
        elif isinstance(node, ast.AsyncFunctionDef):
            afuncs.append(node)
        elif isinstance(node, ast.ClassDef):
            classes.append(node)
    return funcs, afuncs, classes


def _has_any_docstring(tree: ast.AST) -> bool:
    """True if any function / class / module has a docstring."""
    if isinstance(tree, ast.Module) and ast.get_docstring(tree):
        return True
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node):
                return True
    return False


def _is_print_only(tree: ast.AST) -> bool:
    """Heuristic: top-level body is *only* ``print(...)`` calls and
    bare expressions, with no functions / classes / imports / loops."""
    if not isinstance(tree, ast.Module):
        return False
    if not tree.body:
        return False
    for node in tree.body:
        if isinstance(node, ast.Expr):
            v = node.value
            if isinstance(v, ast.Call):
                func = v.func
                if isinstance(func, ast.Name) and func.id == "print":
                    continue
            return False
        return False
    return True


# ── Public review entrypoint ────────────────────────────────────────


def static_review(code: str, *, goal: str = "") -> CodeReview:
    """Statically review ``code``. Never raises, never executes.

    The review structure mirrors the artifact's "Code review" panel:
      * parses + syntax_error
      * function / class counts
      * findings list (rule_id + severity + message)
      * summary blurb
      * next-drill suggestions
    """
    findings: list[CodeFinding] = []
    line_count = _line_count(code)

    tree, err = _safe_parse(code)

    if tree is None:
        # Syntax error — surface the message and bail with a single
        # finding the operator can act on.
        findings.append(CodeFinding(
            severity="error",
            rule_id="SYNTAX",
            message=f"Python could not parse this code ({err}).",
        ))
        next_drills = [
            "Re-read the offending line — common culprit is a missing colon "
            "after `def`, `if`, `for`, or `while`.",
            "Run the snippet through `python -m py_compile <file>` to confirm "
            "it parses before adding more code.",
        ]
        return CodeReview(
            parses=False,
            syntax_error=err,
            line_count=line_count,
            function_count=0,
            class_count=0,
            has_main_guard=False,
            has_docstrings=False,
            findings=findings,
            summary=(
                "Code does not parse. Fix the syntax error before "
                "continuing — no further checks were run."
            ),
            next_drills=next_drills,
        )

    funcs, afuncs, classes = _walk_callables(tree)
    function_count = len(funcs) + len(afuncs)
    class_count = len(classes)
    has_main = _has_main_guard(tree)
    has_doc = _has_any_docstring(tree)

    if function_count == 0 and class_count == 0:
        findings.append(CodeFinding(
            severity="warn",
            rule_id="NO_FUNCTIONS",
            message=(
                "No functions or classes defined. Wrap reusable logic "
                "in `def` to make it testable and importable."
            ),
        ))

    if _is_print_only(tree):
        findings.append(CodeFinding(
            severity="warn",
            rule_id="PRINT_ONLY",
            message=(
                "Module body is only `print(...)` calls. For RISEDUAL, "
                "prefer functions that return values — printing makes "
                "the logic untestable."
            ),
        ))

    if function_count > 0 and not has_doc:
        findings.append(CodeFinding(
            severity="info",
            rule_id="MISSING_DOCSTRINGS",
            message=(
                "No docstrings on any function/class/module. Even one "
                "line per function pays back fast when you re-read code "
                "in a week."
            ),
        ))

    if function_count > 0 and not has_main:
        findings.append(CodeFinding(
            severity="info",
            rule_id="NO_MAIN_GUARD",
            message=(
                "No `if __name__ == \"__main__\":` block. Adding one "
                "lets you run the file directly without auto-executing "
                "everything when imported."
            ),
        ))

    # Tab usage — mixing tabs and spaces is a classic trap.
    if "\t" in code and re.search(r"^ {1,3}\S", code, re.MULTILINE):
        findings.append(CodeFinding(
            severity="warn",
            rule_id="MIXED_INDENT",
            message=(
                "Code mixes tabs and spaces in indentation. PEP 8 says "
                "spaces only — tabs hide silently in diffs."
            ),
        ))

    # ``except: pass`` — silent failure smell.
    if re.search(r"except\s*:\s*\n\s*pass\b", code):
        findings.append(CodeFinding(
            severity="warn",
            rule_id="BARE_EXCEPT_PASS",
            message=(
                "`except: pass` swallows every error including "
                "KeyboardInterrupt. Catch the specific exception or at "
                "least log it."
            ),
        ))

    summary_parts: list[str] = []
    if function_count > 0:
        summary_parts.append(f"{function_count} function(s)")
    if class_count > 0:
        summary_parts.append(f"{class_count} class(es)")
    if not summary_parts:
        summary_parts.append("no callables")
    summary = (
        f"{line_count} line(s), {', '.join(summary_parts)}. "
        f"{len(findings)} finding(s)."
    )

    next_drills = _suggest_drills(
        function_count=function_count,
        class_count=class_count,
        has_doc=has_doc,
        has_main=has_main,
        goal=goal,
    )

    return CodeReview(
        parses=True,
        syntax_error=None,
        line_count=line_count,
        function_count=function_count,
        class_count=class_count,
        has_main_guard=has_main,
        has_docstrings=has_doc,
        findings=findings,
        summary=summary,
        next_drills=next_drills,
    )


# ── Drill suggester (pure heuristic) ────────────────────────────────


def _suggest_drills(
    *,
    function_count: int,
    class_count: int,
    has_doc: bool,
    has_main: bool,
    goal: str,
) -> list[str]:
    out: list[str] = []
    if function_count == 0:
        out.append(
            "Refactor the script: pull each top-level operation into a "
            "named function, even if it's just one line."
        )
    if function_count > 0 and not has_doc:
        out.append(
            "Add a one-line docstring to every function. State what it "
            "does — not how."
        )
    if function_count > 0 and not has_main:
        out.append(
            "Wrap the script entry-point in `if __name__ == \"__main__\":` "
            "so the file is safe to import."
        )
    if class_count > 0 and not has_doc:
        out.append("Add docstrings to your class definitions.")
    g = (goal or "").lower()
    if any(k in g for k in ("fetch", "http", "api", "stock", "price")):
        out.append(
            "Practice retry logic: wrap your fetch in a loop with "
            "exponential back-off and a max-attempts cap."
        )
    if any(k in g for k in ("dict", "dictionary", "json")):
        out.append(
            "Drill: write a function that returns a dict with required + "
            "optional keys, then write a test that asserts the shape."
        )
    if not out:
        out.append(
            "Write three small tests for your code (input → expected "
            "output) and run them with `pytest`."
        )
    return out
