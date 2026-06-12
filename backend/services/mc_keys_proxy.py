"""MC Keys Proxy — boot-time fetcher for centrally-managed market-data keys.

MC's ``/api/admin/keys/market-data`` endpoint is the canonical source for
Polygon + Finnhub API keys across the 5-brain federation. Per the
single-source-of-truth doctrine, each brain (including the trading-app
acting as Alpha) calls the proxy on boot, stamps the returned keys into
``os.environ``, and lets every downstream service read them via the
existing ``os.environ.get(...)`` paths transparently.

Why this matters
----------------
* **Defense-in-depth.** Local ``.env`` is the fallback; MC is the
  authoritative source. A key rotation on MC propagates to every
  brain on next boot WITHOUT a brain-side code redeploy.
* **Audit lineage.** MC's audit collection records every fetch by
  brain — proves which sidecar pulled which key at what time.
* **Symptom-killer.** The ``STUCK_FEATURES_NO_DIVERSITY`` self-veto
  fires when brains have no real bars in the feature pipeline. The
  proxy guarantees every brain has identical, up-to-date keys to
  the same data feeds.

Contract
--------
::

    GET {MC_BASE_URL}/api/admin/keys/market-data
    Headers:
        X-Runtime-Token: <ALPHA_INGEST_TOKEN>
        X-Brain-Id: alpha

    Response 200:
        {
          "keys": {
            "POLYGON_API_KEY": "...",
            "FINNHUB_API_KEY": "..."
          },
          ...
        }

Doctrine pins
-------------
* **Fail-soft.** Network failure, 4xx/5xx, schema mismatch — all
  return ``None`` and leave the local ``.env`` values intact. A
  missing MC must NEVER block backend startup.
* **No silent override of better values.** If MC returns an empty
  string for a key, we keep whatever was already in ``os.environ``.
  Defends against an MC misconfiguration zeroing out a brain's
  data feed.
* **Opt-out via env.** ``MC_KEYS_PROXY_ENABLED=false`` keeps the
  brain on local keys entirely — useful for preview pods where MC
  may not have a route for ``preview``-stamped sidecars yet.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


_KEYS_OF_INTEREST: tuple[str, ...] = (
    "POLYGON_API_KEY",
    "FINNHUB_API_KEY",
)


def _is_enabled() -> bool:
    """Default ON. ``MC_KEYS_PROXY_ENABLED=false`` disables the fetch."""
    return (os.environ.get("MC_KEYS_PROXY_ENABLED") or "true").strip().lower() not in (
        "false", "0", "no", "off",
    )


def _mc_base_url() -> Optional[str]:
    url = (os.environ.get("RISEDUAL_MC_URL") or os.environ.get("MC_BASE_URL") or "").strip()
    return url.rstrip("/") or None


def _runtime_token() -> Optional[str]:
    tok = (
        os.environ.get("ALPHA_MC_INGEST_TOKEN")
        or os.environ.get("ALPHA_INGEST_TOKEN")
        or ""
    ).strip()
    return tok or None


def _brain_id() -> str:
    return (os.environ.get("RISEDUAL_APP_NAME") or "alpha").strip() or "alpha"


def fetch_market_data_keys(
    *, timeout_seconds: float = 6.0,
) -> Optional[dict[str, str]]:
    """Pull the canonical market-data key bundle from MC.

    Returns the ``keys`` sub-dict on success, ``None`` on any failure.
    Never raises out — this is a best-effort enhancement, not a
    blocker.

    2026-06-09 MC2 severance: in standalone mode the MC keys proxy
    is skipped entirely. The pod runs on whatever Polygon/Finnhub
    keys are in the local ``.env``; no outbound HTTP to Original MC
    is issued. (You can still rotate keys by updating the env on
    the deployed pod.)
    """
    try:
        from services.mc2 import is_standalone
        if is_standalone():
            logger.info(
                "[mc_keys_proxy] RISEDUAL_STANDALONE_MODE=1 — Original MC "
                "keys-proxy SKIPPED (local .env keys remain authoritative)"
            )
            return None
    except Exception:  # noqa: BLE001
        pass

    if not _is_enabled():
        logger.info("[mc_keys_proxy] disabled via MC_KEYS_PROXY_ENABLED=false")
        return None

    base = _mc_base_url()
    token = _runtime_token()
    if not base or not token:
        logger.warning(
            "[mc_keys_proxy] skipped — missing %s",
            "MC base URL" if not base else "runtime token",
        )
        return None

    url = f"{base}/api/admin/keys/market-data"
    headers = {
        "X-Runtime-Token": token,
        "X-Brain-Id": _brain_id(),
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("[mc_keys_proxy] transport error: %s", exc)
        return None

    if response.status_code >= 400:
        logger.warning(
            "[mc_keys_proxy] %s returned %d: %s",
            url, response.status_code, response.text[:200],
        )
        return None

    try:
        body: Any = response.json()
    except ValueError as exc:
        logger.warning("[mc_keys_proxy] non-JSON response: %s", exc)
        return None

    if not isinstance(body, dict):
        logger.warning("[mc_keys_proxy] unexpected response shape: %r", type(body))
        return None

    keys = body.get("keys")
    if not isinstance(keys, dict):
        logger.warning("[mc_keys_proxy] response missing 'keys' dict")
        return None

    # Coerce to {str: str} — drop any non-string values defensively.
    clean: dict[str, str] = {}
    for k, v in keys.items():
        if isinstance(k, str) and isinstance(v, str) and v:
            clean[k] = v
    return clean


def apply_keys_to_environ(keys: dict[str, str]) -> list[str]:
    """Stamp the returned keys into ``os.environ``.

    Returns the list of key names actually applied. Empty-string
    values (or anything not in ``_KEYS_OF_INTEREST``) are skipped —
    we never zero out a working local key with a blank MC response.
    """
    applied: list[str] = []
    for name in _KEYS_OF_INTEREST:
        value = (keys.get(name) or "").strip()
        if not value:
            continue
        os.environ[name] = value
        applied.append(name)
    return applied


def fetch_and_apply(*, timeout_seconds: float = 6.0) -> dict[str, Any]:
    """Convenience for the boot path. Returns a status dict.

    ``status`` is one of: ``"applied"``, ``"skipped"``, ``"failed"``.
    Always returns — never raises. Safe to call inside any startup
    ``try/except`` that already guards the rest of the wire-up.
    """
    keys = fetch_market_data_keys(timeout_seconds=timeout_seconds)
    if keys is None:
        return {"status": "skipped", "applied": [], "reason": "fetch_failed_or_disabled"}
    applied = apply_keys_to_environ(keys)
    if not applied:
        return {"status": "skipped", "applied": [], "reason": "no_usable_keys"}
    logger.info(
        "[mc_keys_proxy] applied %d MC-sourced market-data keys: %s",
        len(applied), ", ".join(applied),
    )
    return {"status": "applied", "applied": applied}


__all__ = [
    "fetch_market_data_keys",
    "apply_keys_to_environ",
    "fetch_and_apply",
]
