#!/usr/bin/env python3
"""Structured-log migration helper (v2).

Handles common `logger.warning(f"...{e}")` / `logger.error(...)` shapes:

  • `logger.X(f"Prefix: {e}")`
  • `logger.X(f"Prefix: {str(e)}")`  / `{repr(e)}`
  • `logger.X(f"Prefix for {var}: {e}")` — interpolated context
  • `logger.X(f"Prefix: {result['error']}")` — result-dict lookup

Migrates each to `log_{warning|error}(logger, {...})` with explicit
`error`, `type`, `context`, `note`, and any captured interpolation
vars as extra keys. Leaves anything non-matching untouched.

Usage:  python3 /app/scripts/migrate_logs.py <file.py> <context>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Match one logger.(warning|error) call with an f-string argument.
# We parse the f-string body ourselves since regex can't balance braces.
CALL_RE = re.compile(
    r'(?P<indent>[ \t]+)logger\.(?P<level>warning|error)\(f"(?P<body>[^"]*)"\)'
)


def parse_body(body: str) -> tuple[str, list[tuple[str, str]], str | None]:
    """Return (note, [(key, expr)] extras, exc_var).

    `exc_var` is the detected exception variable name (e, exc, err, ex)
    if the body ends in ``: {e}`` or ``: {str(e)}``; otherwise None.
    `extras` are interpolation vars mid-string (e.g. `{symbol}` → key
    `symbol`, expr `symbol`).
    `note` is the plain-text remnant with brace-placeholders removed,
    for `note=` field.
    """
    # Find all {...} groups, preserving order.
    spans: list[tuple[int, int, str]] = []
    i, n = 0, len(body)
    while i < n:
        if body[i] == "{" and i + 1 < n and body[i + 1] != "{":
            # Find the matching close-brace (no nested f-strings in our corpus).
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                j += 1
            expr = body[i + 1 : j - 1]
            spans.append((i, j, expr))
            i = j
        else:
            i += 1

    if not spans:
        return body.strip(), [], None

    # The LAST brace on the string is almost always the exception: `: {e}`.
    last_start, last_end, last_expr = spans[-1]
    preceding = body[:last_start].rstrip(" :").rstrip()
    trailing = body[last_end:].strip()
    if trailing:
        # Something after the last brace → don't risk misparsing.
        return None, [], None

    # Extract the variable name from the last expr.
    exc_var: str | None = None
    m = re.match(r"^(?:str|repr)\((\w+)\)$", last_expr.strip())
    if m:
        exc_var = m.group(1)
    elif re.match(r"^\w+$", last_expr.strip()):
        exc_var = last_expr.strip()
    else:
        # `result['error']` / `exc.args[0]` — handle later.
        exc_var = None

    # Build extras from any spans BEFORE the last one (if they're simple names).
    extras: list[tuple[str, str]] = []
    note_parts: list[str] = []
    cursor = 0
    for s, e_, expr in spans[:-1]:
        note_parts.append(preceding[cursor:s])
        if re.match(r"^\w+$", expr.strip()):
            extras.append((expr.strip(), expr.strip()))
            note_parts.append(f"<{expr.strip()}>")
        else:
            note_parts.append("<expr>")
        cursor = e_
    note_parts.append(preceding[cursor:])
    note = "".join(note_parts).strip(" :").strip()

    if exc_var is None:
        # Not a simple exc-ending pattern; still usable if last expr is `result['error']`.
        m2 = re.match(r"^(result|r)\[['\"]error['\"]\]$", last_expr.strip())
        if m2:
            return note, extras, f"__dict_error__{last_expr.strip()}"
        return None, [], None
    return note, extras, exc_var


def migrate(path: Path, context: str) -> int:
    src = path.read_text()
    count = 0

    def replace(m: re.Match) -> str:
        nonlocal count
        indent = m.group("indent")
        level = m.group("level")
        level_fn = "log_error" if level == "error" else "log_warning"
        body = m.group("body")

        parsed = parse_body(body)
        if parsed[0] is None:
            return m.group(0)  # skip un-parseable
        note, extras, exc_var = parsed

        # Build error/type lines.
        if exc_var and exc_var.startswith("__dict_error__"):
            err_expr = exc_var.removeprefix("__dict_error__")
            err_line = f'"error": str({err_expr}),'
            type_line = f'"type": type({err_expr}).__name__,'
        else:
            err_line = f'"error": str({exc_var}),'
            type_line = f'"type": type({exc_var}).__name__,'

        lines = [
            f"{indent}{level_fn}(logger, {{",
            f'{indent}    {err_line}',
            f'{indent}    {type_line}',
            f'{indent}    "context": "{context}",',
        ]
        if note:
            # Escape embedded quotes for JSON-ish cleanliness.
            safe_note = note.replace('"', "'")
            lines.append(f'{indent}    "note": "{safe_note}",')
        for key, expr in extras:
            lines.append(f'{indent}    "{key}": {expr},')
        lines.append(f"{indent}}})")

        count += 1
        return "\n".join(lines)

    new_src = CALL_RE.sub(replace, src)

    if count > 0 and "from services.structured_log import" not in new_src:
        new_src = re.sub(
            r'^(logger\s*=\s*logging\.getLogger\(__name__\))',
            r'from services.structured_log import log_error, log_warning\n\n\1',
            new_src, count=1, flags=re.MULTILINE,
        )

    path.write_text(new_src)
    return count


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: migrate_logs.py <file.py> <context_name>", file=sys.stderr)
        sys.exit(2)
    fp = Path(sys.argv[1])
    ctx = sys.argv[2]
    n = migrate(fp, ctx)
    print(f"{fp.name}: migrated {n} sites (context='{ctx}')")
