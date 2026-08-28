"""Why-Not-Trade diagnostic aggregator.

The operator's number-one question when Alpha stands down is
"which gate killed the candidate?". The tick loop already writes
one lifecycle row per gate decision to the SQLite hot store and
one outcome row per resolved setup to Mongo — this module joins
them into a single per-gate rollup so the answer is one HTTP GET
away.

Design notes
------------
* Reads are TIME-BOUNDED and CAPPED. A runaway operator asking
  for a 24-hour window can't force the hot store to load 500k
  rows into memory — the SQLite side has a hard limit, and the
  Mongo side is filtered to ``submitted=False`` rows only.
* We do NOT hit the trade path. Everything here is post-hoc
  observability against append-only stores.
* Sub-reason extraction is best-effort — every gate writes a
  ``reason`` string in its payload where feasible. Missing
  reasons roll up into ``"unspecified"`` so the operator can see
  the gap and we can wire more reasons later.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
import logging

logger = logging.getLogger(__name__)


# ─── gate registry ──────────────────────────────────────────────
#
# Human-readable name + the raw ``event`` string it writes to the
# hot store. Adding a new gate: append a row here and the endpoint
# picks it up automatically.
#
# ``description`` is surfaced to the UI so an operator scanning the
# rollup understands what the gate does without reading the code.
REJECTION_GATES: dict[str, dict[str, str]] = {
    "opportunity_score_rejected": {
        "description": "Candidate opportunity score fell below the "
                       "minimum floor — never reached the pattern engine.",
    },
    "wave_danger_pause": {
        "description": "Per-symbol Wave Intelligence layer flagged "
                       "DANGER_PAUSE (volatility expansion / price "
                       "shock / wide spread) and vetoed the candidate.",
    },
    "no_pattern_match": {
        "description": "Candidate cleared opportunity + wave gates "
                       "but the pattern engine found no matching "
                       "setup shape (sensitivity too tight, or "
                       "features don't fit any of the ~15 patterns).",
    },
    "invalidated": {
        "description": "Setup shape broke before it could trigger "
                       "(e.g. price crossed the invalidation level).",
    },
    "intent_deduplicated": {
        "description": "Same economic intent already in-flight — "
                       "fingerprint hash matched an existing ticket.",
    },
    "exec_lock_conflict": {
        "description": "Another scanner already holds a cross-scanner "
                       "lock on this symbol; skipping to avoid double-buy.",
    },
    "execution_quote_blocked": {
        "description": "Broker-first execution gate refused the trade "
                       "(stale broker quote, broker/vendor drift > 50 bps, "
                       "or no broker price for this symbol).",
    },
    "executor_rejected": {
        "description": "Downstream executor (broker path) refused the "
                       "order — usually risk / sizing / venue-side.",
    },
}


def _sub_reason_from(row: dict) -> str:
    """Pull the ``reason`` sub-string from a lifecycle payload.

    Every gate we care about writes some form of textual reason.
    We normalize to the leading token (before whitespace) so
    ``broker_quote_stale (age=8s > 5s)`` and ``broker_quote_stale
    (age=12s > 5s)`` roll up together.
    """
    payload = row.get("payload") or {}
    if not isinstance(payload, dict):
        return "unspecified"
    for key in ("reason", "reject_reason", "sub_reason"):
        val = payload.get(key)
        if val:
            return str(val).split(" ", 1)[0].strip() or "unspecified"
    return "unspecified"


async def compile_why_not_trade(
    db: Any,
    *,
    since_seconds: int = 300,
    sample_per_gate: int = 3,
    hot_store_limit: int = 5000,
) -> dict:
    """Build the ``/why-not-trade`` payload.

    Args:
        db: Mongo handle. ``None`` is tolerated — outcome rollup
            just returns empty.
        since_seconds: Look-back window. Clamped [60, 86400] so
            an operator can't burn the box asking for a year.
        sample_per_gate: How many example rows to include per gate.
            Kept small so the response is UI-friendly.
        hot_store_limit: Hard cap on lifecycle rows read from
            SQLite. Same defence.

    Returns a JSON-serializable dict with:
        ``since_seconds``, ``window_start_iso``, ``rejections`` (per-gate
        rollup), ``confirmed_submissions``, ``summary`` (human
        one-liner), ``totals`` (bird's-eye counters).
    """
    since_seconds = max(60, min(86_400, int(since_seconds)))
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=since_seconds)
    window_start_ns = int(window_start.timestamp() * 1_000_000_000)

    # ── hot-store side (per-gate rejection observations)
    from services import alpha_hot_store  # noqa: PLC0415
    gate_events = list(REJECTION_GATES.keys())
    # ``execution_quote_confirmed`` is a *pass* event — pull it too
    # so the endpoint can report how many candidates DID make it
    # through the execution gate (helps the operator distinguish
    # "no candidates arrived" from "all candidates blocked").
    read_events = gate_events + [
        "execution_quote_confirmed",
        "broker_submitted",
        "setup_detected",
        "triggered",
        "intent_created",
    ]
    rows = alpha_hot_store.events_since(
        window_start_ns, events=read_events, limit=hot_store_limit,
    )

    rejections: dict[str, dict] = {}
    for gate in gate_events:
        rejections[gate] = {
            "description": REJECTION_GATES[gate]["description"],
            "count": 0,
            "symbols": set(),
            "sub_reasons": Counter(),
            "sample": [],
        }
    confirmed_submissions = 0
    execution_quote_confirmed = 0
    setups_detected = 0
    triggers_fired = 0
    intents_created = 0

    for row in rows:
        event = row.get("event")
        if event in rejections:
            slot = rejections[event]
            slot["count"] += 1
            sym = (row.get("symbol") or "").upper()
            if sym:
                slot["symbols"].add(sym)
            slot["sub_reasons"][_sub_reason_from(row)] += 1
            if len(slot["sample"]) < sample_per_gate:
                slot["sample"].append({
                    "symbol": sym or None,
                    "setup_id": row.get("setup_id"),
                    "ts": row.get("ts"),
                    "stage": row.get("stage"),
                    "reason": _sub_reason_from(row),
                    "payload": row.get("payload"),
                })
        elif event == "broker_submitted":
            confirmed_submissions += 1
        elif event == "execution_quote_confirmed":
            execution_quote_confirmed += 1
        elif event == "setup_detected":
            setups_detected += 1
        elif event == "triggered":
            triggers_fired += 1
        elif event == "intent_created":
            intents_created += 1

    # Finalize (sets → sorted lists, Counters → dicts)
    for slot in rejections.values():
        slot["symbols"] = sorted(slot.pop("symbols"))
        slot["sub_reasons"] = dict(slot["sub_reasons"].most_common())

    # ── Mongo side (persistent outcome rollup for the window)
    outcome_rollup: dict[str, int] = {}
    outcome_sample: list[dict] = []
    if db is not None:
        try:
            cur = db.alpha_outcomes.find(
                {
                    "created_at": {"$gte": window_start},
                    "order_submitted": False,
                    "reject_reason": {"$exists": True, "$ne": None},
                },
                {"_id": 0, "symbol": 1, "reject_reason": 1, "setup_type": 1,
                 "created_at": 1, "setup_id": 1},
            ).sort("created_at", -1).limit(500)
            async for doc in cur:
                reason = str(doc.get("reject_reason") or "unspecified")
                reason_key = reason.split(" ", 1)[0].strip() or "unspecified"
                outcome_rollup[reason_key] = outcome_rollup.get(reason_key, 0) + 1
                if len(outcome_sample) < 20:
                    ts = doc.get("created_at")
                    outcome_sample.append({
                        "symbol": doc.get("symbol"),
                        "setup_id": doc.get("setup_id"),
                        "setup_type": doc.get("setup_type"),
                        "reject_reason": reason,
                        "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("[why_not_trade] outcome rollup failed: %s", exc)

    # ── summary line
    total_rejections = sum(slot["count"] for slot in rejections.values())
    if total_rejections == 0 and confirmed_submissions == 0 and setups_detected == 0:
        summary = (
            f"No Alpha activity in the last {since_seconds}s "
            f"(no setups detected, no rejections, no submissions). "
            f"Check runtime state / market hours."
        )
    else:
        # pick the worst-offending gate for the headline
        worst = max(rejections.items(), key=lambda kv: kv[1]["count"], default=(None, None))
        worst_name, worst_slot = worst
        if worst_slot and worst_slot["count"] > 0:
            top_sub = next(iter(worst_slot["sub_reasons"].items()), ("unspecified", 0))
            summary = (
                f"{setups_detected} setups detected → {triggers_fired} triggered → "
                f"{intents_created} intents → {execution_quote_confirmed} broker-confirmed → "
                f"{confirmed_submissions} submitted. "
                f"Top blocker: {worst_name} ({worst_slot['count']}× — "
                f"mostly {top_sub[0]})."
            )
        else:
            summary = (
                f"{setups_detected} setups detected → {triggers_fired} triggered → "
                f"{intents_created} intents → {execution_quote_confirmed} broker-confirmed → "
                f"{confirmed_submissions} submitted. No gate rejections in-window."
            )

    return {
        "since_seconds": since_seconds,
        "window_start_iso": window_start.isoformat(),
        "totals": {
            "setups_detected": setups_detected,
            "triggers_fired": triggers_fired,
            "intents_created": intents_created,
            "execution_quote_confirmed": execution_quote_confirmed,
            "broker_submitted": confirmed_submissions,
            "total_rejections": total_rejections,
            "hot_store_rows_scanned": len(rows),
        },
        "rejections": rejections,
        "outcome_rollup_mongo": {
            "counts": outcome_rollup,
            "sample": outcome_sample,
        },
        "summary": summary,
    }


__all__ = ["compile_why_not_trade", "REJECTION_GATES"]
