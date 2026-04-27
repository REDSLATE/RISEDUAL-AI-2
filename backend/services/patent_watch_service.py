"""Patent Watch — USPTO patent monitoring service.

What this is
------------
Lets an admin save a small set of "watch queries" (assignee
organization, inventor last name, or keyword in title) and
periodically pulls matching patent filings from the USPTO Open
Data Portal. Results land in a Mongo cache so the admin UI
doesn't pay an external API hit on every page load.

API choice + auth
-----------------
Uses the USPTO Open Data Portal (ODP) endpoint at
``api.uspto.gov``. Legacy ``patentsview.org`` redirects here as
of the March 2026 migration. The base URL is env-overridable
(``USPTO_API_BASE_URL``).

ODP requires an API key on every call via the ``X-API-KEY``
header. Get one by:
  1. Creating a MyUSPTO account at https://www.uspto.gov/
  2. Linking it to ID.me (mandatory)
  3. Visiting MyODP from the data.uspto.gov nav to retrieve
     the key
Then set ``USPTO_API_KEY=<your-key>`` in ``/app/backend/.env``
and restart backend. Without a key set, ``_fetch_from_uspto``
short-circuits with ``error="missing_api_key"`` so the admin UI
can render a clear setup banner instead of an opaque 403.

Failure mode
------------
Any HTTP failure logs a warning, leaves the cache untouched,
and returns ``{"fetched": 0, "error": "..."}``. The admin UI
shows the last successful fetch timestamp + error string from
the most recent attempt. We never raise from background paths.

DB collections (no _id ever returned to clients)
------------------------------------------------
- ``patent_watch_queries`` — saved queries
- ``patent_watch_results`` — fetched patent rows, deduped on
  (query_id, patent_number)
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


_USPTO_BASE_URL = os.environ.get(
    "USPTO_API_BASE_URL",
    "https://api.uspto.gov/api/v1/patent/applications/search",
)
_USPTO_API_KEY = os.environ.get("USPTO_API_KEY", "").strip()
_HTTP_TIMEOUT_S = float(os.environ.get("USPTO_API_TIMEOUT_S", "12"))
_DEFAULT_LIMIT = int(os.environ.get("USPTO_API_LIMIT", "25"))
_USER_AGENT = "RisedualAI-PatentWatch/1.0"

# Field selection for the response. Kept narrow so the cache
# doesn't bloat with full-text abstracts on every row. Admin can
# click through to USPTO if they want the body.
_RESPONSE_FIELDS = [
    "applicationNumberText",
    "applicationMetaData.inventionTitle",
    "applicationMetaData.filingDate",
    "applicationMetaData.firstApplicantName",
    "applicationMetaData.firstInventorName",
    "applicationMetaData.earliestPublicationNumber",
    "applicationMetaData.publicationDateBag",
]


# ── Mongo glue ────────────────────────────────────────────────────


_db: Any = None


def set_db(database: Any) -> None:
    global _db
    _db = database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_id(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Mongo always injects ``_id`` into result dicts on insert.
    Callers must never leak it to the API surface."""
    return {k: v for k, v in doc.items() if k != "_id"}


# ── Query CRUD ────────────────────────────────────────────────────


async def list_queries() -> List[Dict[str, Any]]:
    """Return all saved queries, newest first. ``_id`` stripped."""
    if _db is None:
        return []
    cursor = _db.patent_watch_queries.find(
        {}, {"_id": 0},
    ).sort("created_at", -1)
    return [doc async for doc in cursor]


