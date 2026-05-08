"""Static-source regression check for the cache-hit caller pattern.

The historic bug was callers writing ``cached["data"]`` after
``cached = await ai_cache.get(...)``, even though ``get()`` already
returns the inner data dict. Two independent endpoints in
``routes/ai.py`` (research + hypothesis) had this bug and 500'd on
every cache hit.

This test pins the fix at the source level so the exact wording can't
sneak back in via copy-paste. We allow the pattern only inside
documentation / docstrings (lines containing ``# ``-comment markers
that explicitly call it out as the broken pattern).
"""
from __future__ import annotations

from pathlib import Path

import pytest


_ROUTES_DIR = Path(__file__).parent.parent / "routes"


def _broken_pattern_lines(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, line) tuples where the broken pattern appears
    outside of explicit "this is the broken pattern" documentation.
    """
    offenders: list[tuple[int, str]] = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if 'cached["data"]' not in line:
            continue
        # Allow inside comments / docstrings that explicitly document
        # the historic bug. Keyword anchors keep this tight.
        stripped = line.strip()
        if stripped.startswith("#") and any(
            marker in stripped.lower()
            for marker in ("historic", "bug", "broken", "regression")
        ):
            continue
        offenders.append((i, line.rstrip()))
    return offenders


@pytest.mark.parametrize(
    "route_file",
    [
        "ai.py",
        # Add other route files here if they ever start using
        # AICacheService directly.
    ],
)
def test_no_double_unwrap_in_route_handlers(route_file: str):
    path = _ROUTES_DIR / route_file
    if not path.exists():
        pytest.skip(f"{route_file} not present")

    offenders = _broken_pattern_lines(path)
    assert not offenders, (
        f"`cached[\"data\"]` reappeared in {route_file}. "
        f"AICacheService.get() already returns the inner data dict — "
        f"use ``cached`` directly. Offending lines:\n"
        + "\n".join(f"  L{ln}: {txt}" for ln, txt in offenders)
    )
