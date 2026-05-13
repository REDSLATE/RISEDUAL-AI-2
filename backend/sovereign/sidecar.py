"""Sovereign sidecar runner — glues the deterministic core, local
state, and the MC client.

Doctrine:
    The brain host imports this and runs it as a long-lived process
    (`python -m runtime_patch_kit.sovereign.sidecar --brain alpha
    --mode DTD`). The runner:

      1. Loads / creates `LocalState` on disk.
      2. For each iteration:
         a. Reads a top-of-book snapshot (caller-supplied function in
            production; a stub in this template — replace with broker
            feed).
         b. Runs `wild_adaptive_core_v2.run_adaptive_core(...)`.
         c. Persists the decision locally; if DTD mode and the
            decision is resolved, applies `update_weights(...)`.
         d. POSTs a stance to MC (if the brain wants to commit to an
            open position) + a contribution snapshot (always).
      3. Sleeps `--interval` seconds and repeats.

    Three locks for one door — `LIVE_TRADING_ENABLED` is reasserted
    False here so even if a brain's local copy of `wild_adaptive_core_v2.py`
    is patched, the sidecar still refuses to call execute_trade with a
    live broker. MC's API is the third and final lock.

    This template intentionally has NO broker integration. Production
    brain hosts replace the `_read_top_of_book` stub with their own
    market-data poller (Kraken WebSocket, TOS bars, Public.com REST, …).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

# Allow `python sidecar.py` without installing — same dir imports.
sys.path.insert(0, str(Path(__file__).parent))

from local_state import LocalState  # noqa: E402
from mc_client import MCClient, MCClientError  # noqa: E402
from wild_adaptive_core_v2 import (  # noqa: E402
    LIVE_TRADING_ENABLED,
    asdict,
    assert_safe_action,
    default_weights,
    map_action_to_stance,
    run_adaptive_core,
    update_weights,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("sovereign.sidecar")


# Doctrine assertion — three-lock door. The core module-level flag must
# be False; if a downstream patcher flips it, the sidecar refuses to start.
def _assert_doctrine() -> None:
    if LIVE_TRADING_ENABLED:
        raise RuntimeError(
            "DOCTRINE VIOLATION: wild_adaptive_core_v2.LIVE_TRADING_ENABLED "
            "is True. Sovereign sidecar refuses to start. Phase 1 is "
            "observation-only — there is no scenario where this flag "
            "should be True at the brain layer."
        )


# Default top-of-book reader — production replaces this. The stub
# returns synthetic features so the sidecar can dry-run on a brain host
# with no broker feed.
def _stub_top_of_book(symbol: str) -> dict:
    import math
    t = time.time()
    return {
        "symbol": symbol,
        "price": 100.0 + math.sin(t / 60) * 5,
        "technicals": {
            "sma20": 100.0,
            "macd": math.sin(t / 30) * 0.5,
            "rsi14": 50 + math.cos(t / 45) * 15,
        },
    }


class SovereignSidecar:
    def __init__(self, *, brain: str, mode: str, mc_base_url: str,
                 runtime_token: str, symbols: list[str],
                 state_path: Optional[Path] = None,
                 top_of_book_fn: Optional[Callable[[str], dict]] = None,
                 active_position_resolver: Optional[Callable[[str], Optional[str]]] = None):
        _assert_doctrine()
        self.brain = brain
        self.state = LocalState(brain=brain, path=state_path, mode=mode)
        # Seed weights from defaults if local file is fresh.
        if not self.state.weights:
            self.state.set_weights(default_weights())
            self.state.save()
        self.client = MCClient(
            base_url=mc_base_url, brain=brain, runtime_token=runtime_token,
        )
        self.symbols = symbols
        self.read_top = top_of_book_fn or _stub_top_of_book
        # Optional: maps a symbol to the open position_id MC has for it.
        # Production brain hosts wire this to a small GET against
        # `/api/shared/positions?symbol=...`. Returning None ⇒ no open
        # position; the brain still ships a contribution snapshot but
        # no stance.
        self.resolve_position = active_position_resolver

    # ──────────────────────── one tick ────────────────────────

    def tick(self) -> None:
        contributed = False
        for symbol in self.symbols:
            top = self.read_top(symbol)
            decision = run_adaptive_core(
                top, self.state.weights, account_size=0.0,
            )
            assert_safe_action(decision.action)
            self.state.append_decision(asdict(decision))

            # Stance posting — only if there's an open position to vote on.
            pos_id = self.resolve_position(symbol) if self.resolve_position else None
            if pos_id:
                stance = map_action_to_stance(decision.action)
                try:
                    self.client.post_stance(
                        position_id=pos_id, stance=stance,
                        confidence=decision.confidence,
                        notes=f"sovereign-core auto stance for {symbol}",
                        memory_sources=["sovereign.weights_snapshot"],
                        confidence_origin=decision.confidence_origin,
                    )
                    logger.info(
                        "stance posted: %s %s c=%.3f pos=%s",
                        symbol, stance, decision.confidence, pos_id,
                    )
                except MCClientError as e:
                    # 4xx → likely a doctrine rejection; don't retry.
                    # 5xx → MC hiccup; logged, retried next tick.
                    logger.warning("stance failed: %s", e)

        # Contribution snapshot — once per tick, summarises the brain.
        try:
            self.client.post_contribution(
                mode=self.state.mode,
                weights=self.state.weights,
                learning_rate=self.state.learning_rate,
                recent_outcomes=self.state.recent_outcomes(20),
                # Conservative: this template never asks for a confidence
                # nudge. Brains that want one set training_signal=True
                # (DTD only) and a non-zero delta on their own logic.
                confidence_delta=0.0,
                delta_reason="",
                training_signal=False,
                notes=f"tick @ {time.time():.0f}",
            )
            contributed = True
        except MCClientError as e:
            logger.warning("contribution failed: %s", e)

        # Heartbeat is best-effort.
        try:
            self.client.heartbeat()
        except MCClientError as e:
            logger.debug("heartbeat failed (non-fatal): %s", e)

        # Persist after each tick so a crash doesn't lose decisions.
        self.state.save()
        if contributed:
            logger.info(
                "tick complete: mode=%s weights=%s lr=%.3f",
                self.state.mode, self.state.weights, self.state.learning_rate,
            )

    # ──────────────────────── retrain (DTD only) ────────────────────────

    def apply_outcome(self, decision: dict, outcome: int) -> None:
        """Operator-facing hook: when a decision resolves, apply
        update_weights. PRD mode REFUSES — only DTD-mode brains learn."""
        if self.state.mode != "DTD":
            raise RuntimeError(
                f"refusing to retrain in {self.state.mode} mode; "
                "switch to DTD for replay training"
            )
        if outcome not in (-1, 0, 1):
            raise ValueError(f"outcome must be -1/0/+1, got {outcome!r}")
        new_w = update_weights(
            self.state.weights, decision.get("features") or {}, outcome,
            lr=self.state.learning_rate,
        )
        self.state.set_weights(new_w)
        self.state.save()

    # ──────────────────────── main loop ────────────────────────

    def run_forever(self, interval_seconds: int = 60) -> None:
        logger.info(
            "sovereign sidecar starting: brain=%s mode=%s symbols=%s interval=%ds",
            self.brain, self.state.mode, self.symbols, interval_seconds,
        )
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                logger.exception("tick failed; will retry: %s", e)
            time.sleep(interval_seconds)


def _build_from_argv() -> SovereignSidecar:
    p = argparse.ArgumentParser(description="RISEDUAL Sovereign Sidecar")
    p.add_argument("--brain", required=True,
                   choices=["alpha", "camaro", "chevelle", "redeye"])
    p.add_argument("--mode", default="DTD", choices=["DTD", "PRD"])
    p.add_argument("--mc-url", default=os.environ.get("MC_BASE_URL", ""))
    p.add_argument("--symbols", nargs="+", default=["BTC/USD"])
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--state-path", default=None)
    args = p.parse_args()

    token = os.environ.get(f"{args.brain.upper()}_INGEST_TOKEN")
    if not token:
        raise SystemExit(
            f"missing env var {args.brain.upper()}_INGEST_TOKEN — "
            "see README.md for required envs."
        )
    if not args.mc_url:
        raise SystemExit(
            "missing --mc-url (or MC_BASE_URL env var). Example: "
            "https://mc.risedual.io"
        )

    return SovereignSidecar(
        brain=args.brain, mode=args.mode, mc_base_url=args.mc_url,
        runtime_token=token, symbols=args.symbols,
        state_path=Path(args.state_path) if args.state_path else None,
    )


if __name__ == "__main__":
    sidecar = _build_from_argv()
    sys.exit(sidecar.run_forever() or 0)
