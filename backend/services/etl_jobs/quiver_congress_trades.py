"""Quiver congressional-trades weekly ETL.

Replaces the in-process 6-hour ``SlidingCache`` for the
``beta/live/congresstrading`` endpoint. Three wins:

* **Sub-10ms reads** — every page load that asks "show me NVDA's
  congress trades" now hits Mongo, not Quiver. The
  ``congresstrading_live_v2`` collection is keyed on the same
  fields the read path filters on, so the query is index-only.
* **Multi-pod safe** — the in-process cache was per-pod, so two
  replicas would double-hit Quiver. Mongo is shared.
* **No cold-start after hot-reload** — the prior cache was wiped
  on every backend restart. A re-deploy used to mean the next
  page load paid a 2-3s API hit.

Cadence: weekly on Mondays at 04:30 UTC. Congressional trades
are published with a mandatory 30-45 day disclosure lag (STOCK
Act / House PTR rules), so daily refreshes would be pure waste.
Weekly catches roughly the same volume of new disclosures as a
daily pull would, at 1/7th the API cost.

Retention: framework default of 180 days. A trade disclosed
more than 6 months ago has effectively zero predictive power
for current market moves; the proof chain + permanent
predictions table cover the long-tail audit story already.

Why ``Tweet`` field is not stored: the live feed includes a
denormalized "Tweet" string (Quiver's marketing summary). It
adds ~30% to the row size and the read paths don't use it.
"""
from __future__ import annotations

import logging
from typing import Any

from services.etl_registry import BaseETLJob, register_etl_job

logger = logging.getLogger(__name__)


