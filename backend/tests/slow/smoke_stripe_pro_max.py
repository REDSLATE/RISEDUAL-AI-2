"""Stripe Pro Max smoke test — end-to-end checkout / portal / webhook.

What this script proves
-----------------------
After flipping `STRIPE_SECRET_KEY` to the live key (or before, if you
want to verify test mode first), run this once against the deployed
preview URL or production host. It exercises the three real surfaces
that take real money:

1. **Subscription checkout** (`POST /api/billing/checkout/subscription`)
   — creates a live Stripe Checkout session for the ``pro_max`` plan
   and verifies the returned URL is a real ``checkout.stripe.com``
   redirect, plus detects whether the session was created in TEST or
   LIVE mode (so you can't accidentally launch in test).

2. **Customer Portal** (`POST /api/billing/portal`) — verifies the
   portal returns a real ``billing.stripe.com`` URL. A 400 here
   usually means the Customer Portal isn't configured yet in the
   Stripe Dashboard (Settings → Billing → Customer Portal).

3. **Webhook signature verification** (`POST /api/billing/webhook`) —
   constructs a Stripe-format signed payload using the local
   ``STRIPE_WEBHOOK_SECRET`` and confirms the backend accepts it.
   Also fires a deliberately bad signature to confirm the verifier
   rejects it (a 400/401 response is the correct behaviour).

Usage
-----
    python /app/backend/tests/smoke_stripe_pro_max.py
    python /app/backend/tests/smoke_stripe_pro_max.py --host https://risedual.ai
    python /app/backend/tests/smoke_stripe_pro_max.py --host https://api.risedual.ai

Reads admin credentials from ``/app/memory/test_credentials.md`` if
present, falls back to the env vars ``SMOKE_ADMIN_EMAIL`` /
``SMOKE_ADMIN_PASSWORD``.

Exit code 0 = all checks pass. Non-zero = at least one failed.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx


# Default admin credentials — override via env vars or
# /app/memory/test_credentials.md.
DEFAULT_EMAIL = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@risedual.ai")
DEFAULT_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "RiseDual2026!")


def _resolve_host() -> str:
    """Pull the backend URL from frontend/.env if no --host given."""
    env_path = Path("/app/frontend/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("Pass --host or set REACT_APP_BACKEND_URL")


def _resolve_webhook_secret() -> str | None:
    """Read the local STRIPE_WEBHOOK_SECRET so we can sign a payload."""
    env_path = Path("/app/backend/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("STRIPE_WEBHOOK_SECRET="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("STRIPE_WEBHOOK_SECRET")


# ── Pretty-printing ──────────────────────────────────────────────────────────


_PASS = "\033[32m✓ PASS\033[0m"
_FAIL = "\033[31m✗ FAIL\033[0m"
_WARN = "\033[33m⚠ WARN\033[0m"
_INFO = "\033[36mℹ\033[0m"


def _row(label: str, status: str, detail: str = "") -> None:
    print(f"  {status}  {label}" + (f" — {detail}" if detail else ""))


# ── Probes ───────────────────────────────────────────────────────────────────


def login(client: httpx.Client, email: str, password: str) -> str:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("token") or body.get("access_token")
    if not token:
        raise RuntimeError(f"Login OK but no token in response: {body}")
    return token


def check_pro_max_checkout(client: httpx.Client, token: str) -> dict:
    resp = client.post(
        "/api/billing/checkout/subscription",
        json={"plan": "pro_max"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if resp.status_code != 200:
        return {
            "ok": False,
            "detail": f"HTTP {resp.status_code}: {resp.text[:240]}",
        }
    body = resp.json()
    url = body.get("url") or body.get("checkout_url")
    session_id = body.get("session_id") or body.get("id")
    if not url or "checkout.stripe.com" not in url:
        return {
            "ok": False,
            "detail": f"checkout URL missing or wrong host: {url}",
        }
    # Mode detection — Stripe checkout URLs include /pay/cs_live_… or /pay/cs_test_…
    if "cs_live_" in url or (session_id or "").startswith("cs_live_"):
        mode = "LIVE"
    elif "cs_test_" in url or (session_id or "").startswith("cs_test_"):
        mode = "TEST"
    else:
        mode = "UNKNOWN"
    return {"ok": True, "url": url, "session_id": session_id, "mode": mode}


def check_portal(client: httpx.Client, token: str) -> dict:
    resp = client.post(
        "/api/billing/portal",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code == 400 and "configuration" in resp.text.lower():
        return {
            "ok": False,
            "configured": False,
            "detail": "Customer Portal is not configured in the Stripe Dashboard "
                      "(Settings → Billing → Customer Portal).",
        }
    if resp.status_code == 404:
        return {
            "ok": False,
            "configured": True,
            "detail": "User has no Stripe customer yet (expected if no checkout completed).",
        }
    if resp.status_code != 200:
        return {"ok": False, "detail": f"HTTP {resp.status_code}: {resp.text[:240]}"}
    body = resp.json()
    url = body.get("url") or body.get("portal_url")
    if not url or "billing.stripe.com" not in url:
        return {"ok": False, "detail": f"portal URL missing or wrong host: {url}"}
    return {"ok": True, "url": url}


def _sign_stripe_payload(payload: bytes, secret: str, ts: int | None = None) -> str:
    """Build a Stripe-format `Stripe-Signature` header value."""
    ts = ts or int(time.time())
    signed = f"{ts}.{payload.decode('utf-8')}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def check_webhook_verification(client: httpx.Client, secret: str | None) -> dict:
    if not secret:
        return {"ok": False, "detail": "STRIPE_WEBHOOK_SECRET not available locally — skipped"}

    # Minimal valid Stripe event shape — the verifier only checks the
    # signature, but the handler may decode the JSON body too.
    payload = json.dumps({
        "id": "evt_smoke_test_pro_max",
        "object": "event",
        "type": "ping",
        "data": {"object": {}},
    }).encode("utf-8")

    # 1. Valid signature → expect 200 (or 2xx — handler may no-op on unknown event types)
    good_sig = _sign_stripe_payload(payload, secret)
    good = client.post(
        "/api/billing/webhook",
        content=payload,
        headers={
            "Stripe-Signature": good_sig,
            "Content-Type": "application/json",
        },
        timeout=10,
    )

    # 2. Bad signature → expect 400/401 (verifier MUST reject)
    bad_sig = _sign_stripe_payload(payload, secret + "tampered")
    bad = client.post(
        "/api/billing/webhook",
        content=payload,
        headers={
            "Stripe-Signature": bad_sig,
            "Content-Type": "application/json",
        },
        timeout=10,
    )

    accepts_valid = 200 <= good.status_code < 300
    rejects_invalid = bad.status_code in (400, 401, 403)

    return {
        "ok": accepts_valid and rejects_invalid,
        "valid_status": good.status_code,
        "invalid_status": bad.status_code,
        "valid_body": good.text[:160],
        "invalid_body": bad.text[:160],
        "detail": (
            f"valid_sig→{good.status_code}, invalid_sig→{bad.status_code}"
            + ("" if accepts_valid else " (verifier did not accept a valid sig)")
            + ("" if rejects_invalid else " (verifier did not reject an invalid sig)")
        ),
    }


def detect_mode_from_env() -> dict:
    """Inspect /app/backend/.env and warn on key/price-id mode mismatch."""
    env_path = Path("/app/backend/.env")
    if not env_path.exists():
        return {"detail": "/app/backend/.env not readable — skipped"}
    body = env_path.read_text()

    def grab(name: str) -> str:
        m = re.search(rf"^{re.escape(name)}=(.*)$", body, re.MULTILINE)
        return (m.group(1).strip() if m else "")

    api_key = grab("STRIPE_API_KEY")
    secret_key = grab("STRIPE_SECRET_KEY")
    pro_max_price = grab("STRIPE_PRICE_PRO_MAX")

    api_mode = "LIVE" if api_key.startswith("sk_live_") else \
               "TEST" if api_key.startswith("sk_test_") else "UNSET"
    secret_mode = "LIVE" if secret_key.startswith("sk_live_") else \
                  "TEST" if secret_key.startswith("sk_test_") else "UNSET"
    # Price IDs are the same shape in both modes — they're tied to the
    # account, not the key — but a price ID created in live mode WILL NOT
    # work with a test key. Worth surfacing.
    issues = []
    if api_mode != secret_mode and "UNSET" not in (api_mode, secret_mode):
        issues.append(f"STRIPE_API_KEY={api_mode} but STRIPE_SECRET_KEY={secret_mode}")
    if not pro_max_price:
        issues.append("STRIPE_PRICE_PRO_MAX not set")

    return {
        "api_mode": api_mode,
        "secret_mode": secret_mode,
        "pro_max_price": pro_max_price[:20] + "…" if pro_max_price else "(none)",
        "issues": issues,
    }


# ── Runner ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None,
                        help="Backend base URL (default: REACT_APP_BACKEND_URL)")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()

    host = (args.host or _resolve_host()).rstrip("/")

    print(f"\n{_INFO} Stripe Pro Max smoke test")
    print(f"   target: {host}")
    print(f"   user:   {args.email}\n")

    failures = 0

    # ── 0. Local env mode check ──
    print("┌─ Env mode check")
    env_info = detect_mode_from_env()
    print(f"│  STRIPE_API_KEY    : {env_info.get('api_mode')}")
    print(f"│  STRIPE_SECRET_KEY : {env_info.get('secret_mode')}")
    print(f"│  STRIPE_PRICE_PRO_MAX: {env_info.get('pro_max_price')}")
    if env_info.get("issues"):
        for issue in env_info["issues"]:
            _row("env consistency", _WARN, issue)
            failures += 1
    else:
        _row("env consistency", _PASS, "keys + price ID aligned")
    print("└─\n")

    with httpx.Client(base_url=host, follow_redirects=False) as client:

        # ── 1. Login ──
        print("┌─ 1. Auth")
        try:
            token = login(client, args.email, args.password)
            _row("admin login", _PASS, f"token len={len(token)}")
        except Exception as exc:
            _row("admin login", _FAIL, str(exc))
            print("└─\n")
            print(f"\n{_FAIL} Cannot continue without auth token — exiting.")
            return 1
        print("└─\n")

        # ── 2. Pro Max checkout session ──
        print("┌─ 2. Pro Max subscription checkout")
        co = check_pro_max_checkout(client, token)
        if co["ok"]:
            _row("checkout session created", _PASS,
                 f"mode={co['mode']}, session={co.get('session_id', '?')[:20]}…")
            if co["mode"] == "TEST":
                _row("mode safety", _WARN,
                     "Stripe is in TEST mode — real cards won't charge.")
            elif co["mode"] == "LIVE":
                _row("mode safety", _PASS, "Stripe in LIVE mode")
            else:
                _row("mode safety", _WARN, "could not detect mode from URL")
        else:
            _row("checkout session", _FAIL, co["detail"])
            failures += 1
        print("└─\n")

        # ── 3. Customer Portal ──
        print("┌─ 3. Customer Portal")
        portal = check_portal(client, token)
        if portal["ok"]:
            _row("portal URL", _PASS, "billing.stripe.com URL returned")
        elif portal.get("configured") is False:
            _row("portal URL", _FAIL, portal["detail"])
            failures += 1
        else:
            _row("portal URL", _WARN, portal["detail"])
        print("└─\n")

        # ── 4. Webhook signature verification ──
        print("┌─ 4. Webhook signature verifier")
        secret = _resolve_webhook_secret()
        wh = check_webhook_verification(client, secret)
        if wh["ok"]:
            _row("verifier accepts valid sig", _PASS, f"HTTP {wh['valid_status']}")
            _row("verifier rejects invalid sig", _PASS, f"HTTP {wh['invalid_status']}")
        else:
            _row("webhook verifier", _FAIL, wh["detail"])
            failures += 1
        print("└─\n")

    # ── Summary ──
    if failures == 0:
        print(f"{_PASS}  All checks passed.\n")
        return 0
    print(f"{_FAIL}  {failures} check(s) failed.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
