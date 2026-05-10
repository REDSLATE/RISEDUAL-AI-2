"""Shelly Memory — single source of truth for durable runtime memory.

DOCTRINE (operator audit, 2026-05-10)
--------------------------------------
* Mongo is durable. Chroma is disposable. Mongo write happens first;
  Chroma upsert is best-effort and wrapped in try/except so a Chroma
  failure never blocks the durable record.
* Every memory unit gets, at write time, automatically:
    - id (UUID4)
    - event_date (YYYY-MM-DD, normalized via ``_normalize_event_date``)
    - event_date_ordinal (int days since epoch — for Chroma range filters)
    - regime_status ("legacy" if event_date < today, else "active")
    - regime_label (= event_date)
    - created_at (full ISO UTC timestamp matching audit_trail format)
    - embedding_version ("minilm-l6-v2-default")
* No caller can opt out. ``remember(...)`` is the ONLY write path.
* Date format is enforced at the boundary — bad input raises
  ``ValueError`` so it can never be silently persisted.
* Toxic-spike prevention: the ``event_date_ordinal`` field exists
  alongside the string ``event_date`` because ChromaDB v1.x silently
  rejects ``$gte`` on string fields.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


MEMORY_COLLECTION = "shelly_memories"
EMBEDDING_VERSION = "minilm-l6-v2-default"
CHROMA_COLLECTION = "shelly_memories_v1"


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Time helpers ────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    """Full ISO UTC timestamp — matches the format used by
    audit_trail / ai_core_service / research_trader."""
    return _now_utc().isoformat()


def _today_date_iso() -> str:
    return _now_utc().date().isoformat()


def _date_to_ordinal(event_date_iso: str) -> int:
    """Days since epoch (Python's ``date.toordinal`` is days since
    year 0001 — fine as a strictly-monotonic int for ``$gte``
    range filters)."""
    return date.fromisoformat(event_date_iso).toordinal()


def _is_legacy(event_date_iso: str) -> bool:
    return event_date_iso < _today_date_iso()


# ── The single boundary normalizer ──────────────────────────────────


def _normalize_event_date(raw: Any) -> str:
    """Coerce any reasonable input into ``YYYY-MM-DD`` (UTC).

    Toxic-spike prevention table:

      * ``None`` / ``""``                 → today's UTC date
      * ``datetime`` (naive)              → assumed UTC, date part returned
      * ``datetime`` (aware)              → converted to UTC, date part returned
      * ``date``                          → ``isoformat()``
      * ``"YYYY-MM-DD"``                  → returned unchanged (fast path)
      * ``"2022-05-09T14:30:00+00:00"``   → date part of UTC-coerced datetime
      * ``"2022-05-09T14:30:00Z"``        → ditto (``Z`` → ``+00:00``)
      * anything else                     → ``ValueError`` (fail loud)

    The format guarantee: every return is exactly 10 chars,
    ``YYYY-MM-DD``. No mixed precision, no naive/aware drift.
    """
    if raw is None or raw == "":
        return _today_date_iso()

    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc).date().isoformat()

    if isinstance(raw, date):
        return raw.isoformat()

    if isinstance(raw, str):
        s = raw.strip()
        if _DATE_ONLY_RE.fullmatch(s):
            # Validate by round-tripping — catches "2024-13-40" etc.
            date.fromisoformat(s)
            return s
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"unparseable event_date: {raw!r}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()

    raise ValueError(f"unparseable event_date: {raw!r}")


# ── The label stamper (regime_status + ordinal) ─────────────────────


def _stamp_regime(metadata: Optional[dict]) -> dict:
    """Apply the 4 mandatory metadata fields. ``metadata`` may be
    ``None`` — caller doesn't have to construct it."""
    md = dict(metadata or {})
    md["event_date"] = _normalize_event_date(md.get("event_date"))
    md["event_date_ordinal"] = _date_to_ordinal(md["event_date"])
    md["regime_status"] = "legacy" if _is_legacy(md["event_date"]) else "active"
    md["regime_label"] = md["event_date"]
    return md


# ── Public stamp helper for legacy writers ─────────────────────────


def apply_doctrine_stamps(doc: dict) -> dict:
    """Stamp the 6 mandatory fields onto ``doc`` IN PLACE-FRIENDLY
    fashion (returns a new dict; caller decides what to persist).

    Intended for legacy writers that want the Shelly doctrine
    without changing their destination collection. Existing fields
    in ``doc`` are preserved; only the 6 mandatory fields are
    added/normalized.

    The stamps applied:
      * ``id`` (UUID4 — unless caller supplied a string ``id``)
      * ``embedding_version``
      * ``created_at`` (ISO UTC; if caller already set, preserved)
      * Plus ``_stamp_regime()`` on the ``metadata`` block
        (auto-creates the block if missing; normalizes
        ``event_date``; stamps ``event_date_ordinal`` /
        ``regime_status`` / ``regime_label``).
    """
    out = dict(doc or {})
    if not out.get("id"):
        out["id"] = str(uuid.uuid4())
    if not out.get("embedding_version"):
        out["embedding_version"] = EMBEDDING_VERSION
    if not out.get("created_at"):
        out["created_at"] = _now_iso()
    out["metadata"] = _stamp_regime(out.get("metadata"))
    return out


