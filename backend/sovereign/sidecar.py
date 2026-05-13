"""Alpha sovereign sidecar — main tick loop.

    python3 -m backend.sovereign.sidecar \\
        --brain alpha --mode DTD \\
        --symbols BTC/USD ETH/USD SOL/USD \\
        --interval 60 \\
        --state-path /app/data/sovereign/alpha/state.json

Behavior:
    1. Asserts doctrine on boot (LIVE_TRADING_ENABLED must be False).
    2. Loads or initializes the local state file.
    3. Every ``interval`` seconds, POSTs a sovereign contribution + a heartbeat
       to Mission Control. Failures are logged; the loop keeps running.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

from .local_state import LocalState
from .mc_client import MCClient, MCClientError, build_contribution_body
from .wild_adaptive_core_v2 import BRAIN_NAME, assert_doctrine

log = logging.getLogger("sovereign.sidecar")

# Default initial weights for Alpha — only applied if state.json is missing.
ALPHA_INITIAL_WEIGHTS = {"trend": 0.85, "macd": 0.65, "rsi": -0.25}
ALPHA_INITIAL_LR = 0.06


def _seed_if_empty(state: LocalState) -> bool:
    """Returns True iff seeding happened (state was empty)."""
    if state.weights:
        return False
    state.set_weights(ALPHA_INITIAL_WEIGHTS)
    state.set_learning_rate(ALPHA_INITIAL_LR)
    state.save()
    log.info("seeded initial alpha weights: %s", ALPHA_INITIAL_WEIGHTS)
    return True


def _tick_notes(symbols: list[str]) -> str:
    return f"tick @ {int(time.time())} symbols={','.join(symbols)}"


async def _tick(client: MCClient, state: LocalState, symbols: list[str]) -> None:
    """One iteration of the loop. Logs failures, does not raise."""
    body = build_contribution_body(
        mode=state.mode,
        weights=state.weights,
        learning_rate=state.learning_rate,
        recent_outcomes=list(state.recent_outcomes),
        notes=_tick_notes(symbols),
        # Synthetic stub: no learning signal yet, no confidence drift.
        confidence_delta=0.0,
        delta_reason="",
        training_signal=False,
    )

    # Contribution first (the durable signal), then heartbeat.
    try:
        resp = await client.contribution(body)
        log.info(
            "contribution OK posted_as=%s seat_epoch=%s updated_at=%s",
            resp.get("posted_as"),
            resp.get("seat_epoch"),
            resp.get("updated_at"),
        )
    except MCClientError as e:
        log.warning("contribution failed: %s", e)

    try:
        hb = await client.heartbeat({"ts": datetime.now(timezone.utc).isoformat()})
        log.debug("heartbeat OK: %s", hb)
    except MCClientError as e:
        log.warning("heartbeat failed: %s", e)


async def _run(args: argparse.Namespace) -> int:
    # LOCK #2 — refuse to boot if doctrine is violated.
    assert_doctrine()

    state = LocalState(brain=args.brain, path=args.state_path, mode=args.mode)
    _seed_if_empty(state)

    base = os.environ.get("MC_BASE_URL", "").strip()
    token = os.environ.get("ALPHA_INGEST_TOKEN", "").strip()
    if not base or not token:
        log.error(
            "MC_BASE_URL and ALPHA_INGEST_TOKEN must both be set in the env. "
            "Refusing to start."
        )
        return 2

    client = MCClient(base_url=base, token=token, runtime=args.brain)

    stop = asyncio.Event()

    def _stop(*_a):
        log.info("signal received; stopping after current tick…")
        stop.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _stop)
            except (NotImplementedError, RuntimeError):
                # Windows / non-asyncio context — fall back to sync handler.
                signal.signal(sig, _stop)
    except Exception:  # noqa: BLE001
        pass

    log.info(
        "alpha sidecar starting: mode=%s symbols=%s interval=%ss state=%s",
        args.mode, args.symbols, args.interval, args.state_path,
    )

    try:
        while not stop.is_set():
            try:
                await _tick(client, state, args.symbols)
            except Exception as e:  # noqa: BLE001
                log.exception("tick crashed (loop continuing): %s", e)
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.interval)
            except asyncio.TimeoutError:
                pass
    finally:
        await client.aclose()
        log.info("alpha sidecar stopped cleanly")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Alpha sovereign sidecar")
    p.add_argument("--brain", default=BRAIN_NAME)
    p.add_argument("--mode", default="DTD", choices=["DTD", "PRD"])
    p.add_argument(
        "--symbols",
        nargs="+",
        default=["BTC/USD", "ETH/USD", "SOL/USD"],
        help="symbols Alpha is watching (cosmetic for v1; used in notes)",
    )
    p.add_argument("--interval", type=int, default=60, help="seconds between ticks")
    p.add_argument(
        "--state-path",
        default=os.environ.get(
            "SOVEREIGN_STATE_PATH", "/app/data/sovereign/alpha/state.json"
        ),
    )
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
