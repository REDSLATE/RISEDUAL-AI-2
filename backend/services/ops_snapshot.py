"""Operator snapshot — one-stop health/config readout for admins.

Adapted from the RISEDUAL CLI prototype's ``env_state`` module.
The original version was a synchronous, dataclass-based snapshot
returned by a CLI verb. Here we adapt it for an async FastAPI
admin endpoint that needs to:

  1. Always report a known list of operator flags (whether set or
     unset), so missing config is visible at a glance.
  2. Auto-surface anything else with a RISEDUAL-ish prefix —
     prevents silent drift when a new flag is added in code but
     forgotten by ops.
  3. Probe Mongo connectivity (real ping, not just config check).
  4. Report which third-party integrations have keys configured
     without ever returning the keys themselves.
  5. Pull Tier 3 progress from the existing readiness service so
     the operator doesn't have to bounce between endpoints.

Read-only, admin-only, never writes anywhere.
"""
from __future__ import annotations

__domain__ = "PRD"

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Known RISEDUAL operator flags — always reported, even when unset,
# so a missing value is loud instead of silent. Keep this list in
# sync with /app/memory/TIER3_ACTIVATION_PLAYBOOK.md.
KNOWN_FLAGS: tuple[str, ...] = (
    "CRYPTO_ADVERSARIAL_ENABLED",
    "CRYPTO_ADVERSARIAL_PHASE",
    "CRYPTO_RESEARCH_SHADOW_ENGINE",
    "COUNCIL_SHADOW_MODE",
    "COUNCIL_RISK_MODULATOR_ENABLED",
    "ML_ADAPTATION_SHADOW_MODE",
    "REGIME_WEIGHTS_ENABLED",
    "PUBLIC_DATA_FLOOR_DATE",
    "SHADOW_COST_CEILING_USD_PER_DAY",
)

# Auto-surface anything else with these prefixes (helps catch new
# operator flags before the KNOWN_FLAGS list is updated).
_PREFIXES: tuple[str, ...] = (
    "RISEDUAL_", "COUNCIL_", "CRYPTO_", "PUBLIC_DATA_",
    "STRATEGIST_", "AUDITOR_", "ADVERSARIAL_", "REGIME_",
    "ML_ADAPTATION_", "SHADOW_",
)

# Third-party integrations we care about. Map env-var name → display
# label. Endpoint reports configured-or-not; never the value itself.
_INTEGRATION_KEYS: dict[str, str] = {
    "MONGO_URL": "MongoDB",
    "STRIPE_API_KEY": "Stripe (subscriptions)",
    "USPTO_API_KEY": "USPTO Patent Watch",
    "TAVILY_API_KEY": "Tavily (web research)",
    "ALPACA_API_KEY": "Alpaca (equities/options)",
    "TRADIER_API_KEY": "Tradier (quotes/options)",
    "QUIVER_API_KEY": "QuiverQuant",
    "FINNHUB_API_KEY": "Finnhub (primary market data)",
    "POLYGON_API_KEY": "Polygon.io (A/B challenger)",
    "EMERGENT_LLM_KEY": "Emergent LLM (Claude/GPT/Gemini)",
}


def _collect_flags() -> tuple[dict[str, Optional[str]], dict[str, str]]:
    """Snapshot known flags + auto-detected prefixed flags.

    Returns (known, extras) where ``known`` always has every entry
    in :data:`KNOWN_FLAGS` (value or None) and ``extras`` has any
    other env var matching :data:`_PREFIXES` that isn't already
    in ``known``.
    """
    known = {name: os.environ.get(name) for name in KNOWN_FLAGS}
    extras: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.startswith(_PREFIXES) and k not in known:
            extras[k] = v
    return known, extras


def _collect_integrations() -> list[dict[str, Any]]:
    """One row per known integration with `configured: bool`. The
    actual key never leaves this function."""
    out = []
    for env_name, label in _INTEGRATION_KEYS.items():
        v = os.environ.get(env_name) or ""
        out.append({
            "name": label,
            "env_var": env_name,
            "configured": bool(v.strip()),
        })
    return out


async def _probe_mongo(db: Any, timeout_s: float = 1.5) -> dict[str, Any]:
    """Real connectivity probe — pings the cluster, doesn't just
    check for the URL string. Returns ``{ok, error?, ping_ms?}``."""
    if db is None:
        return {"ok": False, "error": "db_handle_missing"}
    try:
        start = time.perf_counter()
        # Motor exposes the underlying client via .client.
        client = getattr(db, "client", None)
        if client is None:
            return {"ok": False, "error": "no_client_handle"}
        await client.admin.command("ping")
        return {
            "ok": True,
            "ping_ms": round((time.perf_counter() - start) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:120]}


async def _collect_scheduler_heartbeat(db: Any) -> dict[str, Any]:
    """How long since the most recent scheduled job ran successfully.

    Cheap proxy: read the most recent ``research_shadow_decisions``
    or ``crypto_paper_trades`` ``opened_at`` timestamp. The
    scheduler runs both pipelines hourly-ish, so a stale value
    means something's wedged.
    """
    if db is None:
        return {"ok": False, "error": "db_unavailable"}
    try:
        latest = None
        for coll, key in (
            ("research_shadow_decisions", "logged_at"),
            ("crypto_paper_trades", "opened_at"),
            ("paper_trades", "opened_at"),
        ):
            doc = await db[coll].find_one(
                {key: {"$exists": True}},
                {"_id": 0, key: 1},
                sort=[(key, -1)],
            )
            if doc and doc.get(key):
                ts = doc[key]
                if isinstance(ts, datetime):
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if latest is None or ts > latest:
                        latest = ts
        if latest is None:
            return {"ok": False, "error": "no_scheduler_signals_found"}
        age_s = (datetime.now(timezone.utc) - latest).total_seconds()
        return {
            "ok": age_s < 24 * 3600,  # >24h stale = unhealthy
            "last_signal_at": latest.isoformat(),
            "age_seconds": int(age_s),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:120]}


