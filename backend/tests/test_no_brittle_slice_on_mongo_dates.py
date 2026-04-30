"""CI guard: prevent regressions of the Mongo→Chroma `[:10]` slice bug.

Greps the backend source for the historic ``mongo_doc.get("…", "")[:10]``
pattern that re-coerced datetimes into broken shapes across four
different sync sites. The structural fix was to route every such
callsite through ``services.datetime_utils.to_iso_date``; this test
fails CI if any new code reintroduces the pattern.

Pattern matched:

  ``<expr>.get("…")[:10]``                # bare get + slice
  ``<expr>.get("…", "")[:10]``           # get-with-default + slice
  ``(<expr> or "")[:10]``                # ``or "" `` then slice

The matcher is intentionally lossy on the upper end — false
positives go in ``ALLOWLIST`` rather than relaxing the regex, so we
keep the strict default and only excuse known-safe sites (e.g.
slicing a known-string ISO field that's never a datetime).

Why a CI guard rather than a `to_iso_date` mandate: the original
bug's signature was that every site INVENTED its own coercion. A
guideline doesn't catch that. A regex grep on commit does.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent  # /app/backend
SCAN_DIRS = ["services", "routes", "ai_core"]
SKIP_FILES = {
    "datetime_utils.py",  # the helper itself
    "test_no_brittle_slice_on_mongo_dates.py",  # this test (regex literals)
}

# (a) ``.get("…")[:10]`` and ``.get("…", default)[:10]``
PATTERN_GET_SLICE = re.compile(
    r"\.get\(\s*[\"'][^\"']+[\"']\s*(?:,\s*[^)]+)?\)\s*\[\s*:\s*10\s*\]"
)
# (b) ``(<x> or "")[:10]`` — historic guard against None
PATTERN_OR_EMPTY_SLICE = re.compile(
    r"\(\s*[A-Za-z_][A-Za-z_0-9\.\[\]'\"]*\s+or\s+[\"'][\"']\s*\)\s*\[\s*:\s*10\s*\]"
)

# Files with [:10] on PROVABLY non-datetime strings — known safe.
# Keys are paths relative to /app/backend; values are sets of line
# numbers. Add a brief reason in a comment when growing this list.
ALLOWLIST: dict[str, set[int]] = {
    # ``v.get("date", "")[:10]`` where ``date`` is the user-typed
    # YYYY-MM-DD string from the watchlist intelligence form, never
    # a Mongo BSON Date.
    "ai_core/watchlist_intelligence_tool.py": {296},
    # Marketstack API responses always return ISO strings — never
    # datetime objects — and we control the parse path. Safe.
    "services/market_data_pool.py": {296},
    "services/search_war_room/adapters/marketstack.py": {60},
    # ``data.get('bids', [])[:10]`` etc. — slicing a LIST (Python
    # default ``[]``), not a date string. Caught by the regex
    # because of the trailing ``[:10]`` but completely benign.
    "services/crypto_scraping_service.py": {38, 39},
}


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, raw_line) hits."""
    text = path.read_text()
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(text.split("\n"), start=1):
        # Skip comments — they're documenting the bug, not committing it.
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if PATTERN_GET_SLICE.search(line) or PATTERN_OR_EMPTY_SLICE.search(line):
            hits.append((i, line.strip()))
    return hits


def test_no_brittle_slice_on_mongo_dates():
    """Fail if any new code does ``mongo_doc.get("…", "")[:10]``.

    The fix when this test fails:

        from services.datetime_utils import to_iso_date
        # ...
        date_str = to_iso_date(mongo_doc.get("timestamp"))
        if date_str:
            ...

    Why this matters:
        * ``[:10]`` on a Mongo ``datetime`` raises TypeError
          (silently swallowed) → Chroma row never written.
        * ``[:10]`` on an empty string returns "" → all dateless
          rows collide in ``_make_id``.
        * ``[:10]`` on a non-ISO string returns garbage prefix →
          wrong date stored in Chroma metadata.

        ``to_iso_date`` handles all three correctly.
    """
    failures: list[str] = []
    for sub in SCAN_DIRS:
        for py in (ROOT / sub).rglob("*.py"):
            if py.name in SKIP_FILES:
                continue
            rel = str(py.relative_to(ROOT))
            allowed = ALLOWLIST.get(rel, set())
            for lineno, raw in _scan_file(py):
                if lineno in allowed:
                    continue
                failures.append(f"{rel}:{lineno} | {raw}")

    if failures:
        msg = (
            "Brittle Mongo-date slice detected. Route through "
            "``services.datetime_utils.to_iso_date(value)`` instead "
            "of ``mongo_doc.get('field', '')[:10]``:\n  - "
            + "\n  - ".join(failures)
        )
        pytest.fail(msg)