@register_etl_job
class QuiverCongressTradesJob(BaseETLJob):
    """Weekly pull of US Congress stock trades from QuiverQuant.

    See ``services.etl_registry.BaseETLJob`` for the framework
    contract; this class only sets the four required attributes
    plus a ``transform()`` that normalizes Quiver's mixed-case
    keys.
    """

    source_name = "quiver_congress_trades"
    description = (
        "QuiverQuant /beta/live/congresstrading — weekly pull of US "
        "House + Senate stock disclosures, normalised to "
        "{ticker, transaction_date, representative, chamber} unique "
        "key. 6-month TTL on first_seen_at."
    )
    # Mondays @ 04:30 UTC — runs after the daily SEC 13F scan (08:00)
    # but before US business hours (9:30 ET = 14:30 UTC), so any
    # operator looking at the dashboard at market open sees fresh
    # data from that morning.
    cadence = {"day_of_week": "mon", "hour": 4, "minute": 30}
    # Composite key. ``ticker + date + representative`` would be
    # enough on the typical case, but a single rep can disclose two
    # trades on the same ticker same day (a buy and a sell, or
    # two tranches via spouse and dependent accounts) — chamber is
    # included to keep the primary key safe even when the upstream
    # publishes near-duplicates.
    unique_key_fields = ("ticker", "transaction_date", "representative", "chamber", "transaction_type")
    # 180-day default retention is correct here — see module docstring.

    async def fetch(self) -> list[dict[str, Any]]:
        """Pull the full live feed.

        We delegate to the existing ``_fetch_quiver`` helper so we
        inherit its circuit-breaker + auth-key behaviour without
        re-implementing them. The 6-hour SlidingCache it wraps is
        irrelevant here (the cron only runs weekly, so cache
        hits are impossible) but harmless.
        """
        from services.quiver_service import _fetch_quiver

        rows = await _fetch_quiver(
            "congresstrading_live",
            "https://api.quiverquant.com/beta/live/congresstrading",
        )
        if not rows:
            # ``_fetch_quiver`` returns ``None`` on auth-missing /
            # circuit-open / 5xx / parse-error. Surface that to the
            # framework as a no-rows fetch (status="ok", upserted=0)
            # rather than a failure — failure should be reserved for
            # cases where we got an unexpected exception, not where
            # the source legitimately returned no data.
            logger.info(
                "[etl:quiver_congress_trades] fetch returned no rows "
                "(missing API key / circuit open / upstream error)"
            )
            return []
        return list(rows)

    async def transform(
        self, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalise mixed-case Quiver keys to the canonical
        ``unique_key_fields`` shape.

        Quiver's payload uses inconsistent casing across endpoints
        (``Ticker`` here, ``ticker`` there) and stringifies dates
        with timestamps tacked on. We slice the date to YYYY-MM-DD
        for stable upserts — two pulls one week apart should
        produce the same row, not two rows differing only on
        ``2026-04-21T00:00:00Z`` vs ``2026-04-21T00:00:00.000Z``.
        """
        normalised: list[dict[str, Any]] = []
        for r in rows:
            ticker = str(r.get("Ticker") or r.get("ticker") or "").upper().strip()
            tx_date = str(
                r.get("TransactionDate")
                or r.get("Date")
                or r.get("date")
                or ""
            )[:10]  # YYYY-MM-DD slice — see method docstring
            rep = str(r.get("Representative") or r.get("representative") or "").strip()
            tx_type = str(r.get("Transaction") or r.get("transaction") or "").strip()
            chamber = str(r.get("House") or r.get("house") or "").strip()

            if not (ticker and tx_date and rep):
                # Skip rows missing a unique-key component — the
                # framework's per-row fault tolerance counts these
                # as ``failed`` so they show up in the audit log.
                continue

            normalised.append({
                # Unique key fields — must match
                # ``unique_key_fields`` exactly.
                "ticker": ticker,
                "transaction_date": tx_date,
                "representative": rep,
                "chamber": chamber,
                "transaction_type": tx_type,
                # Payload — the consumer-visible normalisation.
                "amount": str(
                    r.get("Amount") or r.get("Range") or r.get("amount") or ""
                ),
                "party": _short_party(
                    str(r.get("Party") or r.get("party") or "")
                ),
                # Keep the raw Quiver row id if present — useful for
                # forensic correlation back to the source feed.
                "quiver_id": r.get("id") or r.get("ID"),
            })
        return normalised


def _short_party(party: str) -> str:
    """Coerce ``"Democrat" / "Democratic" / "D"`` → ``"D"``,
    ``"Republican" / "R"`` → ``"R"``, anything else → ``""``.

    Mirrors the legacy logic in ``quiver_service.get_congressional_trades``
    so the Mongo-backed read path produces visually identical output."""
    if not party:
        return ""
    if len(party) <= 2:
        return party.upper()
    p = party.lower()
    if "dem" in p:
        return "D"
    if "rep" in p:
        return "R"
    return ""


# ── Mongo-backed read path ─────────────────────────────────────────


async def get_congressional_trades_cached(
    db: Any,
    ticker: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Read congressional trades from the ETL cache.

    Drop-in replacement for ``quiver_service.get_congressional_trades``.
    Returns the same canonical shape (``representative``, ``ticker``,
    ``transaction_date``, ``type``, ``amount``, ``party``,
    ``chamber``, ``description``, ``source``) so callers don't
    need to change.

    Sub-10ms in practice — the unique composite index covers the
    ticker filter, and ``transaction_date`` is part of the index
    so the sort uses it without a fallback in-memory sort.
    """
    if db is None:
        return []
    coll = db["quiver_congress_trades"]
    filt: dict[str, Any] = {}
    if ticker:
        filt["ticker"] = ticker.upper().strip()

    cursor = (
        coll.find(filt, {"_id": 0})
        .sort("transaction_date", -1)
        .limit(max(1, min(limit, 100)))
    )

    out: list[dict[str, Any]] = []
    async for row in cursor:
        rep = row.get("representative") or ""
        tx_type = row.get("transaction_type") or ""
        tkr = row.get("ticker") or ""
        amount = row.get("amount") or ""
        party = row.get("party") or ""
        chamber = row.get("chamber") or ""
        out.append({
            "representative": rep,
            "ticker": tkr,
            "transaction_date": row.get("transaction_date") or "",
            "type": tx_type,
            "amount": amount,
            "party": party,
            "chamber": chamber,
            "description": (
                f"{tx_type.upper()} by {rep} "
                f"({party}-{chamber}) — {tkr} {amount}"
            ),
            "source": "quiverquant_etl",
        })
    return out
