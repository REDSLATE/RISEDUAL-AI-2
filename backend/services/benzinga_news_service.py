"""
Benzinga News API client — free-tier-safe wrapper.

Why this exists
---------------
Populates the ``news_volume_zscore`` (and eventually ``news_sentiment_abs``)
input fields of ``MarketTelemetry`` so the dormant ``NEWS_SHOCK`` branch
of Patent M's failure-mode classifier gains a reliable feeder. Same
"additive, never dominant" discipline as the options-flow enrichment:
the classifier still owns the decision; this service only provides data.

Free-tier discipline
--------------------
Three layered guardrails keep the free tier from exhausting its quota:

1. **Per-process rate limiter** — a simple monotonic-clock gate enforcing
   ``BENZINGA_MIN_INTERVAL_SECONDS`` (default 2s) between outbound calls.
   No token bucket; we want strict serialization under FastAPI's single
   event loop.
2. **Daily call ceiling** — ``BENZINGA_DAILY_CALL_CEILING`` (default 500)
   tracked in Mongo (``benzinga_call_stats`` single-doc by UTC date).
   Once the ceiling is hit, ``fetch_news`` returns the empty-list sentinel
   with ``rate_limited=True`` instead of making the HTTP call.
3. **Mongo 5-min cache** — authoritative layer. Callers hit the cache
   first; API is the fallback. Cache TTL configurable via
   ``BENZINGA_CACHE_TTL_SECONDS``.

Auth scheme
-----------
Benzinga uses a query-param ``token`` (NOT Bearer). This is counter to
common REST patterns; codified explicitly in
``_build_params`` so a refactor can't silently switch to Authorization
header and break auth on every call.

Error discipline
----------------
* Missing / empty key → returns empty-list sentinel with
  ``disabled=True``. No exception — this is the default state on a
  fresh deploy where the operator hasn't pasted the key yet.
* 4xx client errors → log + return empty sentinel + ``error_code`` tag.
  NEVER retried (a 401 Unauthorized won't become valid on retry).
* 5xx / 429 → small bounded retry (3 attempts, exponential backoff
  1s→2s→4s). After exhaustion, return sentinel + ``error_code=upstream``.
* Network errors → same as 5xx path.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.benzinga.com/api/v2"
_NEWS_PATH = "/news"

# Module-level rate-limit gate. A single asyncio.Lock makes sure two
# coroutines in the same event loop can't sneak through the interval
# check at the same time. Cross-process rate limiting isn't needed
# today — the FastAPI app is a single process — but the daily ceiling
# in Mongo gives us multi-process safety once we scale horizontally.
_rate_lock = asyncio.Lock()
_last_call_monotonic: float = 0.0


def _env_flag(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _min_interval_seconds() -> float:
    try:
        return max(0.0, float(_env_flag("BENZINGA_MIN_INTERVAL_SECONDS", "2")))
    except (TypeError, ValueError):
        return 2.0


def _daily_ceiling() -> int:
    try:
        return max(0, int(_env_flag("BENZINGA_DAILY_CALL_CEILING", "500")))
    except (TypeError, ValueError):
        return 500


def _api_key() -> str:
    return (_env_flag("BENZINGA_API_KEY", "") or "").strip()


def _today_key() -> str:
    """UTC-date key for the daily-ceiling counter. Strings stay stable
    across Mongo round-trips; using dates avoids tz drift entirely."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _daily_call_count(db: Any) -> int:
    """Read today's Benzinga call count. Returns 0 if Mongo is down —
    fail-safe toward MORE calls, not fewer, because the ceiling is a
    budget guard, not a correctness guard."""
    if db is None:
        return 0
    try:
        doc = await db.benzinga_call_stats.find_one(
            {"_id": _today_key()}, {"count": 1}
        )
        return int((doc or {}).get("count", 0))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[benzinga] daily count read failed (fail-safe to 0): %s", exc)
        return 0


