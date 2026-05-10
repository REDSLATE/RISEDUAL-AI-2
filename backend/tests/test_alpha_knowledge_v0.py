"""Alpha Knowledge Base v0 — pure unit + doctrine firewall tests.

The ingest pass + retrieval are exercised against a mock Mongo
collection; the live HTTP fetch is NOT exercised here (operator
runs that manually).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.alpha_knowledge import (
    api as ak_api,
    chat_hook,
    chunker,
    retrieval,
    schemas,
    seed_manifest,
)
from services.alpha_knowledge.chat_hook import (
    matches_prefix, maybe_expand_with_python_kb, strip_prefix,
)
from services.alpha_knowledge.chunker import chunk_id_for, chunk_text, html_to_text


PKG_DIR = Path(ak_api.__file__).parent
PKG_FILES = sorted(PKG_DIR.rglob("*.py"))


# ── Chunker (pure) ──────────────────────────────────────────────────


def test_html_to_text_strips_script_and_nav():
    html = (
        "<html><head><script>alert('x')</script></head>"
        "<body><nav>skip</nav><main><p>Keep this paragraph.</p>"
        "<p>And this one.</p></main></body></html>"
    )
    txt = html_to_text(html)
    assert "Keep this paragraph." in txt
    assert "And this one." in txt
    assert "alert" not in txt
    assert "skip" not in txt


def test_chunker_returns_overlapping_chunks_for_long_text():
    para = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 40
    text = "\n\n".join([para, para, para])
    chunks = chunk_text(text, target_chars=800, overlap=80)
    assert len(chunks) >= 2
    for c in chunks:
        # Lower bound enforced by chunker; upper bound lenient because
        # overlap + carry-forward paragraph can push a chunk slightly
        # past target_chars before the next split.
        assert len(c) >= 200


def test_chunker_drops_short_input():
    assert chunk_text("") == []
    assert chunk_text("tiny") == []


def test_chunk_id_is_deterministic():
    a = chunk_id_for("https://x", 0)
    b = chunk_id_for("https://x", 0)
    c = chunk_id_for("https://x", 1)
    assert a == b
    assert a != c
    assert len(a) == 32


# ── Manifest ────────────────────────────────────────────────────────


def test_manifest_covers_all_categories():
    sources = seed_manifest.all_sources()
    cats = {c for (_u, _t, c) in sources}
    assert {"language_reference", "library_reference", "tutorial", "howto"} <= cats
    # Sanity — language_reference must include the data model + grammar pages.
    urls = {u for (u, _t, _c) in sources}
    assert any("datamodel.html" in u for u in urls)


def test_filter_by_category_subsets():
    only_tutorial = seed_manifest.filter_by_category(["tutorial"])
    assert all(c == "tutorial" for (_u, _t, c) in only_tutorial)
    assert len(only_tutorial) > 0
    assert seed_manifest.filter_by_category([]) == seed_manifest.all_sources()
    assert seed_manifest.filter_by_category(None) == seed_manifest.all_sources()


# ── Chat hook ───────────────────────────────────────────────────────


def test_prefix_detection():
    assert matches_prefix("/py how do I use dataclasses")
    assert matches_prefix("  /PY whatever")
    assert not matches_prefix("how do I use /py?")
    assert strip_prefix("/py how do I use dataclasses") == "how do I use dataclasses"


@pytest.mark.asyncio
async def test_chat_hook_passes_through_when_prefix_absent():
    db = MagicMock()
    msg, ctx, meta = await maybe_expand_with_python_kb(
        db, "regular chat message", "ctx",
    )
    assert msg == "regular chat message"
    assert ctx == "ctx"
    assert meta is None


@pytest.mark.asyncio
async def test_chat_hook_expands_when_prefix_present(monkeypatch):
    """Prefix present → retrieve called → context prepended."""
    from services.alpha_knowledge.schemas import RetrievalResponse, RetrievalResult
    fake = RetrievalResponse(
        query="dataclasses",
        results=[
            RetrievalResult(
                chunk_id="c1",
                source_url="https://docs.python.org/3/library/dataclasses.html",
                source_title="dataclasses",
                source_category="library_reference",
                chunk_index=0,
                text="Dataclasses provide a decorator for...",
                score=2.5,
            ),
        ],
        total_corpus_size=42,
    )

    async def fake_retrieve(_db, _q, *, limit=6, category=None):
        return fake

    monkeypatch.setattr(chat_hook, "retrieve", fake_retrieve)
    msg, ctx, meta = await maybe_expand_with_python_kb(
        MagicMock(), "/py how do dataclasses work?", "prior context",
    )
    assert msg == "how do dataclasses work?"
    assert "Python knowledge (Alpha KB):" in (ctx or "")
    assert "Dataclasses provide" in (ctx or "")
    assert "prior context" in (ctx or "")
    assert meta is not None
    assert meta["consulted"] is True
    assert meta["results_count"] == 1


@pytest.mark.asyncio
async def test_chat_hook_swallows_retrieve_exceptions(monkeypatch):
    async def boom(_db, _q, **_):
        raise RuntimeError("Mongo down")

    monkeypatch.setattr(chat_hook, "retrieve", boom)
    msg, ctx, meta = await maybe_expand_with_python_kb(
        MagicMock(), "/py whatever", None,
    )
    # Hook strips prefix even on retrieval error so the user's
    # question still reaches the LLM.
    assert msg == "whatever"
    assert ctx is None
    assert meta is None


# ── Retrieval (mocked Mongo) ────────────────────────────────────────


def _make_db_with_results(rows: list[dict], total: int = 0):
    """Build a mock async DB where ``coll.find().sort().limit()`` is
    awaitable-iterable, and ``count_documents`` returns ``total``."""
    coll = MagicMock()

    class _Cursor:
        def __init__(self, items):
            self._items = list(items)

        def sort(self, *_a, **_k):
            return self

        def limit(self, _n):
            return self

        def __aiter__(self):
            self._iter = iter(self._items)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration  # noqa: B904

    coll.find = MagicMock(return_value=_Cursor(rows))
    coll.count_documents = AsyncMock(return_value=total)

    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=coll)
    return db


@pytest.mark.asyncio
async def test_retrieve_empty_query_returns_empty_results():
    db = _make_db_with_results([], total=5)
    resp = await retrieval.retrieve(db, "")
    assert resp.results == []
    assert resp.total_corpus_size == 5


@pytest.mark.asyncio
async def test_retrieve_returns_results_with_score():
    db = _make_db_with_results([
        {
            "chunk_id": "c1", "source_url": "u", "source_title": "t",
            "source_category": "library_reference", "chunk_index": 0,
            "text": "asyncio basics", "score": 1.4,
        },
    ], total=1)
    resp = await retrieval.retrieve(db, "asyncio", limit=5)
    assert len(resp.results) == 1
    assert resp.results[0].score == 1.4
    assert resp.results[0].source_category == "library_reference"


# ── Doctrine firewalls ──────────────────────────────────────────────


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


# Calls forbidden anywhere in coach source code. Checked via AST so
# string literals (URLs, docstrings) don't false-positive.
FORBIDDEN_CALL_FUNCS: set[tuple[str, ...]] = {
    ("exec",),
    ("eval",),
    ("compile",),  # builtins.compile, not re.compile (tuple is module-qualified)
    ("os", "system"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
}


def _ast_call_chain(node):
    """Resolve an attribute chain like ``a.b.c`` to a tuple. Returns
    None for anything we can't statically resolve (e.g. method calls
    on instance attrs)."""
    import ast as _ast
    parts: list[str] = []
    cur = node
    while isinstance(cur, _ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, _ast.Name):
        parts.append(cur.id)
        return tuple(reversed(parts))
    return None


@pytest.mark.parametrize("path", PKG_FILES, ids=lambda p: p.name)
def test_no_forbidden_imports_or_dangerous_calls(path):
    import ast as _ast
    text = path.read_text(encoding="utf-8")

    # Import scan via AST.
    tree = _ast.parse(text)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                for forb in FORBIDDEN_IMPORTS:
                    assert not alias.name.startswith(forb), (
                        f"{path.name} imports {alias.name!r} — Alpha "
                        f"KB must stay firewalled."
                    )
        elif isinstance(node, _ast.ImportFrom):
            mod = node.module or ""
            for forb in FORBIDDEN_IMPORTS:
                assert not mod.startswith(forb), (
                    f"{path.name} imports from {mod!r} — Alpha KB "
                    f"must stay firewalled."
                )
        elif isinstance(node, _ast.Call):
            chain = _ast_call_chain(node.func)
            if chain is None:
                continue
            # Builtin ``compile()`` is a tuple of length 1 with name
            # ``compile`` — but ``re.compile``, ``self.compile`` etc.
            # are tuples of length >= 2 starting with something else.
            if chain == ("compile",):
                # bare ``compile(...)`` = builtin → forbidden
                pytest.fail(f"{path.name} calls builtin compile()")
            if chain == ("exec",) or chain == ("eval",):
                pytest.fail(f"{path.name} calls {chain[0]}()")
            if chain in FORBIDDEN_CALL_FUNCS:
                pytest.fail(f"{path.name} calls {'.'.join(chain)}()")


def test_code_evolution_does_not_import_alpha_knowledge():
    from services import code_evolution
    pkg_dir = Path(code_evolution.__file__).parent
    for path in pkg_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "alpha_knowledge" not in text, (
            f"{path.name} references alpha_knowledge — the code-"
            f"evolution gate must stay disjoint from the KB."
        )


def test_every_chunk_carries_excluded_flag():
    """The schema default is True. A future migration that flips this
    default would silently let the gate read the KB."""
    chunk = schemas.KnowledgeChunk(
        chunk_id="x" * 32,
        source_url="u",
        source_title="t",
        source_category="library_reference",
        chunk_index=0,
        text="x" * 200,
        char_count=200,
        ingested_at=datetime.now(timezone.utc),
    )
    assert chunk.excluded_from_code_gate_inputs is True


# ── API surface ─────────────────────────────────────────────────────


def test_router_endpoints_registered():
    paths = [r.path for r in ak_api.router.routes]
    assert "/api/admin/alpha-knowledge/status" in paths
    assert "/api/admin/alpha-knowledge/manifest" in paths
    assert "/api/admin/alpha-knowledge/ingest" in paths
    assert "/api/admin/alpha-knowledge/retrieve" in paths


def test_every_endpoint_calls_require_owner():
    import inspect
    src = inspect.getsource(ak_api)
    assert src.count("_require_owner(request)") >= 4


def test_modules_stay_small():
    limits = {
        "schemas.py": 200,
        "chunker.py": 200,
        "ingest.py": 250,
        "retrieval.py": 250,
        "api.py": 200,
        "chat_hook.py": 200,
        "seed_manifest.py": 250,
        "__init__.py": 50,
    }
    for path in PKG_FILES:
        cap = limits.get(path.name)
        if cap is None:
            continue
        n = sum(1 for _ in path.read_text(encoding="utf-8").splitlines())
        assert n <= cap, f"{path.name}: {n} > {cap}"
