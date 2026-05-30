"""mc_identity_v1.py — drop-in identity surface for brain sidecars.

Owned by: Mission Control (MC).
Spec version: v1 (2026-05-30).
Target brains: Alpha, Camaro, Chevelle, RedEye.

This file is verbatim from MC's brain-team handoff. DO NOT MODIFY —
contract-pinned by ``tests/test_mc_identity_v1_contract.py``. If a
strong reason to deviate exists, bump to v2 and dual-publish during
a deprecation window.

See BRAIN_IDENTITY_HANDOFF.md (committed alongside this file) for the
integration contract.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional


logger = logging.getLogger("brain.mc_identity")

# Prevent the lifecycle log from firing more than once per process.
_LIFECYCLE_LOGGED = threading.Event()


def _get(name: str) -> Optional[str]:
    """Read an env var; treat empty string as unset (common deploy bug)."""
    v = os.environ.get(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def build_identity_block(
    *,
    app_name: str,
    sidecar_version: str,
    env_name: Optional[str] = None,
    git_sha: Optional[str] = None,
    broker_mode: Optional[str] = None,
) -> dict:
    """Build the identity block MC's chip reads.

    Args:
        app_name:        Human label for this brain (e.g. "alpha").
        sidecar_version: Your sidecar's release version.
        env_name:        Defaults to ENV_NAME or "unknown".
        git_sha:         Defaults to GIT_SHA env var.
        broker_mode:     "paper" | "live" | None.
    """
    mc_url = _get("MC_URL")
    ingest_token = _get("MC_INGEST_TOKEN")
    mc_base_url = _get("MC_BASE_URL")
    heartbeat_token = _get("HEARTBEAT_TOKEN")

    mc_url_set = mc_url is not None
    ingest_token_set = ingest_token is not None
    mc_base_url_set = mc_base_url is not None
    heartbeat_token_set = heartbeat_token is not None

    checkin_worker_eligible = (
        mc_url_set
        and ingest_token_set
        and mc_base_url_set
        and heartbeat_token_set
    )

    return {
        "app_name": app_name,
        "env_name": env_name or _get("ENV_NAME") or "unknown",
        "git_sha": git_sha or _get("GIT_SHA"),
        "broker_mode": broker_mode or _get("BROKER_MODE"),
        "sidecar_version": sidecar_version,
        # Check-in pair
        "mc_url_set": mc_url_set,
        "ingest_token_set": ingest_token_set,
        # Heartbeat pair
        "mc_base_url_set": mc_base_url_set,
        "heartbeat_token_set": heartbeat_token_set,
        # Composite
        "checkin_worker_eligible": checkin_worker_eligible,
    }


def log_lifecycle(identity: dict) -> None:
    """Emit exactly ONE lifecycle log line at boot.

    The NOT STARTED branch ALWAYS names the missing var(s). This is the
    contract MC's tripwire test asserts. Don't change the format.
    """
    if _LIFECYCLE_LOGGED.is_set():
        return
    _LIFECYCLE_LOGGED.set()

    pairs = [
        ("MC_URL", identity["mc_url_set"]),
        ("MC_INGEST_TOKEN", identity["ingest_token_set"]),
        ("MC_BASE_URL", identity["mc_base_url_set"]),
        ("HEARTBEAT_TOKEN", identity["heartbeat_token_set"]),
    ]
    if identity["checkin_worker_eligible"]:
        set_clause = ", ".join(f"{name} set" for name, _ in pairs)
        logger.info(
            "mc_checkin worker STARTED — periodic check-in every 300s (%s)",
            set_clause,
        )
    else:
        missing = [name for name, present in pairs if not present]
        logger.warning(
            "mc_checkin worker NOT STARTED — missing env vars: %s",
            ", ".join(missing) if missing else "unknown",
        )


def is_checkin_eligible() -> bool:
    """Sentinel — should I start my worker?"""
    return build_identity_block(
        app_name="probe", sidecar_version="probe",
    )["checkin_worker_eligible"]


def start_checkin_worker(
    interval_s: int = 300,
    *,
    on_tick=None,
    on_error=None,
) -> None:
    """Spawn a background thread that pings MC every `interval_s` seconds.

    SKELETON only. Alpha already has ``services.mc_checkin`` running a
    proper async loop — this function is here for spec compliance.
    Real wire-up lives in server.py's startup hook.
    """
    if not is_checkin_eligible():
        logger.warning("start_checkin_worker: NOT STARTED (env vars missing)")
        return

    import time

    def _loop():
        while True:
            try:
                if on_tick is not None:
                    on_tick()
            except Exception as e:  # noqa: BLE001
                if on_error is not None:
                    try:
                        on_error(e)
                    except Exception:  # noqa: BLE001
                        pass
                logger.warning("mc_checkin: periodic ping failed: %r", e)
            time.sleep(interval_s)

    t = threading.Thread(target=_loop, daemon=True, name="mc_checkin_worker")
    t.start()
    logger.info(
        "mc_checkin worker thread spawned: name=%s interval=%ss",
        t.name, interval_s,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    iden = build_identity_block(app_name="selftest", sidecar_version="0.0.0")
    log_lifecycle(iden)
    print("identity block:")
    for k, v in iden.items():
        print(f"  {k:28} {v!r}")
    print()
    print(f"checkin_worker_eligible: {iden['checkin_worker_eligible']}")
