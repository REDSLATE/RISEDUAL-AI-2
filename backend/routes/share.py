"""Social-share endpoint — server-rendered HTML pages for shareable ticker links.

`GET /api/share/{ticker}` returns a tiny HTML document with full OpenGraph,
Twitter Card and JSON-LD metadata baked in server-side, so when a user pastes
the URL on X/Twitter, LinkedIn, Facebook, WhatsApp, iMessage, Slack, Discord,
Telegram, Reddit, Bluesky, Pinterest etc. the platform's crawler sees rich
preview metadata (title, description, image, live price).

For a real human browser that lands on the page, a meta-refresh + JS redirect
bounces them into the SPA via `/?warroom=TICKER`, where App.js dispatches the
existing deep-link events to auto-open the AI War Room.
"""
from __future__ import annotations

import html
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from services.price_provider import get_quote

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/share", tags=["share"])

# Public site URL (logo, canonical, etc.). Keep in sync with frontend env.
SITE_URL = os.environ.get("PUBLIC_SITE_URL", "https://risedual.ai")
BRAND = "RISEDUAL AI"
TAGLINE = "Adversarial AI Trading Platform"


def _fmt_price(q: dict | None) -> tuple[str, str]:
    """Return (title_suffix, description_suffix) reflecting live quote."""
    if not q:
        return "", ""
    price = q.get("price") or 0
    change_pct = q.get("changePercent") or q.get("change_pct") or 0
    if not price:
        return "", ""
    arrow = "▲" if change_pct >= 0 else "▼"
    title = f" — ${price:,.2f} {arrow}{abs(change_pct):.2f}%"
    desc = f" Last: ${price:,.2f} ({arrow}{abs(change_pct):.2f}%)."
    return title, desc


def _escape(s: str) -> str:
    return html.escape(s or "", quote=True)


def _public_base(request: Request) -> str:
    """Resolve the public-facing base URL honoring reverse-proxy headers.

    Preference order:
      1. X-Forwarded-Proto + X-Forwarded-Host (ingress/load-balancer)
      2. Host header + assumed https
      3. PUBLIC_SITE_URL env override
      4. request.base_url (cluster-internal fallback)
    """
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if fwd_host and "cluster" not in fwd_host and "localhost" not in fwd_host:
        scheme = fwd_proto or "https"
        return f"{scheme}://{fwd_host}"
    return os.environ.get("PUBLIC_SITE_URL", SITE_URL).rstrip("/")


def _build_html(ticker: str, quote: dict | None, request: Request, ref: str | None = None) -> str:
    t = ticker.upper()
    price_title, price_desc = _fmt_price(quote)
    title = f"{t} · {BRAND} — AI War Room{price_title}"
    description = (
        f"{t} analyzed by the {BRAND} adversarial AI: dual-signal Strategist + Auditor "
        f"verdict, multi-model prediction consensus, 13F smart-money flow, options & "
        f"dark-pool signals.{price_desc} Open the War Room for the full breakdown."
    )

    # Absolute canonical URL for the share page (what crawlers will see).
    base = _public_base(request)
    canonical = f"{base}/api/share/{t}"
    # Dynamic per-ticker OG card (1200×630) with live price + brand.
    og_image = f"{base}/api/share/img/{t}.png"
    # Preserve a referral attribution code (`?ref=CODE`) through the SPA
    # redirect so `useReferralCapture` + AuthModal can read it at signup.
    ref_suffix = f"&ref={ref}" if ref else ""
    spa_url = f"{base}/?warroom={t}{ref_suffix}"

    # JSON-LD: FinancialProduct structured data (Google rich results).
    ld_json = {
        "@context": "https://schema.org",
        "@type": "FinancialProduct",
        "name": f"{t} — AI Analysis",
        "description": description,
        "url": canonical,
        "brand": {"@type": "Brand", "name": BRAND},
        "provider": {"@type": "Organization", "name": BRAND, "url": SITE_URL},
    }
    if quote and quote.get("price"):
        ld_json["offers"] = {
            "@type": "Offer",
            "price": f"{quote['price']:.2f}",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
        }

    import json as _json
    ld_script = _json.dumps(ld_json, separators=(",", ":"))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{_escape(title)}</title>
<meta name="description" content="{_escape(description)}"/>
<link rel="canonical" href="{_escape(canonical)}"/>

