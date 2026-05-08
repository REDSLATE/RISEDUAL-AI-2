"""Dynamic OpenGraph PNG generator for social share cards.

`GET /api/share/img/{ticker}.png` returns a 1200×630 PNG composited on-the-fly
with the ticker, live price, % change, and RISEDUAL branding. Caches the
rendered image in-memory for 60s so crawler hammering is cheap.

Designed to be embedded in `<meta property="og:image">` for X/Twitter,
Facebook, LinkedIn, WhatsApp, Slack, Discord, iMessage, etc. — every platform
that respects OG tags will render the custom card inline.
"""
from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Response
from PIL import Image, ImageDraw, ImageFont

from services.price_provider import get_quote

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/share/img", tags=["share"])

# Brand palette — matches frontend tailwind config.
BG_TOP = (6, 14, 31)      # #060E1F
BG_BOTTOM = (11, 20, 38)  # #0B1426
TEAL = (61, 232, 217)     # #3DE8D9
ORANGE = (251, 146, 60)   # orange-400
LIME = (163, 230, 53)     # lime-400
RED = (248, 113, 113)     # red-400
WHITE = (248, 250, 252)
SLATE = (148, 163, 184)
SLATE_DIM = (100, 116, 139)

# Canonical OG size — works everywhere (X wants 2:1, FB wants 1.91:1; 1200×630 covers both).
W, H = 1200, 630

# Font file hunt — Liberation ships with every Debian slim image.
FONT_DIR = Path("/usr/share/fonts/truetype/liberation")
FONT_MONO_BOLD = FONT_DIR / "LiberationMono-Bold.ttf"
FONT_SANS_BOLD = FONT_DIR / "LiberationSans-Bold.ttf"
FONT_SANS = FONT_DIR / "LiberationSans-Regular.ttf"


def _font(path: Path, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _gradient_bg(img: Image.Image) -> None:
    """Draw a vertical gradient directly onto `img`."""
    px = img.load()
    for y in range(H):
        ratio = y / H
        r = int(BG_TOP[0] * (1 - ratio) + BG_BOTTOM[0] * ratio)
        g = int(BG_TOP[1] * (1 - ratio) + BG_BOTTOM[1] * ratio)
        b = int(BG_TOP[2] * (1 - ratio) + BG_BOTTOM[2] * ratio)
        for x in range(W):
            px[x, y] = (r, g, b)


def _accent_lines(draw: ImageDraw.ImageDraw) -> None:
    """Subtle teal accent bars on the edges."""
    draw.rectangle([(0, 0), (W, 6)], fill=TEAL)
    draw.rectangle([(0, H - 6), (W, H)], fill=TEAL)


def _render(ticker: str, quote: dict | None) -> bytes:
    img = Image.new("RGB", (W, H), BG_TOP)
    _gradient_bg(img)
    draw = ImageDraw.Draw(img)
    _accent_lines(draw)

    # ── Brand strip ───────────────────────────────────────────────────────
    brand_font = _font(FONT_SANS_BOLD, 30)
    tag_font = _font(FONT_SANS, 22)
    draw.text((70, 60), "RISEDUAL AI", font=brand_font, fill=TEAL)
    draw.text((70, 100), "Adversarial AI Trading · War Room", font=tag_font, fill=SLATE)

    # ── Ticker (big, monospace) ───────────────────────────────────────────
    ticker_font = _font(FONT_MONO_BOLD, 200)
    draw.text((70, 170), ticker, font=ticker_font, fill=WHITE)

    # ── Price + change ────────────────────────────────────────────────────
    if quote and quote.get("price"):
        price = quote["price"]
        change_pct = quote.get("changePercent") or quote.get("change_pct") or 0
        up = change_pct >= 0
        arrow = "▲" if up else "▼"
        color = LIME if up else RED

        price_font = _font(FONT_MONO_BOLD, 72)
        chg_font = _font(FONT_MONO_BOLD, 48)
        draw.text((70, 410), f"${price:,.2f}", font=price_font, fill=WHITE)
        chg_text = f"{arrow} {abs(change_pct):.2f}%"
        draw.text((70, 495), chg_text, font=chg_font, fill=color)
    else:
        # No quote (closed / missing) — still show branding cleanly.
        sub_font = _font(FONT_SANS_BOLD, 40)
        draw.text((70, 420), "AI War Room · Multi-Model Consensus", font=sub_font, fill=TEAL)

    # ── Right-side call-out ───────────────────────────────────────────────
    cta_font = _font(FONT_SANS_BOLD, 28)
    cta_small = _font(FONT_SANS, 22)
    bbox = draw.textbbox((0, 0), "Open War Room →", font=cta_font)
    cta_w = bbox[2] - bbox[0]
    cta_x = W - cta_w - 70
    # Pill background
    pad_x, pad_y = 22, 14
    draw.rounded_rectangle(
        [(cta_x - pad_x, 510 - pad_y), (cta_x + cta_w + pad_x, 510 + 34 + pad_y)],
        radius=24,
        fill=TEAL,
    )
    draw.text((cta_x, 510), "Open War Room →", font=cta_font, fill=(15, 23, 42))

    # Platform badge (top-right).
    badge = "Adversarial · Prediction · Intelligence"
    bbox = draw.textbbox((0, 0), badge, font=cta_small)
    bw = bbox[2] - bbox[0]
    draw.text((W - bw - 70, 70), badge, font=cta_small, fill=SLATE_DIM)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── In-memory cache (simple, bounded) ───────────────────────────────────────
_CACHE: dict[str, tuple[float, bytes]] = {}
_CACHE_TTL = 60.0  # seconds
_CACHE_MAX = 256


def _cached(ticker: str, quote: dict | None) -> bytes:
    # Key includes price to avoid serving stale price card after a big move.
    price_bucket = None
    if quote and quote.get("price"):
        # Bucket by 0.5% so tiny ticks don't bust cache.
        price_bucket = round(quote["price"] * 200)
    key = f"{ticker}:{price_bucket}"
    now = time.time()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    png = _render(ticker, quote)
    # Bound cache size — drop oldest if over.
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])
        _CACHE.pop(oldest[0], None)
    _CACHE[key] = (now, png)
    return png


@router.head("/{ticker}.png")
@router.get("/{ticker}.png")
async def share_image(ticker: str) -> Response:
    t = (ticker or "").strip().upper()
    if not t or not t.isalnum() or len(t) > 8:
        t = "RSDU"
    try:
        quote = await get_quote(t)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"share img: quote fetch failed for {t}: {e}")
        quote = None
    png = _cached(t, quote)
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
            "X-Robots-Tag": "noindex",
        },
    )
