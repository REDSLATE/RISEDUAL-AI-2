"""Static manifest of Python documentation URLs to ingest.

Curated for "full language" coverage — the language reference (all
chapters), the canonical stdlib pages Alpha is most likely to be
asked about, plus the most useful tutorial chapters.

Operator can extend this list; the ingest pass is idempotent on
URL + chunk_index, so re-running is safe.
"""
from __future__ import annotations

from typing import Literal

DocSource = tuple[str, str, Literal[
    "language_reference",
    "library_reference",
    "tutorial",
    "howto",
]]


# ── Python Language Reference (full) ────────────────────────────────


LANGUAGE_REFERENCE: list[DocSource] = [
    ("https://docs.python.org/3/reference/introduction.html", "Introduction", "language_reference"),
    ("https://docs.python.org/3/reference/lexical_analysis.html", "Lexical analysis", "language_reference"),
    ("https://docs.python.org/3/reference/datamodel.html", "Data model", "language_reference"),
    ("https://docs.python.org/3/reference/executionmodel.html", "Execution model", "language_reference"),
    ("https://docs.python.org/3/reference/import.html", "The import system", "language_reference"),
    ("https://docs.python.org/3/reference/expressions.html", "Expressions", "language_reference"),
    ("https://docs.python.org/3/reference/simple_stmts.html", "Simple statements", "language_reference"),
    ("https://docs.python.org/3/reference/compound_stmts.html", "Compound statements", "language_reference"),
    ("https://docs.python.org/3/reference/toplevel_components.html", "Top-level components", "language_reference"),
    ("https://docs.python.org/3/reference/grammar.html", "Full grammar specification", "language_reference"),
]


# ── Library Reference (essentials Alpha is most likely to need) ────


LIBRARY_REFERENCE: list[DocSource] = [
    ("https://docs.python.org/3/library/functions.html", "Built-in functions", "library_reference"),
    ("https://docs.python.org/3/library/stdtypes.html", "Built-in types", "library_reference"),
    ("https://docs.python.org/3/library/exceptions.html", "Built-in exceptions", "library_reference"),
    ("https://docs.python.org/3/library/typing.html", "typing — type hints", "library_reference"),
    ("https://docs.python.org/3/library/dataclasses.html", "dataclasses", "library_reference"),
    ("https://docs.python.org/3/library/enum.html", "enum", "library_reference"),
    ("https://docs.python.org/3/library/abc.html", "abc — abstract base classes", "library_reference"),
    ("https://docs.python.org/3/library/asyncio-task.html", "asyncio — coroutines & tasks", "library_reference"),
    ("https://docs.python.org/3/library/asyncio-stream.html", "asyncio — streams", "library_reference"),
    ("https://docs.python.org/3/library/asyncio-sync.html", "asyncio — sync primitives", "library_reference"),
    ("https://docs.python.org/3/library/collections.html", "collections", "library_reference"),
    ("https://docs.python.org/3/library/collections.abc.html", "collections.abc", "library_reference"),
    ("https://docs.python.org/3/library/functools.html", "functools", "library_reference"),
    ("https://docs.python.org/3/library/itertools.html", "itertools", "library_reference"),
    ("https://docs.python.org/3/library/contextlib.html", "contextlib", "library_reference"),
    ("https://docs.python.org/3/library/copy.html", "copy", "library_reference"),
    ("https://docs.python.org/3/library/json.html", "json", "library_reference"),
    ("https://docs.python.org/3/library/re.html", "re — regular expressions", "library_reference"),
    ("https://docs.python.org/3/library/string.html", "string", "library_reference"),
    ("https://docs.python.org/3/library/textwrap.html", "textwrap", "library_reference"),
    ("https://docs.python.org/3/library/datetime.html", "datetime", "library_reference"),
    ("https://docs.python.org/3/library/time.html", "time", "library_reference"),
    ("https://docs.python.org/3/library/calendar.html", "calendar", "library_reference"),
    ("https://docs.python.org/3/library/decimal.html", "decimal", "library_reference"),
    ("https://docs.python.org/3/library/fractions.html", "fractions", "library_reference"),
    ("https://docs.python.org/3/library/math.html", "math", "library_reference"),
    ("https://docs.python.org/3/library/statistics.html", "statistics", "library_reference"),
    ("https://docs.python.org/3/library/random.html", "random", "library_reference"),
    ("https://docs.python.org/3/library/os.html", "os — OS interfaces", "library_reference"),
    ("https://docs.python.org/3/library/os.path.html", "os.path", "library_reference"),
    ("https://docs.python.org/3/library/pathlib.html", "pathlib", "library_reference"),
    ("https://docs.python.org/3/library/io.html", "io", "library_reference"),
    ("https://docs.python.org/3/library/sys.html", "sys", "library_reference"),
    ("https://docs.python.org/3/library/argparse.html", "argparse", "library_reference"),
    ("https://docs.python.org/3/library/logging.html", "logging", "library_reference"),
    ("https://docs.python.org/3/library/threading.html", "threading", "library_reference"),
    ("https://docs.python.org/3/library/multiprocessing.html", "multiprocessing", "library_reference"),
    ("https://docs.python.org/3/library/concurrent.futures.html", "concurrent.futures", "library_reference"),
    ("https://docs.python.org/3/library/queue.html", "queue", "library_reference"),
    ("https://docs.python.org/3/library/subprocess.html", "subprocess", "library_reference"),
    ("https://docs.python.org/3/library/socket.html", "socket", "library_reference"),
    ("https://docs.python.org/3/library/urllib.parse.html", "urllib.parse", "library_reference"),
    ("https://docs.python.org/3/library/urllib.request.html", "urllib.request", "library_reference"),
    ("https://docs.python.org/3/library/http.client.html", "http.client", "library_reference"),
    ("https://docs.python.org/3/library/email.html", "email", "library_reference"),
    ("https://docs.python.org/3/library/csv.html", "csv", "library_reference"),
    ("https://docs.python.org/3/library/sqlite3.html", "sqlite3", "library_reference"),
    ("https://docs.python.org/3/library/hashlib.html", "hashlib", "library_reference"),
    ("https://docs.python.org/3/library/hmac.html", "hmac", "library_reference"),
    ("https://docs.python.org/3/library/secrets.html", "secrets", "library_reference"),
    ("https://docs.python.org/3/library/uuid.html", "uuid", "library_reference"),
    ("https://docs.python.org/3/library/weakref.html", "weakref", "library_reference"),
    ("https://docs.python.org/3/library/gc.html", "gc — garbage collector", "library_reference"),
    ("https://docs.python.org/3/library/inspect.html", "inspect", "library_reference"),
    ("https://docs.python.org/3/library/ast.html", "ast", "library_reference"),
    ("https://docs.python.org/3/library/unittest.html", "unittest", "library_reference"),
    ("https://docs.python.org/3/library/unittest.mock.html", "unittest.mock", "library_reference"),
]