# ── Chroma client (best-effort) ─────────────────────────────────────


_chroma_client = None


def _get_chroma_collection():
    """Return a Chroma collection handle. Created lazily; persisted
    in-process. Caller MUST wrap any use of this in ``try/except`` —
    Chroma failures must never block the durable Mongo write."""
    global _chroma_client
    if _chroma_client is None:
        try:
            import chromadb
        except ImportError:
            return None
        try:
            _chroma_client = chromadb.PersistentClient(path="/tmp/shelly_chroma")
        except Exception:  # noqa: BLE001
            return None
    try:
        return _chroma_client.get_or_create_collection(CHROMA_COLLECTION)
    except Exception:  # noqa: BLE001
        return None


# ── Public write surface ────────────────────────────────────────────


async def remember(
    db,
    *,
    text: str,
    metadata: Optional[dict] = None,
    memory_id: Optional[str] = None,
) -> dict:
    """Persist a memory unit. Mongo first (durable), Chroma second
    (best-effort).

    Returns the persisted document (with ``_id`` stripped) for
    callers that need to confirm what was stored.

    Raises ``ValueError`` if ``event_date`` is malformed — caller
    converts that to HTTP 422 at the API layer. Any other failure
    propagates only if Mongo itself fails; Chroma failures log a
    warning and the call still succeeds.
    """
    if not text or not isinstance(text, str):
        raise ValueError("text must be a non-empty string")

    md = _stamp_regime(metadata or {})
    mid = memory_id or str(uuid.uuid4())
    doc = {
        "id": mid,
        "text": text,
        "metadata": md,
        "embedding_version": EMBEDDING_VERSION,
        "created_at": _now_iso(),
    }

    # ── Mongo write — durable. ──────────────────────────────────
    await db[MEMORY_COLLECTION].insert_one(dict(doc))

    # ── Chroma upsert — best-effort. ────────────────────────────
    try:
        coll = _get_chroma_collection()
        if coll is not None:
            coll.upsert(
                ids=[mid],
                documents=[text],
                metadatas=[{
                    # Chroma metadata supports str/int/float/bool only.
                    "event_date": md["event_date"],
                    "event_date_ordinal": md["event_date_ordinal"],
                    "regime_status": md["regime_status"],
                    "regime_label": md["regime_label"],
                    "embedding_version": EMBEDDING_VERSION,
                }],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("shelly: chroma upsert failed for %s: %s", mid, exc)

    # Strip the Mongo-injected ``_id`` before handing back.
    out = dict(doc)
    out.pop("_id", None)
    return out


# ── Public read surface ─────────────────────────────────────────────


async def recall(
    db,
    *,
    min_event_date: Optional[str] = None,
    max_event_date: Optional[str] = None,
    include_legacy: bool = True,
    limit: int = 50,
) -> list[dict]:
    """Mongo-only recall (durable source). Chroma is for vector
    search — that's a separate read path.

    ``min_event_date`` / ``max_event_date`` are normalized through
    ``_normalize_event_date`` so the same input shapes write+read
    paths accept work here too. Filters use the
    ``event_date_ordinal`` field for fast indexed range scans.
    """
    cap = max(1, min(int(limit or 50), 500))
    q: dict[str, Any] = {}

    ordinal_filter: dict[str, int] = {}
    if min_event_date:
        nd = _normalize_event_date(min_event_date)
        ordinal_filter["$gte"] = _date_to_ordinal(nd)
    if max_event_date:
        nd = _normalize_event_date(max_event_date)
        ordinal_filter["$lte"] = _date_to_ordinal(nd)
    if ordinal_filter:
        q["metadata.event_date_ordinal"] = ordinal_filter
    if not include_legacy:
        q["metadata.regime_status"] = "active"

    cursor = (
        db[MEMORY_COLLECTION]
        .find(q, {"_id": 0})
        .sort("metadata.event_date_ordinal", -1)
        .limit(cap)
    )
    return [doc async for doc in cursor]


async def count_by_regime(db) -> dict[str, int]:
    """Operator-facing audit — how many active vs legacy rows."""
    out = {"active": 0, "legacy": 0, "total": 0}
    try:
        out["active"] = await db[MEMORY_COLLECTION].count_documents(
            {"metadata.regime_status": "active"}
        )
        out["legacy"] = await db[MEMORY_COLLECTION].count_documents(
            {"metadata.regime_status": "legacy"}
        )
        out["total"] = await db[MEMORY_COLLECTION].count_documents({})
    except Exception as exc:  # noqa: BLE001
        logger.warning("shelly: count_by_regime failed: %s", exc)
    return out