async def _collect_tier3_progress(db: Any) -> dict[str, Any]:
    """Reuse the existing readiness service so we have one source
    of truth for Tier-3 / Council / Adversarial state."""
    if db is None:
        return {"ok": False, "error": "db_unavailable"}
    try:
        from services.research_shadow_stats import fetch_tier_readiness
        readiness = await fetch_tier_readiness(db)
        return {
            "ok": True,
            "tier3_progress_pct": readiness.get("tier3_progress_pct"),
            "tier3_unlocked": readiness.get("tier3_unlocked"),
            "adversarial_phase": readiness.get("adversarial_phase"),
            "council_modulator_enabled": readiness.get("council_modulator_enabled"),
            "ready_to_enable_council": readiness.get("ready_to_enable_council"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:120]}


def _runtime_summary() -> dict[str, Any]:
    """Bare-metal facts that occasionally answer 'why is it slow'."""
    import multiprocessing
    import sys
    return {
        "python_version": sys.version.split()[0],
        "cpu_cores": multiprocessing.cpu_count(),
        "service_started_at": _SERVICE_STARTED_AT_ISO,
        "uptime_seconds": int(time.time() - _SERVICE_STARTED_AT_EPOCH),
    }


# Captured once at module import — best proxy we have for "when
# did this backend container start" without parsing supervisor logs.
_SERVICE_STARTED_AT_EPOCH: float = time.time()
_SERVICE_STARTED_AT_ISO: str = datetime.fromtimestamp(
    _SERVICE_STARTED_AT_EPOCH, tz=timezone.utc,
).isoformat()


def _heuristic_notes(
    flags: dict[str, Optional[str]],
    integrations: list[dict[str, Any]],
    mongo: dict[str, Any],
    scheduler: dict[str, Any],
    tier3: dict[str, Any],
) -> list[str]:
    """Auto-generated single-sentence operator guidance for every
    readout. Adapted from the AuditReport.notes pattern in the
    RISEDUAL CLI prototype.
    """
    notes: list[str] = []

    # Connectivity — these are red-alert items.
    if not mongo.get("ok"):
        notes.append("Mongo ping failed — backend cannot persist trades or read state.")
    if not scheduler.get("ok"):
        if scheduler.get("error") == "no_scheduler_signals_found":
            notes.append("No scheduler activity detected yet; system may be newly deployed.")
        else:
            age_h = (scheduler.get("age_seconds") or 0) / 3600
            notes.append(f"Scheduler appears stalled (last signal {age_h:.1f}h ago).")

    # Configuration — non-fatal, but worth flagging.
    by_env = {row["env_var"]: row["configured"] for row in integrations}
    if not by_env.get("MONGO_URL"):
        notes.append("MONGO_URL not set in env (catastrophic if Mongo is up but unreachable).")
    if not by_env.get("EMERGENT_LLM_KEY"):
        notes.append("Emergent LLM key not configured — Council shadow / chat features will degrade.")
    if not by_env.get("STRIPE_API_KEY"):
        notes.append("Stripe not configured — subscription checkout will fail.")
    if not by_env.get("USPTO_API_KEY"):
        notes.append("USPTO_API_KEY not set — Patent Watch refreshes will short-circuit.")

    # Phase / state guidance.
    phase = (flags.get("CRYPTO_ADVERSARIAL_PHASE") or "").strip().lower()
    if flags.get("CRYPTO_ADVERSARIAL_ENABLED") and phase not in ("shadow", "risk_only", "veto", "full"):
        notes.append(
            f"CRYPTO_ADVERSARIAL_ENABLED is on but CRYPTO_ADVERSARIAL_PHASE='{phase}' is invalid; "
            "expected shadow|risk_only|veto|full."
        )
    if (
        tier3.get("tier3_unlocked") is False
        and (flags.get("COUNCIL_RISK_MODULATOR_ENABLED") or "").strip().lower() == "true"
    ):
        notes.append(
            "COUNCIL_RISK_MODULATOR_ENABLED=true but Tier 3 is locked; "
            "the modulator will sit inert until the gate opens."
        )

    if not notes:
        notes.append("All gauges nominal.")
    return notes


async def collect_ops_snapshot(db: Any) -> dict[str, Any]:
    """One call → everything an operator needs to assess instance
    health. Always succeeds; individual probes return their own
    error fields so the response shape is stable.
    """
    known, extras = _collect_flags()
    integrations = _collect_integrations()
    mongo = await _probe_mongo(db)
    scheduler = await _collect_scheduler_heartbeat(db)
    tier3 = await _collect_tier3_progress(db)
    runtime = _runtime_summary()
    notes = _heuristic_notes(known, integrations, mongo, scheduler, tier3)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime,
        "operator_flags": [
            {"name": name, "value": value, "set": value is not None}
            for name, value in known.items()
        ],
        "extra_flags": [
            {"name": name, "value": value} for name, value in sorted(extras.items())
        ],
        "integrations": integrations,
        "mongo": mongo,
        "scheduler": scheduler,
        "tier3": tier3,
        "notes": notes,
    }
