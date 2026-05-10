"""Shelly Perception (Doctrine v2).

Operator directive (2026-05-12):

    "Shelly is the scribe and MongoDB is the source of truth.
     Perception is also Shelly. Any information sourced must be
     labeled according to MongoDB standards. If malformed it still
     must be labeled legacy, date, time and ID. Place malformed in a
     file of its own, numbered by the number of documents in file.
     ChromaDB if used is temporary and can be wiped if necessary."

This module owns the *perception* half of the doctrine — the stamp
and routing of inbound information sourced from any lane (chat,
market feeds, agents, scrapers, ...). It sits next to
``shelly_memory.py`` (which owns durable writes/reads of the canonical
``shelly_memories`` collection) and shares its constants + boundary
normalizer.

Hard rules:
  * ``perceive()`` is the only inbound entry-point. It NEVER raises —
    any failure routes the payload to the malformed-quarantine bin.
  * Malformed docs land in ``shelly_legacy_malformed`` carrying the
    full set of MongoDB-standard stamps (``legacy_id``,
    ``legacy_date``, ``legacy_time``, ``created_at``,
    ``embedding_version``, ``source``, ``error``, ``doc_number``,
    ``raw_payload``).
  * ``doc_number`` is strictly sequential within the malformed
    collection, atomic across concurrent writes (counter-collection
    upsert via ``find_one_and_update`` + ``$inc``).
  * Mongo is durable. Chroma never sees malformed docs.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from .shelly_memory import (
    COUNTERS_COLLECTION,
    EMBEDDING_VERSION,
    MALFORMED_COLLECTION,
    _normalize_event_date,
    _now_utc,
    remember,
)

logger = logging.getLogger(__name__)


# ── Atomic sequence counter ────────────────────────────────────────


async def _next_doc_number(db, *, key: str = MALFORMED_COLLECTION) -> int:
    """Atomic per-collection sequence counter.

    Uses a Mongo ``find_one_and_update`` with ``$inc`` upsert on the
    ``shelly_counters`` collection so concurrent quarantine writes
    cannot collide on the same ``doc_number``. Falls back to a
    best-effort ``count_documents`` + 1 if the counter mechanism
    itself errors out — even in failure mode the doc still gets a
    number, the doctrine just degrades from "strictly sequential" to
    "approximately sequential".
    """
    try:
        result = await db[COUNTERS_COLLECTION].find_one_and_update(
            {"_id": key},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True,
            projection={"_id": 0, "seq": 1},
        )
        if isinstance(result, dict) and "seq" in result:
            return int(result["seq"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("shelly: counter atomic update failed: %s", exc)

    try:
        return int(await db[key].count_documents({})) + 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("shelly: counter fallback failed: %s", exc)
        return 0


# ── Malformed-doc label stamper ────────────────────────────────────


def _stamp_malformed(
    raw: Any,
    *,
    source: str,
    error: str,
    doc_number: int,
) -> dict:
    """Build a malformed-quarantine doc with MongoDB-standard labels.

    The labels are applied unconditionally — even if ``raw`` is
    completely garbage, every quarantined doc carries:

      * ``legacy_id``        — UUID4 (always)
      * ``legacy_date``      — YYYY-MM-DD UTC (today if unparseable)
      * ``legacy_time``      — full ISO UTC timestamp
      * ``created_at``       — full ISO UTC timestamp (audit format)
      * ``embedding_version``— doctrine-pinned label
      * ``source``           — caller-supplied origin name
      * ``error``            — why it was quarantined
      * ``doc_number``       — sequential within MALFORMED_COLLECTION
      * ``raw_payload``      — the original payload preserved verbatim
                              (str/dict/None passthrough; anything
                              else stringified via ``repr`` so Mongo
                              accepts it)
    """
    now = _now_utc()
    salvaged_date: Optional[str] = None
    if isinstance(raw, dict):
        for key in ("event_date", "date", "timestamp", "ts", "created_at"):
            if key in raw and raw[key] not in (None, ""):
                try:
                    salvaged_date = _normalize_event_date(raw[key])
                    break
                except ValueError:
                    continue

    legacy_date = salvaged_date or now.date().isoformat()

    if isinstance(raw, (dict, list, str, int, float, bool)) or raw is None:
        preserved = raw
    else:
        preserved = repr(raw)

    return {
        "legacy_id": str(uuid.uuid4()),
        "legacy_date": legacy_date,
        "legacy_time": now.isoformat(),
        "created_at": now.isoformat(),
        "embedding_version": EMBEDDING_VERSION,
        "source": str(source or "unknown"),
        "error": str(error or "unspecified"),
        "doc_number": int(doc_number),
        "raw_payload": preserved,
    }


# ── Quarantine writer ──────────────────────────────────────────────


async def quarantine_malformed(
    db,
    *,
    raw: Any,
    source: str,
    error: str,
) -> dict:
    """Last-resort writer for malformed perceptions.

    Doctrine v2 contract:
      * Always applies legacy/date/time/id labels to the doc.
      * Always assigns a sequential ``doc_number`` within
        MALFORMED_COLLECTION (atomic counter).
      * Always preserves the original payload under ``raw_payload``.

    Returns the stamped malformed document (Mongo ``_id`` stripped).
    """
    seq = await _next_doc_number(db, key=MALFORMED_COLLECTION)
    doc = _stamp_malformed(raw, source=source, error=error, doc_number=seq)
    await db[MALFORMED_COLLECTION].insert_one(dict(doc))
    out = dict(doc)
    out.pop("_id", None)
    return out


# ── Payload → text coercion ────────────────────────────────────────


def _coerce_payload_to_text(payload: Any) -> Optional[str]:
    """Best-effort text extraction for a perception payload.

    Order of precedence:
      1. ``payload["text"]`` if a non-empty string.
      2. The whole payload if it IS a non-empty string.
      3. JSON-stringified dict (deterministic key order) so the
         scribe can still index something searchable.
      4. ``None`` if nothing salvageable — caller routes to
         quarantine.
    """
    if isinstance(payload, dict):
        t = payload.get("text")
        if isinstance(t, str) and t.strip():
            return t
        try:
            import json as _json
            return _json.dumps(payload, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(payload, str) and payload.strip():
        return payload
    return None


# ── Public perception entry-point ──────────────────────────────────


async def perceive(
    db,
    *,
    payload: Any,
    source: str,
    text: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Doctrine v2 perception entry-point.

    Parameters
    ----------
    payload : Any
        The raw inbound information. May be a dict (typical), a
        string (chat-style), or anything else.
    source : str
        Origin label — e.g. ``"chat"``, ``"market_feed"``,
        ``"agent.alpha"``. Stamped onto the durable record so the
        operator can audit by lane.
    text : str, optional
        Explicit text override. If absent, derived from ``payload``
        via ``_coerce_payload_to_text``.
    metadata : dict, optional
        Extra metadata to merge under the ``metadata`` block before
        stamping. The ``source`` label is auto-injected here.

    Returns
    -------
    dict
        Envelope describing what happened::

            {
              "ok": True,
              "lane": "memory",   # or "malformed"
              "doc": <persisted document>,
            }

    Never raises. If anything in the stamp/write pipeline fails the
    payload is routed to ``quarantine_malformed`` instead and the
    envelope reports ``lane="malformed"``.
    """
    md = dict(metadata or {})
    md.setdefault("source", str(source or "unknown"))
    if isinstance(payload, dict):
        for k in ("event_date", "symbol", "lane"):
            if k in payload and k not in md:
                md[k] = payload[k]

    derived_text = text if text is not None else _coerce_payload_to_text(payload)

    if not derived_text:
        doc = await quarantine_malformed(
            db,
            raw=payload,
            source=source,
            error="empty_or_unscribable_payload",
        )
        return {"ok": True, "lane": "malformed", "doc": doc}

    try:
        persisted = await remember(
            db,
            text=derived_text,
            metadata=md,
        )
        return {"ok": True, "lane": "memory", "doc": persisted}
    except ValueError as exc:
        doc = await quarantine_malformed(
            db,
            raw=payload,
            source=source,
            error=f"stamp_error: {exc}",
        )
        return {"ok": True, "lane": "malformed", "doc": doc}
    except Exception as exc:  # noqa: BLE001
        logger.warning("shelly.perceive: routing to malformed: %s", exc)
        doc = await quarantine_malformed(
            db,
            raw=payload,
            source=source,
            error=f"perception_failure: {type(exc).__name__}: {exc}",
        )
        return {"ok": True, "lane": "malformed", "doc": doc}


# ── Operator audit reader ──────────────────────────────────────────


async def list_malformed(
    db,
    *,
    limit: int = 50,
    min_doc_number: Optional[int] = None,
) -> list[dict]:
    """Operator-facing read of the malformed-quarantine bin.

    Returns rows sorted by ``doc_number`` ascending so the operator
    sees them in arrival order — the file is "numbered by the number
    of documents in file" per the doctrine.
    """
    cap = max(1, min(int(limit or 50), 500))
    q: dict[str, Any] = {}
    if min_doc_number is not None:
        q["doc_number"] = {"$gte": int(min_doc_number)}
    cursor = (
        db[MALFORMED_COLLECTION]
        .find(q, {"_id": 0})
        .sort("doc_number", 1)
        .limit(cap)
    )
    return [doc async for doc in cursor]
