"""Crypto Strategist/Auditor audit log + veto-rate stats.

Why this exists
---------------
The Auditor's RSI≥70 / parabolic-momentum vetoes are appropriate
for paper testing but may be too strict for live crypto, which
frequently trends while "overbought" (a famous failure mode of
classical TA on crypto). To know whether the Auditor is calibrated
correctly we need a measurement layer — same philosophy as the
P2 ``provider_call_log`` proposal.

Target veto-rate band:
* **20–45%** — Auditor is filtering exhaustion setups without
  blocking the momentum continuation trades that make crypto bots
  profitable.
* **>70%** — too strict; loosen RSI floor or drop the parabolic
  veto.
* **<10%** — too lenient; Auditor isn't catching exhaustion.

What gets logged
----------------
Every call to :func:`services.crypto_strategist.adversarial_signal`
that comes through the bot path lands in ``crypto_signal_audit_log``
with:

* ``timestamp`` — when the decision was made
* ``symbol`` — crypto ticker
* ``strategist_direction`` — LONG/SHORT/HOLD
* ``strategist_confidence`` — 0.0–1.0
* ``auditor_verdict`` — CONFIRM / VETO / HOLD
* ``auditor_reason`` — full reason text (e.g. ``rsi_overbought_77.0``)
* ``combined_confidence`` — geometric mean
* ``final_direction`` — what the bot actually did
* ``rsi``, ``ema20``, ``momentum_5b`` — indicator snapshot

Stats endpoints (in ``routes/crypto_trading.py``):
* ``GET /api/crypto/strategist-stats`` — admin tile data:
  veto rate, confirm rate, hold rate over 24h / 7d / 30d windows,
  per-symbol breakdown, top veto reasons.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Canonical collection name. Kept inline (not env-tunable) because
# downstream readers (admin dashboard, this stats endpoint) hard-
# reference it; a config drift would silently break the dashboard
# without a clear error.
COLLECTION = "crypto_signal_audit_log"


async def log_adversarial_decision(
    db: Any,
    *,
    symbol: str,
    signal: dict,
    final_direction: str,
) -> None:
    """Persist one Strategist/Auditor decision.

    Failures are swallowed and logged at WARNING — the bot's main
    fill path must not die because the audit log had a transient
    write error.
    """
    if db is None:
        return
    try:
        proposal = signal.get("strategist") or {}
        audit = signal.get("auditor") or {}
        indicators = proposal.get("indicators") or audit.get("indicators") or {}

        record = {
            "timestamp": datetime.now(timezone.utc),
            "symbol": symbol.upper(),
            "strategist_direction": proposal.get("direction"),
            "strategist_confidence": proposal.get("confidence"),
            "strategist_reason": proposal.get("reason"),
            "auditor_verdict": audit.get("verdict"),
            "auditor_confidence": audit.get("confidence"),
            "auditor_reason": audit.get("reason"),
            "combined_confidence": signal.get("confidence"),
            "final_direction": final_direction,
            "indicators": {
                "rsi": indicators.get("rsi"),
                "ema20": indicators.get("ema20"),
                "momentum_5b": indicators.get("momentum_5b"),
            },
            # SHADOW ONLY — Tavily + LLM stance verdict captured for later
            # expectancy analysis. MUST NOT be used to alter trade direction
            # or confidence in the live path. Populated by
            # services.research_router + services.web_research_service.
            "web_research_shadow_verdict": signal.get(
                "web_research_shadow_verdict"
            ),
        }
        await db[COLLECTION].insert_one(record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto_audit_log] write failed: %s", exc)


async def ensure_indexes(db: Any) -> None:
    """Idempotent index creation. Called once at startup."""
    if db is None:
        return
    try:
        await db[COLLECTION].create_index(
            [("timestamp", -1)], name="ts_desc",
        )
        await db[COLLECTION].create_index(
            [("symbol", 1), ("timestamp", -1)],
            name="symbol_ts_desc",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto_audit_log] index creation failed: %s", exc)


# ── Stats aggregation ─────────────────────────────────────────────────────────


def _compute_summary_from_rows(rows: list[dict]) -> dict[str, Any]:
    """Pure function — exposed for unit testing without a Mongo handle."""
    total = len(rows)
    if total == 0:
        return {
            "total_decisions": 0,
            "proposals": {"long": 0, "short": 0, "hold": 0},
            "audit": {"confirm": 0, "veto": 0, "hold": 0},
            "veto_rate": 0.0,
            "confirm_rate": 0.0,
            "hold_rate": 0.0,
            "calibration": "no_data",
            "top_veto_reasons": [],
            "by_symbol": {},
        }

    proposals = Counter()
    verdicts = Counter()
    veto_reasons: Counter = Counter()
    by_symbol: dict[str, dict] = {}
    proposed_count = 0  # only counts where Strategist actually proposed

    for r in rows:
        sd = (r.get("strategist_direction") or "HOLD").upper()
        av = (r.get("auditor_verdict") or "HOLD").upper()
        sym = r.get("symbol") or "?"

        proposals[sd] += 1
        verdicts[av] += 1

        if sd in ("LONG", "SHORT"):
            proposed_count += 1
            slot = by_symbol.setdefault(sym, {
                "proposed": 0, "vetoed": 0, "confirmed": 0,
            })
            slot["proposed"] += 1
            if av == "VETO":
                slot["vetoed"] += 1
                reason = r.get("auditor_reason") or "unknown"
                # Canonicalise reasons: ``rsi_overbought_77.4`` →
                # ``rsi_overbought`` so the top-N count is meaningful.
                token = reason.split("_")[0:2]
                veto_reasons["_".join(token) if token else reason] += 1
            elif av == "CONFIRM":
                slot["confirmed"] += 1

    veto_rate = (verdicts["VETO"] / proposed_count * 100.0) if proposed_count else 0.0
    confirm_rate = (verdicts["CONFIRM"] / proposed_count * 100.0) if proposed_count else 0.0
    hold_rate = (verdicts["HOLD"] / total * 100.0) if total else 0.0

    if veto_rate == 0 and proposed_count == 0:
        calibration = "no_proposals"
    elif veto_rate < 10:
        calibration = "too_lenient"
    elif veto_rate <= 45:
        calibration = "in_target_band"
    elif veto_rate <= 70:
        calibration = "above_band"
    else:
        calibration = "too_strict"

    return {
        "total_decisions": total,
        "proposals_count": proposed_count,
        "proposals": {
            "long": proposals.get("LONG", 0),
            "short": proposals.get("SHORT", 0),
            "hold": proposals.get("HOLD", 0),
        },
        "audit": {
            "confirm": verdicts.get("CONFIRM", 0),
            "veto": verdicts.get("VETO", 0),
            "hold": verdicts.get("HOLD", 0),
        },
        "veto_rate": round(veto_rate, 2),
        "confirm_rate": round(confirm_rate, 2),
        "hold_rate": round(hold_rate, 2),
        "calibration": calibration,
        "target_band": "20-45%",
        "top_veto_reasons": [
            {"reason": k, "count": v}
            for k, v in veto_reasons.most_common(5)
        ],
        "by_symbol": by_symbol,
    }


async def get_strategist_stats(
    db: Any,
    *,
    hours: int = 24,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    """Compute veto / confirm / hold rates over a rolling window.

    Parameters
    ----------
    db
        Motor handle.
    hours
        Window size. Common: 24 (daily), 168 (7d), 720 (30d).
    symbol
        Optional filter to a single ticker.
    """
    if db is None:
        return _compute_summary_from_rows([])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query: dict[str, Any] = {"timestamp": {"$gte": cutoff}}
    if symbol:
        query["symbol"] = symbol.upper()

    rows = await db[COLLECTION].find(query, {"_id": 0}).to_list(length=100_000)
    summary = _compute_summary_from_rows(rows)
    summary["window_hours"] = hours
    if symbol:
        summary["filter_symbol"] = symbol.upper()
    return summary
