"""Quiver corporate-lobbying weekly ETL.

Same pattern as ``quiver_congress_trades``. Lobbying records
also publish with a long disclosure lag (Lobbying Disclosure
Act mandates quarterly LD-2 filings, often delayed 30+ days),
so weekly refresh > daily.
"""
from __future__ import annotations

import logging
from typing import Any

from services.etl_registry import BaseETLJob, register_etl_job

logger = logging.getLogger(__name__)


@register_etl_job
class QuiverLobbyingJob(BaseETLJob):
    source_name = "quiver_lobbying"
    description = (
        "QuiverQuant /beta/live/lobbying — weekly pull of corporate "
        "lobbying disclosures, keyed on (ticker, date, client, issue). "
        "6-month TTL."
    )
    # Mon 04:40 UTC — staggered after insiders.
    cadence = {"day_of_week": "mon", "hour": 4, "minute": 40}
    unique_key_fields = ("ticker", "date", "client", "issue")

    async def fetch(self) -> list[dict[str, Any]]:
        from services.quiver_service import _fetch_quiver
        rows = await _fetch_quiver(
            "lobbying",
            "https://api.quiverquant.com/beta/live/lobbying",
        )
        if not rows:
            logger.info("[etl:quiver_lobbying] fetch returned no rows")
            return []
        return list(rows)

    async def transform(
        self, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in rows:
            ticker = str(r.get("Ticker") or r.get("ticker") or "").upper().strip()
            date = str(r.get("Date") or r.get("date") or "")[:10]
            client = str(r.get("Client") or r.get("client") or "").strip()
            issue = str(r.get("Issue") or r.get("issue") or "").strip()

            if not (ticker and date and client):
                continue

            try:
                amount_float = float(
                    r.get("Amount") or r.get("amount") or 0
                )
            except (ValueError, TypeError):
                amount_float = 0.0

            out.append({
                # Unique key fields
                "ticker": ticker,
                "date": date,
                "client": client,
                "issue": issue,
                # Payload
                "amount": amount_float,
                "quiver_id": r.get("id") or r.get("ID"),
            })
        return out


# ── Mongo-backed reader ────────────────────────────────────────────


async def get_lobbying_cached(
    db: Any,
    ticker: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Drop-in replacement for ``quiver_service.get_lobbying``."""
    if db is None:
        return []
    coll = db["quiver_lobbying"]
    filt: dict[str, Any] = {}
    if ticker:
        filt["ticker"] = ticker.upper().strip()

    cursor = (
        coll.find(filt, {"_id": 0})
        .sort("date", -1)
        .limit(max(1, min(limit, 100)))
    )

    out: list[dict[str, Any]] = []
    async for row in cursor:
        client_name = row.get("client") or ""
        amount_float = float(row.get("amount") or 0)
        issue = row.get("issue") or ""
        out.append({
            "ticker": row.get("ticker") or "",
            "client": client_name,
            "amount": amount_float,
            "issue": issue,
            "date": row.get("date") or "",
            "description": (
                f"{client_name} lobbied ${amount_float:,.0f} on {issue}"
            ),
            "source": "quiverquant_etl",
        })
    return out