async def create_query(
    *,
    label: str,
    assignee: Optional[str] = None,
    inventor_last: Optional[str] = None,
    keyword: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a new watch query. At least one of (assignee,
    inventor_last, keyword) must be non-empty — enforced by the
    route layer; this function trusts its inputs."""
    if _db is None:
        raise RuntimeError("patent_watch DB not initialised")

    record = {
        "id": str(uuid.uuid4()),
        "label": label.strip(),
        "assignee": (assignee or "").strip() or None,
        "inventor_last": (inventor_last or "").strip() or None,
        "keyword": (keyword or "").strip() or None,
        "created_at": _now_iso(),
        "last_fetch_at": None,
        "last_fetch_count": 0,
        "last_error": None,
    }
    await _db.patent_watch_queries.insert_one(record)
    return _strip_id(record)


async def delete_query(query_id: str) -> bool:
    """Drop a query AND its cached results. Returns True if
    a query was actually deleted."""
    if _db is None:
        return False
    res = await _db.patent_watch_queries.delete_one({"id": query_id})
    if res.deleted_count == 0:
        return False
    await _db.patent_watch_results.delete_many({"query_id": query_id})
    return True


# ── Results read ──────────────────────────────────────────────────


async def list_results(
    *,
    query_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Paginated cached results, newest-filing first."""
    if _db is None:
        return []
    limit = max(1, min(int(limit or 50), 200))
    flt: Dict[str, Any] = {}
    if query_id:
        flt["query_id"] = query_id
    cursor = (
        _db.patent_watch_results
        .find(flt, {"_id": 0})
        .sort("patent_date", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]


# ── USPTO fetch ───────────────────────────────────────────────────


def _build_query_payload(query: Dict[str, Any]) -> str:
    """Translate a saved query into a USPTO ODP Lucene `q` string.

    The Patent File Wrapper search endpoint accepts a plain Lucene
    query (NOT a JSON filter object). Multiple user conditions are
    OR'd together — any application matching at least one of
    (assignee / inventor / keyword) qualifies.

    Quoting matters: org names like "Acme Industries" need to be
    wrapped in double quotes so Lucene treats them as a phrase.
    Single-token terms can pass through bare.

    Returns an empty string when no conditions are set; the fetch
    layer short-circuits in that case.
    """
    def _quote(term: str) -> str:
        term = term.strip()
        if not term:
            return ""
        # Phrase-quote anything with whitespace so Lucene matches
        # the full string instead of OR-ing each token.
        return f'"{term}"' if " " in term else term

    parts: list[str] = []
    for key in ("assignee", "inventor_last", "keyword"):
        v = _quote(query.get(key) or "")
        if v:
            parts.append(v)

    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return " OR ".join(parts)


async def _fetch_from_uspto(query: Dict[str, Any]) -> Dict[str, Any]:
    """One HTTP call to USPTO. Returns ``{"rows": [...], "error": None}``
    on success or ``{"rows": [], "error": "..."}`` on any failure.

    Never raises. Background scheduler must always make forward
    progress.
    """
    payload = _build_query_payload(query)
    if not payload:
        return {"rows": [], "error": "empty_query"}

    if not _USPTO_API_KEY:
        return {"rows": [], "error": "missing_api_key"}

    params = {
        "q": payload,
        "limit": _DEFAULT_LIMIT,
    }
    headers = {
        "User-Agent": _USER_AGENT,
        "X-API-KEY": _USPTO_API_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.get(
                _USPTO_BASE_URL,
                params=params,
                headers=headers,
            )
        if resp.status_code != 200:
            return {
                "rows": [],
                "error": f"http_{resp.status_code}",
            }
        body = resp.json() or {}
    except httpx.TimeoutException:
        return {"rows": [], "error": "timeout"}
    except httpx.HTTPError as exc:
        return {"rows": [], "error": f"http_error:{type(exc).__name__}"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[patent-watch] uspto fetch unexpected: %s", exc)
        return {"rows": [], "error": f"unexpected:{type(exc).__name__}"}

    # USPTO ODP Patent File Wrapper response envelope:
    #   {"count": N, "patentFileWrapperDataBag": [...rows...]}
    # Fall back to other shapes for forward-compat with any future
    # endpoint variants.
    rows = (
        body.get("patentFileWrapperDataBag")
        or body.get("data")
        or body.get("results")
        or []
    )
    if not isinstance(rows, list):
        return {"rows": [], "error": "malformed_response"}
    return {"rows": rows, "error": None}


def _normalise_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Coerce a USPTO ODP Patent File Wrapper row into our compact
    cache shape.

    Real-row schema (from `api.uspto.gov`):
        row.applicationNumberText                 → application id
        row.applicationMetaData.inventionTitle    → title
        row.applicationMetaData.filingDate        → filing date
        row.applicationMetaData.firstApplicantName→ assignee/applicant
        row.applicationMetaData.firstInventorName → inventor
        row.applicationMetaData.earliestPublicationNumber → US pre-grant pub#

    Falls back to legacy snake_case / camelCase keys so the
    function tolerates older response payloads (and keeps the
    existing unit tests honest).

    Returns ``None`` when the row has no identifying number — that
    field is the dedupe key in the cache.
    """
    md = row.get("applicationMetaData") or {}

    application_number = (
        row.get("applicationNumberText")
        or row.get("application_number")
        or row.get("patent_number")
        or row.get("patentNumber")
    )
    publication_number = md.get("earliestPublicationNumber") or row.get("publicationNumber")

    # Prefer the publication number (pre-grant pub) for the cache
    # key when present — it's the URL-routable identifier on
    # patents.google.com. Fall back to the application number.
    canonical_id = publication_number or application_number
    if not canonical_id:
        return None

    title = (
        md.get("inventionTitle")
        or row.get("patent_title")
        or row.get("patentTitle")
    )

    filing_date = md.get("filingDate") or row.get("patent_date") or row.get("patentDate")

    pub_dates = md.get("publicationDateBag") or []
    pub_date = pub_dates[0] if isinstance(pub_dates, list) and pub_dates else None
    # Use the publication date when available — that's when the
    # filing actually became public knowledge. Otherwise show the
    # filing date so the row is still sortable.
    sort_date = pub_date or filing_date

    assignee = (
        md.get("firstApplicantName")
        or row.get("assignee_organization")
        or row.get("assigneeOrganization")
    )
    if isinstance(assignee, list):
        assignee = assignee[0] if assignee else None

    inventor = md.get("firstInventorName")
    if not inventor:
        # Legacy snake_case fallback for older payloads.
        inv_first = row.get("inventor_name_first") or row.get("inventorNameFirst")
        inv_last = row.get("inventor_name_last") or row.get("inventorNameLast")
        if isinstance(inv_first, list):
            inv_first = inv_first[0] if inv_first else None
        if isinstance(inv_last, list):
            inv_last = inv_last[0] if inv_last else None
        inventor = " ".join(s for s in [inv_first, inv_last] if s) or None

    # Build a Google Patents deep-link. Prefer the publication
    # number (most likely to resolve), fall back to the app number.
    if publication_number:
        url = f"https://patents.google.com/patent/{publication_number}"
    elif application_number:
        url = f"https://patents.google.com/?q=%22{application_number}%22"
    else:
        url = None

    return {
        "patent_number": str(canonical_id),
        "application_number": str(application_number) if application_number else None,
        "publication_number": str(publication_number) if publication_number else None,
        "title": title,
        "abstract": row.get("patent_abstract") or row.get("patentAbstract"),
        "patent_date": sort_date,
        "filing_date": filing_date,
        "assignee": assignee,
        "inventor": inventor,
        "url": url,
    }


async def refresh_query(query_id: str) -> Dict[str, Any]:
    """Fetch + upsert results for a single saved query.

    Updates the query's ``last_fetch_at`` / ``last_fetch_count`` /
    ``last_error`` so the UI can render "Last refreshed N
    minutes ago" without a separate metadata fetch.
    """
    if _db is None:
        return {"fetched": 0, "error": "db_unavailable"}

    query = await _db.patent_watch_queries.find_one(
        {"id": query_id}, {"_id": 0},
    )
    if not query:
        return {"fetched": 0, "error": "query_not_found"}

    fetch = await _fetch_from_uspto(query)
    rows = fetch["rows"]
    err = fetch["error"]

    inserted = 0
    for raw in rows:
        record = _normalise_row(raw)
        if not record:
            continue
        record["query_id"] = query_id
        record["fetched_at"] = _now_iso()
        # Idempotent upsert keyed on (query_id, patent_number).
        await _db.patent_watch_results.update_one(
            {
                "query_id": query_id,
                "patent_number": record["patent_number"],
            },
            {"$set": record},
            upsert=True,
        )
        inserted += 1

    await _db.patent_watch_queries.update_one(
        {"id": query_id},
        {"$set": {
            "last_fetch_at": _now_iso(),
            "last_fetch_count": inserted,
            "last_error": err,
        }},
    )
    return {"fetched": inserted, "error": err}


async def refresh_all_queries() -> Dict[str, Any]:
    """Daily scheduler entry point. Returns aggregate stats so
    the scheduler log shows a single readable line."""
    if _db is None:
        return {"queries": 0, "fetched": 0, "errors": 0}

    queries = await list_queries()
    total_fetched = 0
    error_count = 0
    for q in queries:
        result = await refresh_query(q["id"])
        total_fetched += result.get("fetched", 0)
        if result.get("error"):
            error_count += 1

    return {
        "queries": len(queries),
        "fetched": total_fetched,
        "errors": error_count,
    }


async def ensure_indexes() -> None:
    """Best-effort index creation. Cheap to call repeatedly."""
    if _db is None:
        return
    try:
        await _db.patent_watch_queries.create_index("id", unique=True)
        await _db.patent_watch_results.create_index(
            [("query_id", 1), ("patent_number", 1)], unique=True,
        )
        await _db.patent_watch_results.create_index([("patent_date", -1)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[patent-watch] index create failed: %s", exc)


def get_config_status() -> Dict[str, Any]:
    """Light read of env config for the admin UI banner.

    Never returns the key itself — only whether one is set,
    plus the human-readable instructions for getting one.
    """
    return {
        "api_key_configured": bool(_USPTO_API_KEY),
        "api_base_url": _USPTO_BASE_URL,
        "setup_url": "https://data.uspto.gov/apis/getting-started",
    }
