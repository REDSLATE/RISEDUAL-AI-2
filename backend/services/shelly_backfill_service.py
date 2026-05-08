"""Shelly backfill service — Lever 1.

Walks ``paper_trades`` and ``crypto_paper_trades`` collections,
produces a chronologically-ordered stream of denormalised closed-
trade dicts ready for ``paper_trade_to_memory``, and reports
aggregate statistics so the operator can sanity-check before
allowing a write-mode pass.

Design choices (operator-approved spec)
---------------------------------------
* Reads BOTH ``paper_trades`` and ``crypto_paper_trades``.
* Only resolved trades are eligible.
* Sort chronologically by entry timestamp.
* Reuses the existing live-ingest adapter (``paper_trade_to_memory``).
* Trust tier is REPORTED in aggregate, not stamped on the memory —
  the canonical ``RegimeMemory`` schema is intentionally untouched
  (operator's prior rule: don't touch the 596-line tested engine).
* ``source`` and ``macro_proxy`` are also REPORTED in aggregate
  for the same reason. Per-memory stamps would require a schema
  change; deferred until persistence proves clean.

paper_trades pairing
--------------------
``paper_trades`` stores individual fills (BUY / SELL rows), not
denormalised round-trips. We FIFO-match BUY rows against SELL
rows per ``(user_id, symbol)`` to reconstruct closed positions.
The match is per-share/per-unit so multi-BUY accumulators
correctly produce multiple memories (one per BUY → SELL leg).

This pairing is approximate — partial-fill SELLs that get split
across multiple later SELLs aren't reassembled, and a BUY left
unmatched at the end of the window simply doesn't produce a
memory. Both behaviours are acceptable for an ingest signal:
Shelly clusters by regime fingerprint, not by exact P&L precision.

``crypto_paper_trades`` is already one-row-per-round-trip so it
flows through unchanged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from services.auto_regime_tagger import RawMacroData
from services.shelly_ingest_adapter import paper_trade_to_memory


logger = logging.getLogger(__name__)


# Hard caps so a misconfigured ``since_days=365`` doesn't load
# 100k docs into memory. Operator can raise these later if the
# real volume warrants it.
MAX_PAPER_ROWS = 20_000
MAX_CRYPTO_ROWS = 20_000


# ─── pure helpers (tested independently of Mongo) ──────────────


def _fifo_pair_buy_sells(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """FIFO-match BUY/SELL rows from ``paper_trades`` into
    denormalised closed-trade dicts.

    Input rows must be sorted chronologically by ``opened_at``.

    Returns a list of synthetic trade dicts shaped for
    ``paper_trade_to_memory``. Unmatched BUYs at the end of the
    window are dropped (they're still open positions).
    """
    open_buys: dict[tuple, list[dict[str, Any]]] = {}
    synthetics: list[dict[str, Any]] = []

    for row in rows:
        side = row.get("side")
        if side not in ("BUY", "SELL"):
            continue

        user_id = row.get("user_id")
        symbol = row.get("symbol")
        if not symbol:
            continue
        try:
            qty = float(row.get("qty") or row.get("quantity") or 0.0)
            price = float(row.get("price") or 0.0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        opened_at = row.get("opened_at") or row.get("timestamp")
        if not isinstance(opened_at, datetime):
            continue

        key = (user_id, symbol)

        if side == "BUY":
            open_buys.setdefault(key, []).append({
                "qty_remaining": qty,
                "price": price,
                "opened_at": opened_at,
            })
            continue

        # SELL — pop oldest BUYs FIFO until consumed.
        sell_remaining = qty
        queue = open_buys.get(key)
        if not queue:
            # Naked SELL with no preceding BUY in window — skip.
            continue
        while sell_remaining > 1e-9 and queue:
            buy = queue[0]
            matched = min(sell_remaining, buy["qty_remaining"])
            synthetics.append({
                "trade_id": (
                    f"backfill-{user_id}-{symbol}-"
                    f"{buy['opened_at'].isoformat()}-"
                    f"{opened_at.isoformat()}"
                ),
                "symbol": symbol,
                "direction": "LONG",
                "entry_price": buy["price"],
                "exit_price": price,
                "opened_at": buy["opened_at"],
                "closed_at": opened_at,
            })
            buy["qty_remaining"] -= matched
            sell_remaining -= matched
            if buy["qty_remaining"] <= 1e-9:
                queue.pop(0)

    return synthetics


def _crypto_row_to_trade_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Pass-through for already-denormalised closed crypto trades.
    Just normalises field names so ``paper_trade_to_memory`` sees
    what it expects."""
    return {
        "trade_id": row.get("trade_id") or row.get("memory_id"),
        "symbol": row.get("symbol"),
        "direction": row.get("direction") or "LONG",
        "entry_price": row.get("entry_price"),
        "exit_price": row.get("exit_price"),
        "opened_at": row.get("opened_at"),
        "closed_at": row.get("closed_at"),
        "pnl_pct": row.get("pnl_pct") or row.get("pnl"),
    }


def categorise_pnl(pnl_pct: float, toxic_threshold_pct: float) -> dict[str, bool]:
    """Pure label classifier for the aggregate report.

    Returns flags rather than a single label so the caller can
    increment multiple counters in one pass without re-checking
    the value.
    """
    return {
        "win": pnl_pct > 0.5,
        "loss": pnl_pct < -0.5,
        "breakeven": abs(pnl_pct) <= 0.5,
        "toxic": pnl_pct < toxic_threshold_pct,
    }


# ─── DB walkers (separated so the pure helpers stay testable) ──


async def load_paper_trade_synthetics(
    db: Any,
    since: datetime,
) -> list[dict[str, Any]]:
    """Pull BUY/SELL rows from ``paper_trades`` and FIFO-pair them."""
    rows = await db.paper_trades.find(
        {
            "opened_at": {"$gte": since},
            "side": {"$in": ["BUY", "SELL"]},
        },
        {"_id": 0},
    ).sort("opened_at", 1).to_list(length=MAX_PAPER_ROWS)

    if len(rows) >= MAX_PAPER_ROWS:
        logger.warning(
            "shelly_backfill: paper_trades hit MAX_PAPER_ROWS=%d cap; "
            "older rows truncated", MAX_PAPER_ROWS,
        )
    return _fifo_pair_buy_sells(rows)


async def load_crypto_trade_synthetics(
    db: Any,
    since: datetime,
) -> list[dict[str, Any]]:
    """Pull closed crypto rows (already denormalised)."""
    rows = await db.crypto_paper_trades.find(
        {
            "status": "closed",
            "opened_at": {"$gte": since},
        },
        {"_id": 0},
    ).sort("opened_at", 1).to_list(length=MAX_CRYPTO_ROWS)

    if len(rows) >= MAX_CRYPTO_ROWS:
        logger.warning(
            "shelly_backfill: crypto_paper_trades hit "
            "MAX_CRYPTO_ROWS=%d cap; older rows truncated",
            MAX_CRYPTO_ROWS,
        )
    return [_crypto_row_to_trade_dict(r) for r in rows]


# ─── orchestrator (the endpoint just calls this) ───────────────


async def run_backfill(
    db: Any,
    since_days: int,
    dry_run: bool,
    macro: RawMacroData,
    toxic_threshold_pct: float = -5.0,
    trust_tier: float = 0.25,
) -> dict[str, Any]:
    """End-to-end backfill orchestrator.

    * Loads paper + crypto synthetic trade dicts.
    * Tags each with its ``source``.
    * Merge-sorts chronologically by ``opened_at``.
    * Builds memories via the live adapter.
    * Aggregates counts.
    * If ``dry_run=False``, ingests via ``add_and_persist_memory``.

    Returns the operator-facing report dict.
    """
    since = datetime.now(timezone.utc) - timedelta(days=since_days)

    # Load (DB) — done before stat tracking so we can report
    # accurately on what reached us.
    paper_synthetics = await load_paper_trade_synthetics(db, since)
    crypto_synthetics = await load_crypto_trade_synthetics(db, since)

    # Tag with source then merge-sort.
    tagged: list[tuple[datetime, str, dict[str, Any]]] = []
    for t in paper_synthetics:
        tagged.append((
            t["opened_at"], "paper_trade", t,
        ))
    for t in crypto_synthetics:
        oa = t.get("opened_at")
        if not isinstance(oa, datetime):
            # Skip malformed rows so the chronological sort is safe.
            continue
        tagged.append((oa, "crypto_paper_trade", t))
    tagged.sort(key=lambda x: x[0])

    # Stats
    eligible = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}
    by_source: dict[str, int] = {"paper_trade": 0, "crypto_paper_trade": 0}
    by_label: dict[str, int] = {"win": 0, "loss": 0, "breakeven": 0}
    by_direction: dict[str, int] = {"LONG": 0, "SHORT": 0}
    toxic = 0
    unique_fingerprints: set[tuple] = set()

    # Snapshot cluster count for delta reporting.
    cluster_count_before = _safe_cluster_count()

    ingest_failures = 0
    canonical_rejected = 0

    for _, source, trade in tagged:
        memory = paper_trade_to_memory(trade, macro)
        if memory is None:
            skipped += 1
            skip_reasons["not_eligible"] = (
                skip_reasons.get("not_eligible", 0) + 1
            )
            continue

        eligible += 1
        by_source[source] = by_source.get(source, 0) + 1
        by_direction[memory.direction] = (
            by_direction.get(memory.direction, 0) + 1
        )

        cats = categorise_pnl(memory.pnl_pct, toxic_threshold_pct)
        for k, present in cats.items():
            if k == "toxic" and present:
                toxic += 1
            elif k != "toxic" and present:
                by_label[k] = by_label.get(k, 0) + 1

        fp = memory.regime_at_entry
        unique_fingerprints.add((
            fp.vix_level, fp.yield_curve, fp.dxy_trend,
            fp.credit_spreads, fp.liquidity, fp.macro_phase,
        ))

        if not dry_run:
            try:
                # ``add_and_persist_memory`` respects
                # ``LEARNING_CORE_PERSISTENCE_ENABLED`` — when off
                # (which is the operator's current posture) the
                # memory goes into Shelly's in-memory engine only.
                # The canonical engine itself respects
                # ``REGIME_MEMORY_ENABLED`` — when off, ingest is
                # rejected and ``regime_cluster_id`` comes back
                # ``None``. We surface that as a distinct counter
                # so the operator can spot the gate state from
                # the report.
                from services.learning_core_service import (
                    add_and_persist_memory,
                )
                result = await add_and_persist_memory(db, memory)
                if not result.get("regime_cluster_id"):
                    canonical_rejected += 1
            except Exception as exc:  # noqa: BLE001
                ingest_failures += 1
                logger.warning(
                    "shelly_backfill: ingest failed memory_id=%s err=%s",
                    memory.memory_id, exc,
                )

    cluster_count_after = _safe_cluster_count()

    return {
        "dry_run": dry_run,
        "since_days": since_days,
        "scanned_synthetic_trades": len(tagged),
        "eligible": eligible,
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "by_source": by_source,
        "by_label": by_label,
        "by_direction": by_direction,
        "toxic_count": toxic,
        "toxic_threshold_pct": toxic_threshold_pct,
        "trust_tier": trust_tier,
        "macro_proxy": "current",
        "ingest_failures": ingest_failures,
        "canonical_engine_rejected": canonical_rejected,
        # ``approximate_regime_clusters`` is the count of distinct
        # regime fingerprints among eligible memories — the upper
        # bound on cluster count since each fingerprint maps to at
        # most one cluster. In dry-run this is the only honest
        # cluster signal (no actual ingest happened).
        "approximate_regime_clusters": len(unique_fingerprints),
        "regime_clusters_before": cluster_count_before,
        "regime_clusters_after": cluster_count_after,
        "regime_clusters_delta": cluster_count_after - cluster_count_before,
        "notes": _build_notes(dry_run),
    }


def _safe_cluster_count() -> int:
    """Read the live cluster count from the singleton. Returns 0
    on any failure so the report always renders."""
    try:
        from services.learning_core_service import get_core
        report = get_core().regime_memory.get_cluster_report()
        return int(report.get("total_regime_clusters", 0))
    except Exception:
        return 0


def _build_notes(dry_run: bool) -> Iterable[str]:
    notes = [
        "Macro at entry is approximated using current macro snapshot.",
        "paper_trades pairing is FIFO; partial-fill SELLs split "
        "across multiple later SELLs are not reassembled.",
        "Trust tier and source labels are REPORTED in aggregate; "
        "the canonical RegimeMemory schema is intentionally untouched.",
    ]
    if dry_run:
        notes.append(
            "Dry-run mode — no memories were ingested. Re-run with "
            "dry_run=false to write to Shelly's in-memory bank.",
        )
    else:
        notes.append(
            "Write mode — memories landed in the in-memory engine. "
            "LEARNING_CORE_PERSISTENCE_ENABLED is OFF so they will "
            "NOT survive a backend restart.",
        )
    return list(notes)
