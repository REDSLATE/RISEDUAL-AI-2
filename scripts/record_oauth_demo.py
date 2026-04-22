"""Record the public ?demo=oauth walkthrough as a landing-page video.

Flow captured (matches what user requested):
  1. Arrival on landing (?demo=oauth route → StepLanding)
  2. Sign-in → StepDashboard (account info + disclaimers banner)
  3. Open Settings → StepBrokerConnect (broker catalog)
  4. Start → StepDisclosure (terms + ALLOW)
  5. Allow → StepAlpacaAuth (broker-side authorize)
  6. Authorize → StepSuccess (bot-release + account info summary)

Output: /tmp/risedual_demo.webm (Playwright native) and, if ffmpeg is
available, /tmp/risedual_demo.mp4 (the format the landing <video>
element expects).

Usage:
  python3 /app/scripts/record_oauth_demo.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

OUT_DIR = Path("/tmp/risedual_demo_recording")
OUT_DIR.mkdir(exist_ok=True)


def _frontend_url() -> str:
    """Read REACT_APP_BACKEND_URL from frontend/.env. That's the
    ingress URL the browser is supposed to hit — same one the real
    `<video>` element fetches through."""
    env = Path("/app/frontend/.env")
    for line in env.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("REACT_APP_BACKEND_URL not found in frontend/.env")


async def record() -> Path:
    base = _frontend_url()
    print(f"[record] base URL: {base}")

    async with async_playwright() as pw:
        # Headful-style viewport for a clean 16:9 frame. Playwright records
        # at the viewport size; the <video> element is aspect-video so
        # 1280×720 renders crisp without being huge on disk.
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(OUT_DIR),
            record_video_size={"width": 1280, "height": 720},
        )
        page = await ctx.new_page()

        # 1. Arrive on ?demo=oauth (StepLanding)
        await page.goto(f"{base}/?demo=oauth", wait_until="networkidle")
        await page.wait_for_timeout(2500)

        async def click_and_pause(testid: str, pause_ms: int = 1800):
            try:
                await page.click(f'[data-testid="{testid}"]', timeout=8000)
                await page.wait_for_timeout(pause_ms)
            except Exception as e:
                print(f"[record] ⚠️  could not click {testid}: {e}")

        # 2. StepLanding → Sign In → StepDashboard
        await click_and_pause("demo-sign-in", 2500)

        # Let viewer read the dashboard + disclaimer band.
        await page.wait_for_timeout(2000)

        # 3. StepDashboard → Open Settings → StepBrokerConnect
        await click_and_pause("demo-go-settings", 2500)

        # 4. StepBrokerConnect → Start → StepDisclosure
        await click_and_pause("demo-oauth-start", 2500)

        # 5. StepDisclosure → ALLOW → StepAlpacaAuth
        await click_and_pause("demo-disclosure-allow", 2500)

        # 6. StepAlpacaAuth authorize → StepSuccess (bot release panel)
        # The Alpaca step uses its own button without a testid in the
        # patch we grepped — fall back to visible-text click if the
        # primary testid isn't present.
        try:
            await page.click('button:has-text("Authorize")', timeout=6000)
        except Exception:
            try:
                await page.click('button:has-text("Allow")', timeout=4000)
            except Exception as e:
                print(f"[record] ⚠️  could not click Authorize: {e}")
        await page.wait_for_timeout(3500)

        # Linger on success so the viewer sees the bot-release + account
        # summary. This is the "completion" beat the user asked for.
        await page.wait_for_timeout(4000)

        # Graceful close so the .webm finalises with a proper moov atom.
        await ctx.close()
        await browser.close()

    # Playwright writes the video asynchronously; the file appears after
    # `ctx.close()`. Pick the newest .webm.
    videos = sorted(OUT_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not videos:
        raise RuntimeError("Playwright did not produce a video file")
    out = videos[-1]
    print(f"[record] webm → {out} ({out.stat().st_size / 1024:.1f} KB)")
    return out


def transcode_to_mp4(webm: Path) -> Path | None:
    """Transcode to MP4 if ffmpeg is on PATH — the landing <video>
    element is served as-is, and MP4/H.264 has the widest browser +
    iOS compatibility. Returns None if ffmpeg isn't available (user
    can still upload the .webm directly; modern browsers play it)."""
    if shutil.which("ffmpeg") is None:
        print("[record] ffmpeg not installed; skipping .mp4 transcode")
        return None
    mp4 = webm.with_suffix(".mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(webm),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-an",  # no audio track
        str(mp4),
    ]
    print(f"[record] transcoding → {mp4}")
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"[record] mp4  → {mp4} ({mp4.stat().st_size / 1024:.1f} KB)")
    return mp4


def main() -> None:
    webm = asyncio.run(record())
    mp4 = transcode_to_mp4(webm)
    print("\n=== RECORDING COMPLETE ===")
    print(f"webm: {webm}")
    if mp4:
        print(f"mp4 : {mp4}")
    print("\nUpload via: Admin → Media Manager → Category 'Landing Page' → Upload")


if __name__ == "__main__":
    main()
