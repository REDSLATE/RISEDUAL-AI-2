"""Regression guard for the 2026-05-06 production scheduler stall.

Root cause: a redundant ``from datetime import datetime, timezone``
inside an inner ``if owner is not None:`` branch of
``server._start_schedulers`` made ``datetime`` a function-LOCAL name
for the entire ~550-line scheduler-start function. When the auto-
seed branch was skipped at runtime (no owner row, or env flag
disabled), the inner import never executed — but the local was
still bound at compile time, so 500 lines later::

    next_run_time=datetime.now(timezone.utc)

raised ``UnboundLocalError`` and the whole scheduler boot died
silently. Production was stuck for 3 days with ``_scheduler is None``
because the only evidence was a single ``logger.warning`` line in
swallowed pod stdout.

This test scans ``server.py``'s ``_start_schedulers`` for any
``from datetime import ...`` (or ``import datetime``) that is NOT
at the module top level. Any such finding is rejected — the module
already imports ``datetime, timezone`` at line 15; nested re-imports
are redundant and historically catastrophic.

Also asserts ``datetime``/``timezone`` survive at module level so
the module-level import isn't accidentally removed.
"""
from __future__ import annotations

import ast
from pathlib import Path

SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"


def _parse_server() -> ast.Module:
    return ast.parse(SERVER_PATH.read_text())


def test_module_level_datetime_imports_present():
    """The module top-level ``from datetime import datetime, timezone``
    is the canonical source. Removing it forces every consumer to
    re-import — exactly the bug we're guarding against."""
    tree = _parse_server()
    module_level_dt_imports = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "datetime"
    ]
    assert module_level_dt_imports, (
        "server.py must keep ``from datetime import datetime, timezone`` "
        "at module level (line 15). Removing it forces inner imports, "
        "which historically caused the 2026-05-06 scheduler stall."
    )
    imported = set()
    for node in module_level_dt_imports:
        for alias in node.names:
            imported.add(alias.name)
    assert "datetime" in imported and "timezone" in imported, (
        f"Module-level datetime import must include both ``datetime`` "
        f"and ``timezone``; got {imported}"
    )


def test_no_inner_datetime_imports_in_start_schedulers():
    """Belt-and-suspenders against the 2026-05-06 incident.

    Walks the AST of ``_start_schedulers`` and rejects any
    ``from datetime import ...`` or ``import datetime``. The
    function is async, ~550 lines, and registers 30+ jobs across
    multiple try/except branches and inner closures. Any conditional
    re-import of ``datetime`` shadows the module-level binding for
    the entire function body and produces ``UnboundLocalError`` on
    the lines that reach ``datetime.now()`` in branches where the
    inner import never ran.
    """
    tree = _parse_server()

    target = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_start_schedulers"
        ):
            target = node
            break
    assert target is not None, "_start_schedulers function not found"

    offenders: list[tuple[int, str]] = []
    for sub in ast.walk(target):
        # ImportFrom: ``from datetime import ...``
        if isinstance(sub, ast.ImportFrom) and sub.module == "datetime":
            offenders.append((sub.lineno, f"from datetime import "
                                          f"{[a.name for a in sub.names]}"))
        # Plain Import: ``import datetime``
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                if alias.name == "datetime":
                    offenders.append((sub.lineno, "import datetime"))

    assert not offenders, (
        "Nested datetime imports inside _start_schedulers are forbidden — "
        "they shadow the module-level binding and caused the 2026-05-06 "
        "production scheduler stall (UnboundLocalError on a 500-lines-"
        f"later datetime.now call). Offenders:\n  " + "\n  ".join(
            f"line {ln}: {src}" for ln, src in offenders
        )
    )
