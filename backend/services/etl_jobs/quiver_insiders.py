"""Quiver insider-trading (Form 4) weekly ETL.

Same pattern as ``quiver_congress_trades``: weekly pull of the
full live feed, normalise mixed-case payload, upsert into
``quiver_insiders`` with 180-day TTL. See that module's docstring
for the architecture rationale.

Quiver's ``/beta/live/insiders`` has been 500-ing intermittently
for weeks — when it does, ``fetch()`` returns an empty list and
the framework records ``status=ok, upserted=0``. Reads still work;
they just serve whatever the last successful pull produced (up
to 180 days old before the TTL drops them).
"""
from __future__ import annotations

import logging
from typing import Any

from services.etl_registry import BaseETLJob, register_etl_job

logger = logging.getLogger(__name__)


@register_etl_job
class QuiverInsidersJob(BaseETLJob):
    source_name = "quiver_insiders"
    description = (
        "QuiverQuant /beta/live/insiders — weekly pull of SEC Form 4 "
        "insider trades, keyed on (ticker, filed_date, insider_name, "
        "trade_type, qty). 6-month TTL on first_seen_at."
    )
    # Mon 04:35 UTC — staggered 5 min after congresstrading so we
    # don't slam Quiver with three back-to-back requests.
    cadence = {"day_of_week": "mon", "hour": 4, "minute": 35}
    # qty is part of the key because an insider can do two tranches
    # the same day (e.g. an option exercise + open-market sale at
    # different prices).
    unique_key_fields = (
        "ticker", "filed_date", "insider_name", "trade_type", "qty",
    )

    async def fetch(self) -> list[dict[str, Any]]:
        from services.quiver_service import _fetch_quiver
        rows = await _fetch_quiver(
            "insiders",
            "https://api.quiverquant.com/beta/live/insiders",
        )
        if not rows:
            logger.info(
                "[etl:quiver_insiders] fetch returned no rows "
                "(missing API key / circuit open / upstream 500)"
            )
            return []
        return list(rows)

    async def transform(
        self, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in rows:
            ticker = str(r.get("Ticker") or r.get("ticker") or "").upper().strip()
            filed_date = str(r.get("Date") or r.get("date") or "")[:10]
            name = str(r.get("Name") or r.get("name") or "").strip()
            tx_type = str(r.get("Transaction") or r.get("transaction") or "").strip()
            qty = str(r.get("Shares") or r.get("shares") or "").strip()

            if not (ticker and filed_date and name):
                continue

            out.append({
                "ticker": ticker,
                "filed_date": filed_date,
                "insider_name": name,
                "trade_type": tx_type,
                "qty": qty,
                # Payload
                "insider_title": str(r.get("Title") or r.get("title") or "").strip(),
                "price": str(r.get("Price") or r.get("price") or "").strip(),
                "quiver_id": r.get("id") or r.get("ID"),
            })
        return out


# ── Mongo-backed reader ────────────────────────────────────────────


async def get_insider_trades_cached(
    db: Any,
    ticker: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Drop-in replacement for ``quiver_service.get_insider_trades``
    that reads from the ETL cache. Returns the same canonical shape."""
    if db is None:
        return []
    coll = db["quiver_insiders"]
    filt: dict[str, Any] = {}
    if ticker:
        filt["ticker"] = ticker.upper().strip()

    cursor = (
        coll.find(filt, {"_id": 0})
        .sort("filed_date", -1)
        .limit(max(1, min(limit, 100)))
    )

    out: list[dict[str, Any]] = []
    async for row in cursor:
        name = row.get("insider_name") or ""
        title = row.get("insider_title") or ""
        tx_type = row.get("trade_type") or ""
        tkr = row.get("ticker") or ""
        qty = row.get("qty") or ""
        price = row.get("price") or ""
        out.append({
            "form_type": "Form 4",
            "filed_date": row.get("filed_date") or "",
            "ticker": tkr,
            "insider_name": name,
            "insider_title": title,
            "trade_type": tx_type,
            "price": price,
            "qty": qty,
            "description": (
                f"Insider {tx_type} by {name} ({title}) — "
                f"{tkr} {qty} shares @ ${price}"
            ),
            "source": "quiverquant_etl",
        })
    return out
