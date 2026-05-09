"""CI guard — observability call-site safety.

Born of incident 2026-05-09: ``ai_service.py`` passed ``model=`` as a
kwarg to :func:`services.langfuse_tracer.span_update`. The helper
didn't accept it, raised ``TypeError`` AFTER the LLM call had already
succeeded, and the surrounding ``except Exception`` path converted the
success into a "technical difficulties" message returned to the user.

This module enforces four invariants so that class of bug stays dead:

  1. No call site uses ``span_update(..., model=...)``.
  2. No call site passes any kwarg outside the helper's accepted set.
  3. :func:`span_update` continues to accept unknown kwargs safely via
     ``**_extra``.
  4. Exceptions inside :func:`span_update` (or inside the underlying
     SDK) MUST never propagate out of the helper — observability MUST
     NOT convert a successful business call into a failure response.

The guard is a static AST pass over ``/app/backend`` plus a few
behavioural checks. It runs in milliseconds — safe for the fast lint
loop.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from services.langfuse_tracer import span_update


REPO_ROOT = Path(__file__).resolve().parent.parent  # /app/backend
EXCLUDE_DIRS = {
    "tests", ".venv", "venv", "node_modules", "__pycache__",
    "_backlog", "data", "models",
}


def _python_files() -> list[Path]:
    """Walk ``/app/backend`` for .py files outside test dirs.

    The CI guard intentionally does NOT scan the tests tree itself —
    test code is allowed to invoke the helper with deliberately-bad
    kwargs to assert the safety contract.
    """
    out: list[Path] = []
    for p in REPO_ROOT.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def _accepted_kwargs() -> set[str]:
    """Resolve the accepted-kwarg set straight from the helper's
    signature. Catches any rename / removal automatically."""
    sig = inspect.signature(span_update)
    accepted: set[str] = set()
    for name, param in sig.parameters.items():
        if name == "span":
            continue
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            # ``**_extra`` is the absorption mechanism — its presence
            # is asserted separately. Don't include in the static set.
            continue
        accepted.add(name)
    return accepted


def _find_span_update_calls(tree: ast.AST) -> list[ast.Call]:
    """Return every ``ast.Call`` whose callable is ``span_update``."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "span_update":
            calls.append(node)
        elif isinstance(func, ast.Attribute) and func.attr == "span_update":
            calls.append(node)
    return calls


# ── Invariant 1 + 2: no call site passes disallowed kwargs ──────────


def test_no_call_site_passes_model_kwarg_to_span_update():
    """Invariant 1 (the original incident)."""
    offenders: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for call in _find_span_update_calls(tree):
            for kw in call.keywords:
                if kw.arg == "model":
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(f"{rel}:{call.lineno}")

    assert not offenders, (
        "span_update(..., model=...) is forbidden — fold it into "
        "metadata={'model': ...}. Offenders:\n  "
        + "\n  ".join(offenders)
    )


def test_no_call_site_passes_unsupported_kwargs_to_span_update():
    """Invariant 2 — every kwarg at every call site must be in the
    helper's named-parameter set. Unknown kwargs MUST be expressed
    via metadata=... so they show up where ops actually look."""
    accepted = _accepted_kwargs()
    offenders: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for call in _find_span_update_calls(tree):
            for kw in call.keywords:
                # ``**kwargs`` splat (kw.arg is None) — can't statically
                # verify but is rare and used only in tests.
                if kw.arg is None:
                    continue
                if kw.arg not in accepted:
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(
                        f"{rel}:{call.lineno} kwarg={kw.arg!r}"
                    )

    assert not offenders, (
        "span_update(...) call sites passed kwargs outside the "
        "accepted set " + repr(sorted(accepted)) + ".\n"
        "Fold the data into metadata={...} instead. Offenders:\n  "
        + "\n  ".join(offenders)
    )


# ── Invariant 3: helper signature still absorbs unknown kwargs ──────


def test_span_update_signature_has_var_keyword_absorber():
    """Defence-in-depth — even with the static guard, the helper
    MUST keep ``**_extra`` so a rogue kwarg slipping through code
    review can't crash the surrounding business logic."""
    sig = inspect.signature(span_update)
    has_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )
    assert has_var_kw, (
        "span_update() must keep **_extra so unknown kwargs are "
        "absorbed. This is the runtime safety net behind the "
        "static guard."
    )


# ── Invariant 4: observability exceptions never sink business logic ─


def _success_path_with_observability(span_factory) -> str:
    """Mimic the ai_service success-path pattern. The function MUST
    return the LLM's "actual response" even when ``span_update``
    misbehaves."""
    span = span_factory()
    try:
        result = "actual response"  # business logic succeeded.
        # This call site historically converted a TypeError into a
        # "technical difficulties" message via the outer except.
        span_update(span, output={"result": result}, model="gpt-5.2")
        return result
    except Exception:  # noqa: BLE001
        return "technical difficulties"


def test_span_update_unknown_kwargs_do_not_sink_success_response():
    """Invariant 4 — the original incident's smoke test. With the
    helper hardened, a stray kwarg must NOT flip the success path
    into the failure branch."""
    from unittest.mock import MagicMock

    out = _success_path_with_observability(span_factory=lambda: MagicMock())
    assert out == "actual response", (
        "Observability call site sank the successful response — "
        "regression of the 2026-05-09 chat outage."
    )


def test_span_update_with_raising_sdk_does_not_sink_success_response():
    """Invariant 4 — even when the underlying SDK ``.update()`` call
    raises mid-write, the helper's internal try/except must absorb
    it."""
    from unittest.mock import MagicMock

    def _bad_span_factory():
        s = MagicMock()
        s.update.side_effect = RuntimeError("span already closed")
        return s

    out = _success_path_with_observability(span_factory=_bad_span_factory)
    assert out == "actual response"


def test_span_update_with_none_does_not_sink_success_response():
    """Invariant 4 — disabled tracing path (span is None) must also
    leave the success branch intact."""
    out = _success_path_with_observability(span_factory=lambda: None)
    assert out == "actual response"
