"""Chat hook — handle the ``/py <question>`` opt-in prefix.

The chat endpoint calls ``maybe_expand_with_python_kb`` BEFORE
invoking the LLM. If the message starts with ``/py``, we strip the
prefix, retrieve the top-k Python KB chunks, and prepend them to
``memory_context`` as a "Python knowledge:" block. The LLM never
sees the gate doctrine — it only sees the chunks plus the user's
question.

Pure async function. Never raises. Returns the (possibly modified)
``(message, memory_context)`` tuple so the caller can pass them
verbatim to ``ai_service.chat``.
"""
from __future__ import annotations

import logging
import re

from .retrieval import retrieve

logger = logging.getLogger(__name__)


PREFIX_RE = re.compile(r"^\s*/py\b\s*", re.IGNORECASE)
KB_LABEL = "Python knowledge (Alpha KB):"
DEFAULT_K = 5


def matches_prefix(message: str) -> bool:
    return bool(PREFIX_RE.match(message or ""))


def strip_prefix(message: str) -> str:
    return PREFIX_RE.sub("", message or "", count=1).strip()


def _format_chunks(chunks) -> str:
    """Render chunks as a single context block. Token-budget caps
    each chunk to ~600 chars so a 5-chunk pull stays under ~3 KB."""
    lines: list[str] = [KB_LABEL]
    for i, c in enumerate(chunks, start=1):
        snippet = c.text[:600]
        lines.append(
            f"[{i}] {c.source_title} ({c.source_category}) — {c.source_url}\n"
            f"{snippet}"
        )
    return "\n\n".join(lines)


async def maybe_expand_with_python_kb(
    db,
    message: str,
    memory_context: str | None = None,
    *,
    top_k: int = DEFAULT_K,
) -> tuple[str, str | None, dict | None]:
    """Returns ``(message, memory_context, kb_meta)``.

    * If ``message`` does not start with ``/py``, returns the inputs
      unchanged with ``kb_meta=None``.
    * If it does, strips the prefix and prepends retrieved chunks
      to ``memory_context``. ``kb_meta`` carries the structured
      results so the caller can surface them in the API response.
    """
    if not matches_prefix(message):
        return message, memory_context, None

    stripped = strip_prefix(message)
    if not stripped:
        return message, memory_context, None

    try:
        resp = await retrieve(db, stripped, limit=top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_knowledge] chat hook retrieve failed: %s", exc)
        return stripped, memory_context, None

    if not resp.results:
        kb_meta = {
            "consulted": True,
            "results_count": 0,
            "total_corpus_size": resp.total_corpus_size,
        }
        return stripped, memory_context, kb_meta

    block = _format_chunks(resp.results)
    new_ctx = (
        f"{block}\n\n{memory_context}" if memory_context else block
    )
    kb_meta = {
        "consulted": True,
        "results_count": len(resp.results),
        "total_corpus_size": resp.total_corpus_size,
        "sources": [
            {
                "title": r.source_title,
                "url": r.source_url,
                "category": r.source_category,
                "score": r.score,
            }
            for r in resp.results
        ],
    }
    return stripped, new_ctx, kb_meta