<!-- OpenGraph (Facebook, LinkedIn, WhatsApp, iMessage, Slack, Discord,
     Telegram, Reddit, Bluesky, Pinterest, Signal, Teams) -->
<meta property="og:type" content="website"/>
<meta property="og:site_name" content="{BRAND}"/>
<meta property="og:title" content="{_escape(title)}"/>
<meta property="og:description" content="{_escape(description)}"/>
<meta property="og:image" content="{og_image}"/>
<meta property="og:image:alt" content="{BRAND} — {t}"/>
<meta property="og:image:width" content="1200"/>
<meta property="og:image:height" content="630"/>
<meta property="og:url" content="{_escape(canonical)}"/>
<meta property="og:locale" content="en_US"/>

<!-- Twitter / X Cards -->
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="{_escape(title)}"/>
<meta name="twitter:description" content="{_escape(description)}"/>
<meta name="twitter:image" content="{og_image}"/>
<meta name="twitter:image:alt" content="{BRAND} — {t}"/>

<!-- Misc platform hints -->
<meta name="theme-color" content="#060E1F"/>
<meta name="application-name" content="{BRAND}"/>
<meta name="apple-mobile-web-app-title" content="{BRAND}"/>
<meta name="robots" content="index, follow, max-image-preview:large"/>

<!-- Structured data: FinancialProduct -->
<script type="application/ld+json">{ld_script}</script>

<!-- Redirect real human browsers into the SPA. Crawlers ignore this. -->
<meta http-equiv="refresh" content="0; url={_escape(spa_url)}"/>
<script>
  // JS fallback redirect (skipped by crawlers).
  try {{ window.location.replace({_json.dumps(spa_url)}); }} catch (e) {{}}
</script>

<style>
  body {{ margin:0; background:#060E1F; color:#e2e8f0;
    font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",Roboto,sans-serif;
    display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ max-width:520px; padding:32px; text-align:center; }}
  .card h1 {{ font-size:28px; margin:0 0 8px; }}
  .card p {{ font-size:15px; line-height:1.5; color:#94a3b8; margin:0 0 20px; }}
  .pill {{ display:inline-block; padding:6px 14px; border-radius:999px;
    background:rgba(249,115,22,0.15); color:#fdba74;
    border:1px solid rgba(249,115,22,0.35); font-size:12px; font-weight:700;
    letter-spacing:0.08em; text-transform:uppercase; margin-bottom:18px; }}
  a.cta {{ display:inline-block; padding:12px 24px; background:#3DE8D9;
    color:#0f172a; font-weight:700; border-radius:10px; text-decoration:none; }}
</style>
</head>
<body>
  <div class="card">
    <div class="pill">{BRAND} · AI War Room</div>
    <h1>{_escape(t)}{_escape(price_title)}</h1>
    <p>{_escape(description)}</p>
    <a class="cta" href="{_escape(spa_url)}">Open the War Room →</a>
  </div>
</body>
</html>"""


@router.head("/{ticker}")
@router.get("/{ticker}", response_class=HTMLResponse)
async def share_ticker(ticker: str, request: Request, ref: str | None = None) -> HTMLResponse:
    """Return a crawler-friendly HTML page for any ticker share link.

    Optional ``?ref=CODE`` query param is preserved through the SPA redirect
    so referral attribution works end-to-end (share → click → signup).
    """
    t = (ticker or "").strip().upper()
    # Sanitise ref: alphanumeric + dashes, max 32 chars, lowercase-prefixed `share-*`
    # codes or raw referral codes both accepted.
    safe_ref: str | None = None
    if ref:
        r = ref.strip()
        if 1 <= len(r) <= 32 and all(c.isalnum() or c == "-" for c in r):
            safe_ref = r
    if not t or not t.isalnum() or len(t) > 8:
        # Minimal safe fallback — still valid OG for brand root.
        return HTMLResponse(_build_html("RSDU", None, request, safe_ref), status_code=200)
    try:
        quote = await get_quote(t)
    except Exception as e:  # pragma: no cover — log & continue with no quote
        logger.warning(f"share: quote fetch failed for {t}: {e}")
        quote = None
    headers = {
        "Cache-Control": "public, max-age=120, stale-while-revalidate=600",
        "X-Robots-Tag": "index, follow",
        "X-Share-Generated-At": datetime.now(timezone.utc).isoformat(),
    }
    return HTMLResponse(_build_html(t, quote, request, safe_ref), status_code=200, headers=headers)
