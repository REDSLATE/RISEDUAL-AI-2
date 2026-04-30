"""Quiver federal-contracts weekly ETL.

Same pattern as ``quiver_congress_trades``. Federal contract
awards publish via SAM.gov but Quiver normalises the firehose
into per-ticker rows. Updates are weekly at most because the
underlying federal procurement data system (FPDS) batches its
public release.

Quiver exposes two routes — ``govcontractsall`` (richer; agency
+ description) and ``govcontracts`` (aggregated; ticker + amount
+ qtr + year only). The ETL pulls ``-all``; if it 500s upstream
the framework records ``status=ok, upserted=0`` and we keep
reading the last good batch from the cache.
"""
from __future__ import annotations

import logging
from typing import Any

from services.etl_registry import BaseETLJob, register_etl_job

logger = logging.getLogger(__name__)


@register_etl_job
class QuiverGovContractsJob(BaseETLJob):
    source_name = "quiver_gov_contracts"
    description = (
        "QuiverQuant /beta/live/govcontractsall — weekly pull of "
        "federal contracts awarded to public companies, keyed on "
        "(ticker, date, agency, amount). 6-month TTL."
    )
    # Mon 04:45 UTC — staggered after lobbying.
    cadence = {"day_of_week": "mon", "hour": 4, "minute": 45}
    unique_key_fields = ("ticker", "date", "agency", "amount")

    async def fetch(self) -> list[dict[str, Any]]:
        from services.quiver_service import _fetch_quiver
        # Try the rich endpoint first.
        rows = await _fetch_quiver(
            "govcontractsall",
            "https://api.quiverquant.com/beta/live/govcontractsall",
        )
        if rows:
            return list(rows)
        # Fall back to aggregated. Less rich (no agency, no
        # description) but we still capture ticker/amount/date.
        rows = await _fetch_quiver(
            "govcontracts_aggregated",
            "https://api.quiverquant.com/beta/live/govcontracts",
        )
        if not rows:
            logger.info(
                "[etl:quiver_gov_contracts] both endpoints returned "
                "no rows; cache stays at last-good state"
            )
            return []
        return list(rows)

    async def transform(
        self, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in rows:
            ticker = str(r.get("Ticker") or r.get("ticker") or "").upper().strip()
            date = str(r.get("Date") or r.get("date") or "")[:10]
            agency = str(r.get("Agency") or r.get("agency") or "").strip()
            desc = str(r.get("Description") or r.get("description") or "").strip()

            if not (ticker and date):
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
                "agency": agency,
                "amount": amount_float,
                # Payload — clip description so a freak 10kB row
                # doesn't bloat the cache.
                "description": desc[:200],
                "quiver_id": r.get("id") or r.get("ID"),
            })
        return out


# ── Mongo-backed reader ────────────────────────────────────────────


async def get_gov_contracts_cached(
    db: Any,
    ticker: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Drop-in replacement for ``quiver_service.get_gov_contracts``."""
    if db is None:
        return []
    coll = db["quiver_gov_contracts"]
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
        out.append({
            "ticker": row.get("ticker") or "",
            "agency": row.get("agency") or "",
            "amount": float(row.get("amount") or 0),
            "description": row.get("description") or "",
            "date": row.get("date") or "",
            "source": "quiverquant_etl",
        })
    return out
