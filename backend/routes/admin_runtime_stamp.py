"""Admin route — Runtime stamp diagnostic (2026-02-27).

Read-only owner endpoint surfacing the EXACT runtime state the
running pod is reporting:
- ``RuntimeStamp.current()`` — the dict our sidecar would POST to
  MC's ``/api/admin/runtime/sidecar-checkin/alpha``.
- Operator trading gate state (blocked vs open + the env flag
  source).
- ``RISEDUAL_EMIT_INTENTS_TO_MC`` flag value.
- ``MC_KEYS_PROXY_ENABLED`` flag + whether keys were applied on
  boot.

Purpose: eliminate "what does the running pod actually believe
its config is" as a guessing game after a redeploy. One curl from
a phone tells you whether the env vars reached the Python process
without needing MC's diagnose endpoint or a deploy-panel re-check.

Doctrine pins
-------------
* **Owner-only.** Surfaces env values that are sensitive enough to
  require role-gated access; no broader auth.
* **Read-only.** No mutations, no writes, no side effects. Safe
  to poll arbitrarily.
* **Stamp is masked.** Token values are NEVER returned — only
  presence flags.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin/runtime",
    tags=["admin-runtime"],
)

db: Any = None


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("false", "0", "no", "off")


@router.get("/stamp")
async def get_runtime_stamp(request: Request) -> dict[str, Any]:
    """Return the current runtime stamp + relevant gate flags.

    Stamps the SAME values the periodic sidecar check-in would POST
    to MC, plus the operator-trading-gate state. Use this after a
    redeploy to confirm new env vars actually reached the process.
    """
    await _require_owner(request)

    # ── RuntimeStamp ─────────────────────────────────────────────
    try:
        from services.mc_checkin import RuntimeStamp
        stamp = RuntimeStamp.current()
        stamp_dict = asdict(stamp)
        # Mask pip fingerprint — large, irrelevant for "what env am I?"
        stamp_dict["pip_fingerprint"] = {
            "package_count": len(stamp_dict.get("pip_fingerprint") or {}),
            "_truncated": True,
        }
    except Exception as exc:  # noqa: BLE001
        stamp_dict = {"error": f"stamp build failed: {exc}"}

    # ── Operator trading gate ────────────────────────────────────
    gate_blocked = _bool_env("RISEDUAL_LOCAL_TRADES_BLOCKED", default=True)
    gate_legacy = _bool_env("OPERATOR_TRADING_AUTHORIZATION_ENABLED", default=False)
    gate_state = {
        "blocked": gate_blocked,
        "env": {
            "RISEDUAL_LOCAL_TRADES_BLOCKED": os.environ.get(
                "RISEDUAL_LOCAL_TRADES_BLOCKED", "(unset)",
            ),
            "OPERATOR_TRADING_AUTHORIZATION_ENABLED": os.environ.get(
                "OPERATOR_TRADING_AUTHORIZATION_ENABLED", "(unset)",
            ),
        },
        "reason": (
            "RISEDUAL_LOCAL_TRADES_BLOCKED=true (master switch)"
            if gate_blocked
            else (
                "OPERATOR_TRADING_AUTHORIZATION_ENABLED=true (legacy back-compat)"
                if gate_legacy
                else "open — intents emit to MC"
            )
        ),
    }

    # ── Emit-to-MC + MC keys proxy ───────────────────────────────
    emit_intents = os.environ.get("RISEDUAL_EMIT_INTENTS_TO_MC", "(unset)")
    keys_proxy_enabled = _bool_env("MC_KEYS_PROXY_ENABLED", default=True)
    polygon_present = bool((os.environ.get("POLYGON_API_KEY") or "").strip())
    finnhub_present = bool((os.environ.get("FINNHUB_API_KEY") or "").strip())

    # ── Token presence (NEVER return token values) ───────────────
    token_present = {
        "ALPHA_INGEST_TOKEN": bool(os.environ.get("ALPHA_INGEST_TOKEN")),
        "ALPHA_MC_INGEST_TOKEN": bool(os.environ.get("ALPHA_MC_INGEST_TOKEN")),
    }

    # ── Validator self-check ─────────────────────────────────────
    # Re-run MC's prod validator locally so the operator can see WHY
    # the stamp would be rejected without round-tripping through MC.
    try:
        from shared.runtime.platform_survival import RuntimeStamp as _SurvivalStamp
        survival_stamp = _SurvivalStamp.current()
        verdict = survival_stamp.validate_for_prod_sidecar()
        validator_view = {
            "ok": bool(verdict.get("ok")),
            "errors": list(verdict.get("errors") or []),
            "env_name": survival_stamp.env_name,
            "db_name": survival_stamp.db_name,
            "mc_url": survival_stamp.mc_url,
            "broker_mode": survival_stamp.broker_mode,
            "git_sha": survival_stamp.git_sha,
        }
    except Exception as exc:  # noqa: BLE001
        validator_view = {"error": f"validator unavailable: {exc}"}

    return {
        "runtime_stamp": stamp_dict,
        "validator_self_check": validator_view,
        "operator_trading_gate": gate_state,
        "intent_emission": {
            "RISEDUAL_EMIT_INTENTS_TO_MC": emit_intents,
            "enabled": emit_intents not in ("0", "false", "False", "(unset)"),
        },
        "mc_keys_proxy": {
            "MC_KEYS_PROXY_ENABLED": keys_proxy_enabled,
            "POLYGON_API_KEY_present": polygon_present,
            "FINNHUB_API_KEY_present": finnhub_present,
        },
        "tokens_present": token_present,
    }


__all__ = ["router", "set_db"]