async def _increment_daily_count(db: Any) -> None:
    """Best-effort ceiling counter bump. Failures are logged, never
    raised — a telemetry write failure must not block news fetches."""
    if db is None:
        return
    try:
        await db.benzinga_call_stats.update_one(
            {"_id": _today_key()},
            {
                "$inc": {"count": 1},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[benzinga] daily count increment failed: %s", exc)


async def _rate_limit_wait() -> None:
    """Enforce ``min_interval`` seconds between outbound Benzinga calls.
    Held inside ``_rate_lock`` so two parallel coroutines serialize."""
    global _last_call_monotonic
    async with _rate_lock:
        elapsed = time.monotonic() - _last_call_monotonic
        wait = _min_interval_seconds() - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_monotonic = time.monotonic()


def _build_params(
    tickers: list[str],
    *,
    date_from: str | None,
    page_size: int,
    display_output: str,
) -> dict[str, Any]:
    """Shape the query string. Token goes in as a QUERY PARAM —
    Benzinga does NOT accept an Authorization header."""
    params: dict[str, Any] = {
        "token": _api_key(),
        "tickers": ",".join(sorted({t.strip().upper() for t in tickers if t})),
        "pageSize": max(1, min(100, int(page_size))),
        "displayOutput": display_output,
    }
    if date_from:
        params["dateFrom"] = date_from
    return params


async def fetch_news(
    db: Any,
    tickers: list[str],
    *,
    minutes: int = 30,
    page_size: int = 50,
    display_output: str = "headline",
) -> dict[str, Any]:
    """Fetch recent news for a set of tickers.

    Returns a dict ``{articles, meta}`` even on failure — the callers
    want a predictable shape so failure branches don't require special
    handling at every call site. ``meta`` carries:

    * ``disabled`` — True iff the API key is empty (default state)
    * ``rate_limited`` — True iff we declined the call due to the
      daily ceiling; articles is ``[]``
    * ``error_code`` — ``None``, ``"upstream"``, ``"auth"``,
      ``"bad_request"``, ``"network"``
    * ``remaining``, ``limit``, ``reset`` — verbatim from Benzinga's
      ``X-RateLimit-*`` headers when present
    * ``fetched_at`` — UTC ISO timestamp of the call (or attempt)
    * ``daily_calls_used`` — counter AFTER this invocation
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    if not _api_key():
        return {
            "articles": [],
            "meta": {
                "disabled": True,
                "rate_limited": False,
                "error_code": "missing_key",
                "fetched_at": now_iso,
            },
        }

    if not tickers:
        return {
            "articles": [],
            "meta": {
                "disabled": False,
                "rate_limited": False,
                "error_code": "empty_ticker_list",
                "fetched_at": now_iso,
            },
        }

    ceiling = _daily_ceiling()
    used = await _daily_call_count(db)
    if ceiling > 0 and used >= ceiling:
        logger.warning(
            "[benzinga] daily ceiling hit (%d/%d) — declining call",
            used, ceiling,
        )
        return {
            "articles": [],
            "meta": {
                "disabled": False,
                "rate_limited": True,
                "error_code": "daily_ceiling_hit",
                "fetched_at": now_iso,
                "daily_calls_used": used,
            },
        }

    date_from = (datetime.now(timezone.utc) - timedelta(minutes=max(1, int(minutes)))).strftime("%Y-%m-%d")
    params = _build_params(
        tickers,
        date_from=date_from,
        page_size=page_size,
        display_output=display_output,
    )

    await _rate_limit_wait()

    # Bounded retry loop — 3 attempts, exponential backoff 1s→2s→4s.
    # Only 5xx / 429 / network errors retry; 4xx non-429 fail immediately.
    articles: list[dict[str, Any]] = []
    error_code: str | None = None
    rate_headers: dict[str, str | None] = {"remaining": None, "limit": None, "reset": None}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(_BASE_URL + _NEWS_PATH, params=params)
                rate_headers["remaining"] = resp.headers.get("X-RateLimit-Remaining")
                rate_headers["limit"] = resp.headers.get("X-RateLimit-Limit")
                rate_headers["reset"] = resp.headers.get("X-RateLimit-Reset")

            if resp.status_code == 200:
                data = resp.json()
                # Response is a JSON array of articles — per Benzinga docs.
                if isinstance(data, list):
                    articles = data
                elif isinstance(data, dict) and "data" in data:
                    # Defensive: some historical responses wrap in "data".
                    articles = data.get("data") or []
                break

            if resp.status_code in (401, 403):
                logger.error("[benzinga] auth error %d — check API key", resp.status_code)
                error_code = "auth"
                break

            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                logger.warning(
                    "[benzinga] 4xx status=%d body=%s — not retrying",
                    resp.status_code, resp.text[:200],
                )
                error_code = "bad_request"
                break

            # 5xx / 429 — retry
            logger.info(
                "[benzinga] status=%d attempt=%d/3 — backing off",
                resp.status_code, attempt + 1,
            )
            error_code = "upstream"

        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            logger.info(
                "[benzinga] network error attempt=%d/3: %s", attempt + 1, exc,
            )
            error_code = "network"

        if attempt < 2:
            await asyncio.sleep(2 ** attempt)  # 1s, 2s

    # Bump the daily counter after every ATTEMPT (not just success) —
    # the quota is per-call, not per-successful-call.
    await _increment_daily_count(db)

    return {
        "articles": articles,
        "meta": {
            "disabled": False,
            "rate_limited": False,
            "error_code": error_code if not articles else None,
            "fetched_at": now_iso,
            "daily_calls_used": used + 1,
            "remaining": rate_headers["remaining"],
            "limit": rate_headers["limit"],
            "reset": rate_headers["reset"],
        },
    }


# ── Metric computation — pure functions ───────────────────────────


def count_recent_articles(articles: list[dict[str, Any]], minutes: int = 30) -> int:
    """Count how many articles in ``articles`` were published within
    the last ``minutes`` minutes. Robust to malformed ``created``
    fields — an unparseable timestamp counts as out-of-window (safer
    than inflating the news-volume signal on garbage data)."""
    if not articles:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(minutes)))
    count = 0
    for a in articles:
        created = a.get("created") or a.get("updated") or ""
        ts = _parse_benzinga_timestamp(created)
        if ts and ts >= cutoff:
            count += 1
    return count


def _parse_benzinga_timestamp(raw: str) -> datetime | None:
    """Parse Benzinga's RFC 2822 timestamp (``"Wed, 19 Nov 2025 00:49:52 -0400"``).

    Returns tz-aware UTC datetime or ``None``. Benzinga has historically
    also returned ISO-8601 in some endpoints; both are handled.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()

    # RFC 2822 first — the documented format.
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass

    # ISO-8601 fallback.
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def summarize_articles(articles: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce a compact per-symbol news summary for admin display /
    telemetry payloads. Pure function, no I/O."""
    count_30 = count_recent_articles(articles, minutes=30)
    count_240 = count_recent_articles(articles, minutes=240)
    channels: dict[str, int] = {}
    titles: list[str] = []
    for a in articles[:10]:
        title = (a.get("title") or "").strip()
        if title:
            titles.append(title)
        for ch in (a.get("channels") or []):
            name = (ch.get("name") if isinstance(ch, dict) else str(ch)) or ""
            if name:
                channels[name] = channels.get(name, 0) + 1
    return {
        "count_last_30min": count_30,
        "count_last_4h": count_240,
        "total_returned": len(articles),
        "top_channels": sorted(channels.items(), key=lambda kv: kv[1], reverse=True)[:5],
        "sample_titles": titles[:5],
    }
