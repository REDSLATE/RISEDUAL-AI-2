"""Rejection taxonomy — separates *protection* (Alpha correctly
refusing a trade) from *infrastructure* (plumbing failed and dropped
a legitimate intent) from *session* (market state, not a rejection).

Why this matters
----------------
The operator's principle: mixing protection and infrastructure in
one bucket makes both invisible. When "17 market_closed + 19
chasing_filter + 8 place_order_exception" all show up as "27
rejected," the operator can't tell whether Alpha is:

* healthy but discerning (protection dominates — leave it alone),
* being starved of trades by a broken broker adapter (infrastructure
  dominates — fix immediately), or
* just off-hours (session dominates — do nothing).

Turning Alpha loose safely requires seeing those three signals
separately. Loosening protection when the real problem is
infrastructure would recreate the late-entry / bad-entry regression.

Classification table
--------------------
* ``protection``      — Alpha's own guards. NEVER auto-fix; retuning
                        needs deliberate operator sign-off.
* ``infrastructure``  — Alpha wanted to trade but plumbing failed. If
                        this rises we investigate the adapter, quote
                        provider, or watchdog state.
* ``session``         — Market/config state; neither good nor bad.
* ``concurrency``     — Alpha's own lock protocol; rare and expected
                        under multi-scanner load.
* ``unknown``         — Reason code not in the table. Any new reason
                        code shows up here so the taxonomy stays
                        loudly incomplete rather than silently
                        misclassifying.
"""
from __future__ import annotations


# Explicit lists so an unknown reason falls to "unknown" and forces
# taxonomy maintenance rather than silently getting bucketed.

_PROTECTION: frozenset[str] = frozenset({
    # Setup / entry guards (Alpha correctly refusing a bad entry)
    "chasing_filter",
    "luld_roadguard",
    "symbol_cooldown",
    "confidence_floor",
    "not_in_allowlist",
    "non_directional",
    "empty_symbol",
    "insufficient_buying_power",
    "short_signal_only",
    "hw_kill_switch_tripped",
    # Position-state guards (correct refusal to double-book)
    "dup_open_row",
    "sell_no_position",
    "broker_watchdog_frozen",
    # Discernment / risk guards
    "qty_zero",
    "danger_pause_mode",
    "invalidated",
    "invalidated_before_trigger",
    "execute_switch_off",
})

_INFRASTRUCTURE: frozenset[str] = frozenset({
    # Market-data / quote failures
    "execution_quote_blocked",
    "no_broker_price",
    "broker_degraded",
    "no_mark_price",
    # Broker adapter failures
    "no_broker_creds",
    "client_init_failed",
    "place_order_exception",
    "broker_empty_response",
    "moomoo_not_execution_ready",
    "moomoo_health_probe_failed",
    # Fail-visible invariant sweep
    "orphaned:no_terminal_event",
})

_SESSION: frozenset[str] = frozenset({
    "market_closed",
    "live_exec_disabled",
})

_CONCURRENCY: frozenset[str] = frozenset({
    "exec_lock_conflict",
    "intent_deduplicated",
})


def classify(reason: str) -> str:
    """Return one of ``protection`` | ``infrastructure`` | ``session``
    | ``concurrency`` | ``unknown``.
    """
    if not reason:
        return "unknown"
    r = reason.strip().lower()
    # Prefix match for orphaned:*.
    if r.startswith("orphaned"):
        return "infrastructure"
    if r in _PROTECTION:
        return "protection"
    if r in _INFRASTRUCTURE:
        return "infrastructure"
    if r in _SESSION:
        return "session"
    if r in _CONCURRENCY:
        return "concurrency"
    return "unknown"


def summarize(reason_counts: dict[str, int]) -> dict[str, dict]:
    """Fold a ``{reason: count}`` map into class-level buckets.

    Returns a shape like::

        {
          "protection":     {"total": 36, "reasons": {"chasing_filter": 19, ...}},
          "infrastructure": {"total":  7, "reasons": {"execution_quote_blocked": 7, ...}},
          "session":        {"total": 17, "reasons": {"market_closed": 17}},
          "concurrency":    {"total":  2, "reasons": {"exec_lock_conflict": 2}},
          "unknown":        {"total":  0, "reasons": {}},
        }
    """
    out: dict[str, dict] = {
        "protection":     {"total": 0, "reasons": {}},
        "infrastructure": {"total": 0, "reasons": {}},
        "session":        {"total": 0, "reasons": {}},
        "concurrency":    {"total": 0, "reasons": {}},
        "unknown":        {"total": 0, "reasons": {}},
    }
    for reason, count in reason_counts.items():
        klass = classify(reason)
        try:
            n = int(count)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            continue
        out[klass]["total"] += n
        out[klass]["reasons"][reason] = n
    return out


def health_hint(taxonomy: dict[str, dict]) -> dict[str, str]:
    """Interpret the taxonomy summary into a one-sentence hint the
    dashboard can show above the tile. Not a diagnosis — just points
    the operator at the dominant class.
    """
    infra = taxonomy.get("infrastructure", {}).get("total", 0)
    prot = taxonomy.get("protection", {}).get("total", 0)
    sess = taxonomy.get("session", {}).get("total", 0)
    unk = taxonomy.get("unknown", {}).get("total", 0)

    if infra == 0 and prot == 0 and sess == 0 and unk == 0:
        return {"tone": "neutral", "message": "No rejections in window."}

    # Infrastructure never dominates in a healthy system.
    if infra > 0 and infra >= max(prot, sess, unk):
        return {
            "tone": "alert",
            "message": (
                f"Infrastructure failures leading rejections ({infra}). "
                "Investigate broker adapter / quote provider before "
                "retuning Alpha's guards."
            ),
        }
    if prot > 0 and prot >= max(infra, sess, unk):
        return {
            "tone": "ok",
            "message": (
                f"Protection guards dominate ({prot}). Alpha is refusing "
                "trades on purpose — verify these are correct refusals "
                "before loosening any thresholds."
            ),
        }
    if sess > 0 and sess >= max(prot, infra, unk):
        return {
            "tone": "neutral",
            "message": (
                f"Session state dominates ({sess}). Market is closed or "
                "live exec is off — no rejections require action."
            ),
        }
    if unk > 0:
        return {
            "tone": "warn",
            "message": (
                f"Unclassified rejections ({unk}). Update rejection "
                "taxonomy in services/alpha_rejection_taxonomy.py."
            ),
        }
    return {"tone": "neutral", "message": "Mixed rejection signal."}


__all__ = ["classify", "summarize", "health_hint"]
