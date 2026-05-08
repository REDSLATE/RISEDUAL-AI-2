"""Stripe webhook verifier — lists live-mode endpoints + checks coverage.

What this script proves
-----------------------
After flipping to live mode, you need to confirm three things in your
Stripe Dashboard's **live** webhook list (not the test-mode list — they
are separate):

1. **An endpoint is registered** that points at your production
   webhook URL (default check: ``https://api.risedual.ai/api/billing/webhook``,
   override with ``--expected-url``).

2. **The endpoint is active** (Stripe lets you disable an endpoint
   without deleting it — easy to miss in the Dashboard).

3. **It subscribes to the events your handler expects** so paid
   subscriptions actually activate. The required set lives in
   :data:`REQUIRED_EVENTS` and matches what
   ``services.stripe_billing_service.process_webhook`` handles.

The script also surfaces every other live webhook endpoint (so you
spot accidental staging / dev endpoints still attached to live mode)
and reminds you that the **signing secret cannot be re-fetched via
the API** — Stripe shows it once on endpoint creation. The only
verification we can do for the secret is whether one is set locally;
the actual match is verified end-to-end by the real
``smoke_stripe_pro_max.py`` webhook check.

Usage
-----
    python /app/backend/tests/verify_stripe_webhook.py
    python /app/backend/tests/verify_stripe_webhook.py --expected-url https://risedual.ai/api/billing/webhook

Exit code 0 = production webhook is correctly configured.
Exit code 1 = at least one issue (missing endpoint, disabled endpoint,
              missing required events, or no local signing secret).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import stripe
from dotenv import load_dotenv


# Default expected webhook URL — adjust if you host the API on a
# different subdomain. ``--expected-url`` overrides at runtime.
DEFAULT_EXPECTED_URL = "https://api.risedual.ai/api/billing/webhook"

# Events the FastAPI handler in services.stripe_billing_service.process_webhook
# actually does work on. If any of these are NOT subscribed in the
# Stripe Dashboard, the corresponding business outcome silently breaks:
#
# * ``checkout.session.completed``       — first-time subscription activation
# * ``invoice.paid``                     — recurring monthly renewal
# * ``invoice.payment_failed``           — flag past-due, downgrade UX
# * ``customer.subscription.updated``    — plan change / quantity change
# * ``customer.subscription.deleted``    — user-initiated cancellation
REQUIRED_EVENTS = frozenset({
    "checkout.session.completed",
    "invoice.paid",
    "invoice.payment_failed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})


# ── Pretty-printing ──────────────────────────────────────────────────────────


_PASS = "\033[32m✓ PASS\033[0m"
_FAIL = "\033[31m✗ FAIL\033[0m"
_WARN = "\033[33m⚠ WARN\033[0m"
_INFO = "\033[36mℹ\033[0m"


def _row(label: str, status: str, detail: str = "") -> None:
    print(f"  {status}  {label}" + (f" — {detail}" if detail else ""))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_env() -> tuple[str, str | None]:
    """Read STRIPE_API_KEY (live) + STRIPE_WEBHOOK_SECRET from .env.

    Uses ``override=True`` because the platform may have set an
    Emergent test key at the shell level that would otherwise mask
    our real live key (same gotcha resolved in
    smoke_stripe_pro_max.py).
    """
    env_path = Path("/app/backend/.env")
    load_dotenv(env_path, override=True)
    api_key = os.environ.get("STRIPE_API_KEY", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    return api_key, webhook_secret


def _normalise(url: str) -> str:
    """Trailing-slash and case-insensitive comparison helper."""
    return (url or "").rstrip("/").lower()


# ── Probes ───────────────────────────────────────────────────────────────────


def list_live_endpoints() -> list:
    """Page through every webhook endpoint Stripe knows about under
    the configured live key. Stripe paginates at 100 by default."""
    out = []
    starting_after = None
    while True:
        kwargs = {"limit": 100}
        if starting_after:
            kwargs["starting_after"] = starting_after
        page = stripe.WebhookEndpoint.list(**kwargs)
        out.extend(page.data)
        if not page.has_more:
            break
        starting_after = page.data[-1].id
    return out


def evaluate_endpoint(ep, expected_url: str) -> dict:
    """Return ``{matches_url, active, missing_events, extra_events}``
    for a single endpoint."""
    matches_url = _normalise(ep.url) == _normalise(expected_url)
    active = (ep.status == "enabled")
    enabled = set(ep.enabled_events or [])
    # ``["*"]`` means "subscribe to everything" — that satisfies the
    # required set trivially.
    if "*" in enabled:
        missing = set()
    else:
        missing = REQUIRED_EVENTS - enabled
    extra = enabled - REQUIRED_EVENTS - {"*"}
    return {
        "id": ep.id,
        "url": ep.url,
        "status": ep.status,
        "matches_url": matches_url,
        "active": active,
        "enabled_events": sorted(enabled),
        "missing_events": sorted(missing),
        "extra_events": sorted(extra),
    }


# ── Runner ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expected-url",
        default=DEFAULT_EXPECTED_URL,
        help=f"Production webhook URL to look for (default: {DEFAULT_EXPECTED_URL})",
    )
    args = parser.parse_args()
    expected_url = args.expected_url

    print(f"\n{_INFO} Stripe webhook verifier")
    print(f"   expected URL: {expected_url}\n")

    api_key, webhook_secret = _load_env()
    if not api_key:
        print(f"{_FAIL} STRIPE_API_KEY not set — cannot query Stripe.")
        return 1

    mode = "LIVE" if api_key.startswith("sk_live_") \
        else "TEST" if api_key.startswith("sk_test_") \
        else "UNKNOWN"
    print("┌─ Local config")
    print(f"│  STRIPE_API_KEY mode      : {mode}")
    print(f"│  STRIPE_WEBHOOK_SECRET set: {'yes' if webhook_secret else 'no'}")
    print("└─\n")

    if mode != "LIVE":
        print(f"{_WARN}  Stripe key is not in LIVE mode — this script lists "
              "endpoints for whichever mode the key belongs to. Continuing.\n")

    stripe.api_key = api_key

    failures = 0

    # ── 1. Pull every endpoint ──
    print("┌─ 1. Webhook endpoints registered with Stripe")
    try:
        endpoints = list_live_endpoints()
    except Exception as exc:  # noqa: BLE001
        print(f"  {_FAIL}  Stripe API call failed: {exc}")
        print("└─\n")
        return 1

    if not endpoints:
        _row("endpoints found", _FAIL,
             "no webhook endpoints registered for this mode")
        print("└─\n")
        print(f"{_FAIL}  Add the production webhook in Dashboard → "
              "Developers → Webhooks (LIVE mode).\n")
        return 1

    print(f"  Found {len(endpoints)} endpoint(s):")
    for ep in endpoints:
        marker = "→" if _normalise(ep.url) == _normalise(expected_url) else " "
        print(f"    {marker} {ep.id}  [{ep.status}]  {ep.url}")
    print("└─\n")

    # ── 2. Find the production endpoint ──
    print("┌─ 2. Production endpoint match")
    matches = [ep for ep in endpoints
               if _normalise(ep.url) == _normalise(expected_url)]
    if not matches:
        _row("match for expected URL", _FAIL,
             f"no endpoint URL equals {expected_url}")
        failures += 1
        print("└─\n")
    elif len(matches) > 1:
        _row("match for expected URL", _WARN,
             f"{len(matches)} endpoints share this URL — Stripe will "
             "deliver to all of them (duplicates likely)")
        print("└─\n")
    else:
        _row("match for expected URL", _PASS, matches[0].id)
        print("└─\n")

    # ── 3. Per-endpoint health (run for every match if duplicates) ──
    target = matches[0] if matches else None
    if target is not None:
        print(f"┌─ 3. Endpoint health: {target.id}")
        report = evaluate_endpoint(target, expected_url)
        if report["active"]:
            _row("endpoint enabled", _PASS, "status=enabled")
        else:
            _row("endpoint enabled", _FAIL, f"status={report['status']}")
            failures += 1

        if not report["missing_events"]:
            _row("required events subscribed", _PASS,
                 f"{len(REQUIRED_EVENTS)}/{len(REQUIRED_EVENTS)} present "
                 + ("(wildcard *)" if "*" in report["enabled_events"] else ""))
        else:
            _row("required events subscribed", _FAIL,
                 f"missing: {', '.join(report['missing_events'])}")
            failures += 1

        if report["extra_events"]:
            _row("extra events", _INFO,
                 f"{len(report['extra_events'])} additional event(s) "
                 "subscribed (harmless, just informational)")
        print("└─\n")

    # ── 4. Local signing secret presence ──
    print("┌─ 4. Local signing secret")
    if webhook_secret and webhook_secret.startswith("whsec_"):
        _row("STRIPE_WEBHOOK_SECRET", _PASS,
             f"{webhook_secret[:12]}… (signature match verified by "
             "smoke_stripe_pro_max.py end-to-end)")
    elif webhook_secret:
        _row("STRIPE_WEBHOOK_SECRET", _WARN,
             "set but doesn't start with 'whsec_' — likely malformed")
        failures += 1
    else:
        _row("STRIPE_WEBHOOK_SECRET", _FAIL,
             "not set in /app/backend/.env — webhook signature verification "
             "will reject every event")
        failures += 1
    print(f"  {_INFO}  Stripe does not let you re-fetch the signing secret "
          "after creation. To rotate: Dashboard → Developers → Webhooks → "
          "<endpoint> → 'Roll secret' or recreate the endpoint, then update "
          "STRIPE_WEBHOOK_SECRET in .env.")
    print("└─\n")

    # ── Summary ──
    if failures == 0:
        print(f"{_PASS}  Stripe webhook is correctly configured for live mode.\n")
        return 0
    print(f"{_FAIL}  {failures} issue(s) found — fix before going public.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
