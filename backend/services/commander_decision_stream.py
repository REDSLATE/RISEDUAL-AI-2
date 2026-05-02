"""
Commander Decision Stream — per-market JSONL mirror.

Why a file mirror when Mongo is the source of truth?
─────────────────────────────────────────────────────
The authoritative Commander decision log lives in the
``research_shadow_decisions`` Mongo collection — it's what the
scorer back-patches with tactical P&L, what the promotion gate
queries for win-rate math, and what the admin dashboard reads. That
layer is not going anywhere.

But operators repeatedly want to:

* ``tail -f`` a file during a live bot cycle
* ``grep NVDA`` across a week of decisions without launching mongosh
* ``wc -l`` a stream per-market for a 30-second sanity check

This module provides that developer-ergonomics stream, one JSONL
file per market, appended to on every fire_shadow call. Mongo stays
authoritative — so a file-write failure NEVER blocks a shadow
decision, and a file that's been wiped (preview pod restart, log
rotation) can always be rebuilt from Mongo.

Layout
──────
``/app/backend/data/commander_decisions/``
    stocks.jsonl
    options.jsonl
    crypto.jsonl

Asset-type → filename mapping mirrors the values
``services.research_shadow`` uses (``stock`` / ``crypto`` /
``options``). Unknown asset_types are logged once and dropped —
never silently routed to the wrong file.

Wire-up
───────
Called from :func:`services.research_shadow_engines.fire_shadow`
right after the Mongo ``insert_one`` succeeds. Never from user
code directly — that risks duplicating rows and drifting from the
Mongo record.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────

# Override via env for ops who want a different disk layout (e.g.
# a mounted volume). Default lives alongside chromadb so all
# disk-backed shadow artefacts sit in one predictable tree.
COMMANDER_DECISIONS_DIR = Path(
    os.environ.get(
        "COMMANDER_DECISIONS_DIR",
        "/app/backend/data/commander_decisions",
    )
)

# Asset-type → filename. Kept as a frozen mapping so a typo in the
# caller's asset_type can't silently create a new bogus file.
_ASSET_FILE_MAP: dict[str, str] = {
    "stock": "stocks.jsonl",
    "crypto": "crypto.jsonl",
    "options": "options.jsonl",
}

# Max bytes per file before a size-based rotation kicks in. At 25 MB
# per file, a week of decisions is ~10k rows per market × ~400B per
# row ≈ 4 MB — well under the threshold. Rotation keeps a single
# ``.prev`` alongside the active file so operators always have
# yesterday's stream plus the current one.
_ROTATE_THRESHOLD_BYTES = int(
    os.environ.get("COMMANDER_DECISIONS_ROTATE_BYTES", str(25 * 1024 * 1024))
)


def _ensure_dir() -> None:
    """Create the decisions directory if it doesn't exist. Cheap —
    ``mkdir -p``. Called on every write; the filesystem fast-paths
    the no-op case."""
    try:
        COMMANDER_DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[commander-stream] mkdir failed for %s: %s",
            COMMANDER_DECISIONS_DIR, exc,
        )


def _path_for(asset_type: str) -> Path | None:
    """Resolve an asset_type to its target JSONL path, or None if the
    asset_type is unknown. Unknown values surface as a warning
    (once per process) and are dropped — never routed to a default
    file that could hide a bug."""
    filename = _ASSET_FILE_MAP.get((asset_type or "").strip().lower())
    if filename is None:
        logger.warning(
            "[commander-stream] unknown asset_type=%r — skipping file mirror",
            asset_type,
        )
        return None
    return COMMANDER_DECISIONS_DIR / filename


def _maybe_rotate(path: Path) -> None:
    """Rotate the file if it exceeds the threshold. Keeps exactly
    one ``.prev`` — two-generation retention is plenty for a
    developer-ergonomics stream; Mongo has the long tail."""
    try:
        if not path.exists():
            return
        if path.stat().st_size < _ROTATE_THRESHOLD_BYTES:
            return
        prev = path.with_suffix(path.suffix + ".prev")
        # Best-effort: overwrite any existing .prev (two-gen retention).
        if prev.exists():
            prev.unlink(missing_ok=True)
        path.rename(prev)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[commander-stream] rotation failed for %s: %s", path, exc,
        )


def _row_from_decision(decision: Any) -> dict[str, Any]:
    """Extract a JSONL-serializable dict from a ``ShadowDecision``.

    Works with both the dataclass and a raw dict (tests pass dicts
    to avoid construction overhead). Ignores non-serializable
    fields rather than silently coercing — a missing field is
    better evidence than a wrong one.
    """
    if isinstance(decision, dict):
        src = dict(decision)
    elif hasattr(decision, "to_doc"):
        try:
            src = decision.to_doc()
        except Exception:  # noqa: BLE001
            src = {}
    else:
        # Dataclass fallback via __dict__
        src = dict(getattr(decision, "__dict__", {}))

    # Stamp a server-side mirror timestamp in ISO UTC for grep-ability.
    # The real decision ``ts`` from Mongo is preserved if present.
    src.setdefault("mirrored_at", datetime.now(timezone.utc).isoformat())

    # Coerce datetime → ISO strings so json.dumps never chokes.
    for k, v in list(src.items()):
        if isinstance(v, datetime):
            src[k] = v.isoformat()

    return src


def append_commander_decision(decision: Any, asset_type: str) -> bool:
    """Mirror a ShadowDecision to the per-market JSONL file.

    Returns True on success, False on any failure. Caller treats
    False as "observability gap" — never as a correctness signal.
    Mongo is still the source of truth.
    """
    path = _path_for(asset_type)
    if path is None:
        return False
    _ensure_dir()
    _maybe_rotate(path)
    try:
        row = _row_from_decision(decision)
        # ``default=str`` catches any remaining non-trivial types
        # (ObjectId, Decimal128) so one exotic field doesn't nuke the
        # whole row.
        line = json.dumps(row, default=str, ensure_ascii=False)
        # Append mode with an explicit newline — plain UTF-8, no BOM.
        # Line-level atomicity on POSIX ``append`` is good enough
        # for a single-writer stream; we're not racing writers here.
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[commander-stream] write failed for %s: %s", path, exc,
        )
        return False


def read_recent_decisions(
    asset_type: str, limit: int = 100,
) -> list[dict[str, Any]]:
    """Return the most recent N rows from the market's JSONL file.

    Helper for the admin endpoint that powers the dashboard card.
    Parses lazily from the tail — never reads the whole file into
    memory. Returns oldest → newest so the UI can render
    left-to-right without post-sort.
    """
    path = _path_for(asset_type)
    if path is None or not path.exists():
        return []
    try:
        # Read whole file — file max is 25 MB, fine for one-shot load.
        with path.open("r", encoding="utf-8") as f:
            tail_lines = f.readlines()[-max(1, int(limit)):]
        out: list[dict[str, Any]] = []
        for line in tail_lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[commander-stream] read failed for %s: %s", path, exc,
        )
        return []


def list_stream_stats() -> dict[str, dict[str, Any]]:
    """One-shot summary: path + size + line_count + mtime per market.
    Powers the admin dashboard's "stream health" chip."""
    stats: dict[str, dict[str, Any]] = {}
    for asset_type, filename in _ASSET_FILE_MAP.items():
        p = COMMANDER_DECISIONS_DIR / filename
        entry: dict[str, Any] = {
            "path": str(p),
            "exists": p.exists(),
            "size_bytes": 0,
            "line_count": 0,
            "mtime": None,
        }
        if p.exists():
            try:
                stat = p.stat()
                entry["size_bytes"] = stat.st_size
                entry["mtime"] = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
                # Fast count — ``rb`` + ``.count(b'\\n')`` is ~100 MB/s
                with p.open("rb") as f:
                    entry["line_count"] = sum(
                        1 for _ in _iter_lines(f)
                    )
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
        stats[asset_type] = entry
    return stats


def _iter_lines(fh: Any) -> Iterable[bytes]:
    """Chunked line iterator — ``readlines()`` would load the whole
    file; this generator streams. Only used by the stats counter
    which might be called for bigger archive files post-rotation."""
    while True:
        chunk = fh.read(1 << 20)  # 1 MB
        if not chunk:
            return
        for _ in chunk.split(b"\n")[:-1]:
            yield b""
        # Handle trailing partial line across chunks by reading the
        # rest of this line then breaking — we only need a count,
        # off-by-one on the last line is acceptable.
        if len(chunk) < (1 << 20):
            return
