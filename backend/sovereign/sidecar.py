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
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# Allow `python sidecar.py` without installing — same dir imports.
sys.path.insert(0, str(Path(__file__).parent))

from local_state import LocalState  # noqa: E402
from mc_client import MCClient, MCClientError  # noqa: E402
from wild_adaptive_core_v2 import (  # noqa: E402
    asdict,
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


# DOCTRINE V3 (2026-05-13) — local trade-authorization gates removed.
# RISEDUAL is a headless brain; Mission Control's Executor seat owns
# execution, and broker keys live only on that host. The wire-level
# field ``live_trading_enabled`` is still serialized as ``False`` to
# every contribution payload (MC's API schema requires it), but no
# local code path can flip it.


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

        # ── 2026-05-14 freeze-defence wiring ─────────────────────────
        # Track the last successful tick wall-time so a watchdog thread
        # can hard-exit the process if the main loop wedges in a
        # syscall (the supervisor sees the process as RUNNING in that
        # case; autorestart never fires). Hard-exit → supervisor
        # respawn within `startsecs`.
        self._last_tick_at = time.time()
        # Independent heartbeat client so the heartbeat path can never
        # be starved by a hung contribution POST sharing the same
        # connection pool. (Distinct httpx.Client = distinct pool.)
        self._hb_client = MCClient(
            base_url=mc_base_url, brain=brain, runtime_token=runtime_token,
        )
        # External liveness file path — see MC's 2026-05-14 hardening
        # note, Fix #3. Touched after every successful tick; an
        # external supervisor program kills us if it goes stale past
        # 2× interval.
        self._liveness_file = Path(
            os.environ.get("SOVEREIGN_LIVENESS_FILE")
            or f"/tmp/{brain}_alive"
        )

    # ──────────────────────── heartbeat thread ────────────────────────

    def _heartbeat_loop(self, interval_seconds: int, stop: threading.Event) -> None:
        """Independent heartbeat publisher. Runs every ``interval_seconds``
        regardless of whether the main tick is healthy or wedged. This
        is the change MC asked for — single shared loop → SPOF for
        heartbeat + contribution + intent publisher."""
        logger.info("heartbeat thread starting: interval=%ds", interval_seconds)
        while not stop.is_set():
            try:
                self._hb_client.heartbeat()
            except MCClientError as e:
                logger.warning("heartbeat (thread) failed: %s", e)
            except Exception as e:  # noqa: BLE001
                logger.exception("heartbeat thread unexpected: %s", e)
            stop.wait(interval_seconds)
        logger.info("heartbeat thread stopped")

    # ──────────────────────── watchdog thread ────────────────────────

    def _watchdog_loop(self, max_stale_seconds: int, stop: threading.Event) -> None:
        """If the main tick hasn't completed in ``max_stale_seconds``,
        the loop is wedged (silent freeze — see 2026-05-14 RCA). Hard
        exit so supervisor respawns us. ``os._exit`` skips all atexit
        + thread joins by design: a wedged thread won't release
        gracefully."""
        logger.info("watchdog thread starting: max_stale=%ds", max_stale_seconds)
        while not stop.is_set():
            stale = time.time() - self._last_tick_at
            if stale > max_stale_seconds:
                logger.error(
                    "WATCHDOG: main loop wedged for %.1fs (limit %ds) — "
                    "hard-exiting so supervisor can respawn",
                    stale, max_stale_seconds,
                )
                os._exit(2)
            stop.wait(5)

    # ──────────────────────── one tick ────────────────────────

    def tick(self) -> None:
        contributed = False

        # ── 2026-05-22: drain the outcome inbox FIRST so any
        # backend-closed paper trades populate _outcomes before
        # the contribution-emit check below. The bridge is best-
        # effort — if Mongo is down the drainer returns [] and
        # the tick proceeds normally (just won't post a
        # contribution under the empty-payload doctrine).
        try:
            from outcome_inbox_client import drain_pending_for_brain_sync
            drained = drain_pending_for_brain_sync(self.brain, limit=20)
            logger.info(
                "outcome_inbox_drain brain=%s found=%d",
                self.brain, len(drained),
            )
            if drained:
                for row in drained:
                    try:
                        self.state.add_outcome(
                            symbol=row.get("symbol", ""),
                            action=row.get("action", "BUY"),
                            confidence=float(row.get("confidence", 0.0)),
                            outcome=int(row.get("outcome", 0)),
                            resolved_at=str(row.get("resolved_at") or ""),
                            notional=float(row.get("notional", 0.0)),
                            # 2026-05-22 (Gap 2): forward provenance
                            # so MC sees the audit lineage. add_outcome
                            # treats them as optional; missing values
                            # pass through cleanly.
                            sovereign_decision_id=(
                                row.get("sovereign_decision_id") or None
                            ),
                            prediction_id=(
                                row.get("prediction_id") or None
                            ),
                            source_signal=(
                                row.get("source_signal") or None
                            ),
                        )
                    except Exception as _add_exc:  # noqa: BLE001
                        logger.warning(
                            "outcome_add_failed trade_id=%s err=%s",
                            row.get("trade_id"), _add_exc,
                        )
                self.state.save()
                logger.info(
                    "outcomes_ingested brain=%s n=%d outcomes_total=%d",
                    self.brain, len(drained), len(self.state._outcomes),
                )
        except Exception as _drain_exc:  # noqa: BLE001
            logger.warning("outcome drain failed (non-fatal): %s", _drain_exc)

        for symbol in self.symbols:
            top = self.read_top(symbol)
            decision = run_adaptive_core(
                top, self.state.weights, account_size=0.0,
            )
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
        #
        # 2026-05-22 (operator decree): refuse to emit empty
        # contributions. The screenshot of MC's diagnostics showed
        # 60 consecutive ALPHA `SOV-AUDIT contribution • as executor
        # • (empty payload)` rows because `recent_outcomes` was an
        # empty list every cycle (nothing populates _outcomes in
        # LocalState — the writer side is missing). MC treated each
        # empty contribution as a "skeleton row — engine not
        # emitting substance."
        #
        # Rule per operator: if no symbol/side/conf is available,
        # emit ABSTAIN explicitly or emit NOTHING. Never an empty
        # executor contribution.
        recent = self.state.recent_outcomes(20)
        if not recent:
            logger.warning(
                "ALPHA_ABSTAIN_CONTRIBUTION brain=%s reason=no_recent_outcomes "
                "decisions_on_disk=%d outcomes_on_disk=%d "
                "(skipping post — refusing to ship empty payload)",
                self.brain,
                len(self.state._decisions),
                len(self.state._outcomes),
            )
        else:
            try:
                self.client.post_contribution(
                    mode=self.state.mode,
                    weights=self.state.weights,
                    learning_rate=self.state.learning_rate,
                    recent_outcomes=recent,
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

        # Heartbeat is now published by the independent thread launched
        # in run_forever() — see 2026-05-14 freeze RCA. We deliberately
        # do NOT call self.client.heartbeat() here: a hung contribution
        # POST would otherwise starve the heartbeat path on the same
        # connection pool, which is exactly the failure mode we saw.

        # Persist after each tick so a crash doesn't lose decisions.
        self.state.save()
        if contributed:
            logger.info(
                "tick complete: mode=%s weights=%s lr=%.3f",
                self.state.mode, self.state.weights, self.state.learning_rate,
            )
        # Watchdog timestamp — only stamp on a tick that actually
        # reached the end. A partial tick (e.g. contribution hung) will
        # leave this stale and the watchdog will respawn us.
        self._last_tick_at = time.time()
        # External liveness file — MC's belt-and-suspenders watchdog
        # (2026-05-14 hardening note). An external supervisor program
        # checks this file's mtime every 30s and pkills us if it's
        # stale past 120s. Catches the GIL-deadlock / fork-in-thread
        # class of freeze that our in-process watchdog thread can't
        # observe (because the same GIL stall blocks it too).
        try:
            self._liveness_file.touch()
        except OSError as e:
            logger.warning("liveness file touch failed: %s", e)

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
        # Spawn the heartbeat + watchdog threads. Both are daemons —
        # they die when the main process exits (intended).
        stop_evt = threading.Event()
        # Heartbeat cadence is independent of tick cadence on purpose:
        # 30s gives MC's <60s staleness threshold a safety margin of
        # exactly one missed beat.
        hb_thread = threading.Thread(
            target=self._heartbeat_loop, args=(30, stop_evt),
            name="sidecar-heartbeat", daemon=True,
        )
        # Watchdog: if a tick hasn't completed in 2× interval, we're
        # wedged. 120s for interval=60s is plenty of headroom.
        wd_thread = threading.Thread(
            target=self._watchdog_loop, args=(max(interval_seconds * 2, 120), stop_evt),
            name="sidecar-watchdog", daemon=True,
        )
        hb_thread.start()
        wd_thread.start()

        try:
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001
                    logger.exception("tick failed; will retry: %s", e)
                time.sleep(interval_seconds)
        finally:
            stop_evt.set()


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
