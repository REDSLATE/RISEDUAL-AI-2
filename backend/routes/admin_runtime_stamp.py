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


def _get_last_posted() -> dict[str, Any]:
    """Snapshot of the last stamp the periodic mc_checkin loop POSTed.

    Crucial diagnostic — answers "what is the periodic loop actually
    sending to MC?" without log digging. If this disagrees with
    runtime_stamp above, something between RuntimeStamp.current() and
    the POST is mutating the stamp.
    """
    try:
        from services.mc_checkin import get_last_posted_stamp
        snap = get_last_posted_stamp()
        if snap.get("stamp") is None:
            return {"stamp": None, "posted_at": None, "note": "no checkin yet"}
        s = snap["stamp"]
        # Surface only the validator-relevant fields — keep response small.
        return {
            "posted_at": snap.get("posted_at"),
            "env_name": s.get("env_name"),
            "db_name": s.get("db_name"),
            "mc_url": s.get("mc_url"),
            "broker_mode": s.get("broker_mode"),
            "git_sha": s.get("git_sha"),
            "policy_hash": s.get("policy_hash"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"snapshot unavailable: {exc}"}


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
        "last_posted_to_mc": _get_last_posted(),
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


# ── MC identity probe (2026-06) ─────────────────────────────────────
#
# Issue: MC's identity surface (``/api/admin/runtime/{brain}/status``)
# returned 401 when the brain hit it with ``X-Brain-Id`` +
# ``X-Runtime-Token`` — the exact headers documented as the runtime
# auth scheme in ``MC_BRAIN_API_QUICKSTART_v1.md`` section 10.
#
# This probe lets the operator confirm exactly what MC is returning
# without writing one-off curls. It hits MC live and surfaces the
# status code + first 200 chars of the response, so a 401 can be
# triaged into one of:
#   * runtime token rotated on MC but not in our env
#   * MC moved identity behind operator JWT auth (doc out of date)
#   * MC env (preview vs prod) doesn't issue this brain a token
#
# Read-only on MC's side; the GET cannot mutate state.
@router.get("/mc-identity-probe")
async def mc_identity_probe(request: Request) -> dict[str, Any]:
    """Probe MC's identity endpoint with the brain's runtime token.

    Returns::

        {
          "url": "<full url>",
          "headers_sent": {"X-Brain-Id": "<brain>", "X-Runtime-Token": "***"},
          "status_code": int,
          "response_preview": "<first 400 chars>",
          "hint": "<operator-facing diagnosis>",
        }

    Never raises — network errors surface as ``status_code=null``
    with the exception text in ``response_preview``.
    """
    await _require_owner(request)

    import httpx

    base = (
        os.environ.get("RISEDUAL_MC_URL")
        or os.environ.get("MC_BASE_URL")
        or ""
    ).strip().rstrip("/")
    brain = (
        os.environ.get("RISEDUAL_APP_NAME")
        or os.environ.get("RUNTIME_NAME")
        or "alpha"
    ).strip() or "alpha"
    brain = brain.lower()
    token = (
        os.environ.get("ALPHA_MC_INGEST_TOKEN")
        or os.environ.get("ALPHA_INGEST_TOKEN")
        or ""
    ).strip()

    if not base or not token:
        return {
            "url": None,
            "status_code": None,
            "response_preview": "",
            "hint": (
                "MC base URL or runtime token missing — set "
                "RISEDUAL_MC_URL + ALPHA_MC_INGEST_TOKEN before probing."
            ),
        }

    url = f"{base}/api/admin/runtime/{brain}/status"
    headers = {
        "X-Brain-Id": brain,
        "X-Runtime-Token": token,
        "Accept": "application/json",
    }

    status_code: Any = None
    body_preview = ""
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(url, headers=headers)
            status_code = resp.status_code
            body_preview = (resp.text or "")[:400]
    except Exception as exc:  # noqa: BLE001
        body_preview = f"transport_error: {exc}"

    hint = _identity_probe_hint(status_code, body_preview)
    return {
        "url": url,
        "headers_sent": {
            "X-Brain-Id": brain,
            "X-Runtime-Token": "*** (present)",
        },
        "status_code": status_code,
        "response_preview": body_preview,
        "hint": hint,
    }


def _identity_probe_hint(status_code: Any, body_preview: str) -> str:
    """Human-friendly diagnosis for the operator. Pure-function so
    tests can pin every branch without HTTP."""
    if status_code is None:
        return (
            "Could not reach MC. Check RISEDUAL_MC_URL is correct + "
            "network egress."
        )
    if status_code == 200:
        return "Identity surface returns 200 — runtime token is accepted."
    if status_code == 401:
        return (
            "MC rejected the runtime token. Either: (a) token was "
            "rotated on MC and the env var is stale, (b) MC moved "
            "this route behind operator JWT auth (doctrine drift), "
            "or (c) MC's preview/prod env doesn't issue this brain a "
            "token. Ping the MC operator with this probe response."
        )
    if status_code == 403:
        return (
            "MC returned 403 — the token is valid but lacks permission "
            "for this route. Likely operator JWT-only path. Ask MC if "
            "the identity surface still accepts X-Runtime-Token per the "
            "MC_BRAIN_API_QUICKSTART_v1 doc."
        )
    if status_code == 404:
        return (
            "MC returned 404 — the route path may have changed. "
            "Compare with MC's deployed API listing."
        )
    return f"MC returned {status_code}. Inspect response_preview."


__all__ = ["router", "set_db"]