# ── Tutorial (key chapters) ─────────────────────────────────────────


TUTORIAL: list[DocSource] = [
    ("https://docs.python.org/3/tutorial/introduction.html", "Tutorial: An informal introduction", "tutorial"),
    ("https://docs.python.org/3/tutorial/controlflow.html", "Tutorial: Control flow", "tutorial"),
    ("https://docs.python.org/3/tutorial/datastructures.html", "Tutorial: Data structures", "tutorial"),
    ("https://docs.python.org/3/tutorial/modules.html", "Tutorial: Modules", "tutorial"),
    ("https://docs.python.org/3/tutorial/errors.html", "Tutorial: Errors and exceptions", "tutorial"),
    ("https://docs.python.org/3/tutorial/classes.html", "Tutorial: Classes", "tutorial"),
    ("https://docs.python.org/3/tutorial/stdlib.html", "Tutorial: Brief tour of the stdlib", "tutorial"),
    ("https://docs.python.org/3/tutorial/stdlib2.html", "Tutorial: Stdlib pt 2", "tutorial"),
]


# ── HOWTOs ──────────────────────────────────────────────────────────


HOWTO: list[DocSource] = [
    ("https://docs.python.org/3/howto/argparse.html", "HOWTO: argparse", "howto"),
    ("https://docs.python.org/3/howto/descriptor.html", "HOWTO: Descriptors", "howto"),
    ("https://docs.python.org/3/howto/functional.html", "HOWTO: Functional programming", "howto"),
    ("https://docs.python.org/3/howto/logging.html", "HOWTO: Logging basic", "howto"),
    ("https://docs.python.org/3/howto/regex.html", "HOWTO: Regex", "howto"),
    ("https://docs.python.org/3/howto/sorting.html", "HOWTO: Sorting", "howto"),
    ("https://docs.python.org/3/howto/unicode.html", "HOWTO: Unicode", "howto"),
]


def all_sources() -> list[DocSource]:
    """Combined manifest. Order is preserved — operator can ingest
    incrementally by passing ``limit_urls``."""
    return [
        *LANGUAGE_REFERENCE,
        *LIBRARY_REFERENCE,
        *TUTORIAL,
        *HOWTO,
    ]


def filter_by_category(
    cats: list[str] | None,
) -> list[DocSource]:
    sources = all_sources()
    if not cats:
        return sources
    keep = set(cats)
    return [s for s in sources if s[2] in keep]
