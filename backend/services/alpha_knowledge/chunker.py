"""Pure HTML → text → chunks pipeline. No I/O, no Mongo."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser

CHUNK_TARGET_CHARS = 1200
CHUNK_OVERLAP = 150


class _TextExtractor(HTMLParser):
    """Strip tags, drop nav / script / style / footer / header.

    Python docs use a stable enough structure that a simple
    skip-block heuristic works well — we don't need a full parser.
    Block-level closing tags emit a paragraph break so the
    downstream chunker can split on ``\\n\\n``.
    """

    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript"}
    BLOCK_TAGS = {
        "p", "div", "section", "article", "li", "h1", "h2", "h3",
        "h4", "h5", "h6", "pre", "tr", "blockquote", "dt", "dd",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self.BLOCK_TAGS and self._skip_depth == 0:
            # Mark a paragraph break for the downstream chunker.
            self.parts.append("\n\n")

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        s = data.strip()
        if s:
            self.parts.append(s)


def html_to_text(html: str) -> str:
    """Convert raw HTML to a single normalised text blob."""
    if not html:
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:  # noqa: BLE001
        # Malformed HTML — return whatever we got.
        pass
    # Join with spaces — block-level tags inserted explicit "\n\n"
    # markers; data nodes that should be on the same line stay so.
    text = " ".join(extractor.parts)
    # Collapse 3+ blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of spaces.
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def chunk_text(
    text: str,
    *,
    target_chars: int = CHUNK_TARGET_CHARS,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Split ``text`` into overlapping chunks at paragraph
    boundaries when possible.

    Empty / very short input returns an empty list — no point
    storing 5-character chunks.
    """
    if not text or len(text) < 200:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if not buf:
            buf = p
            continue
        if len(buf) + 2 + len(p) <= target_chars:
            buf = f"{buf}\n\n{p}"
            continue
        chunks.append(buf)
        # Carry forward the tail of the previous chunk for overlap.
        tail = buf[-overlap:] if overlap > 0 else ""
        buf = f"{tail}\n\n{p}" if tail else p
    if buf:
        chunks.append(buf)
    # Drop trivially short chunks.
    return [c for c in chunks if len(c) >= 200]


def chunk_id_for(source_url: str, chunk_index: int) -> str:
    h = hashlib.sha256()
    h.update(source_url.encode("utf-8"))
    h.update(b"::")
    h.update(str(chunk_index).encode("utf-8"))
    return h.hexdigest()[:32]
