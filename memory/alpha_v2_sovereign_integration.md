# Alpha V2 × Sovereign shadow integration (2026-06)

Status: SHADOW-ONLY. Worker OFF, Sovereign enforce OFF, live autonomy OFF.
No deploy. No live arming performed by this work.

## 1. Kernel reference vs Alpha's existing code — drift audit

`RISE_AI_KERNEL (1)-1.py` is a **single-file documentation artifact** (it says so
itself, lines 5-22). It is NOT a server and NOT an inventory of deployed
services. Boxes 1-7 (Safety Governor, Memory, Tool Router, Model Adapter,
Agent Council, Audit Ledger, Execution Gate) are conceptual.

Mapping of each kernel doctrine pin to what already exists in Alpha:

| Kernel pin | Alpha status | Where |
|---|---|---|
| LLM output ADVISORY_ONLY; brains advise, gates execute | HELD | `sovereign/intent_bridge.py` stamps OBSERVE_ONLY / advisory; Core v2 owns execution |
| HOLD cannot be promoted to a trade | HELD | Core v2 `_process` DECIDE floor; new gate maps HOLD→block only under enforce |
| Opponent can VETO, council can MODULATE only | PARTIAL | `wild_adaptive_core_v2.run_adaptive_core` is single-model, not council; veto = HOLD |
| Memory provenance strict (VE/SO/DI/UV); only VE trainable | PARTIAL | `LocalState.outcomes` are broker-reconciled (VE-like) via `sovereign_outcome_bridge`; no explicit VE/SO/DI/UV tag column yet |
| Provider role ≠ execution authority | HELD | `alpha_core_v2/autonomy.py` (`ProviderRole` vs `Authority`) |
| Controller never bypasses Core v2 gates | HELD | gate only *vetoes*; it cannot submit; engine keeps account/quote/size/idempotency/reconcile |
| PRIMARY provider never grants trading authority | HELD | `AutonomyState.execute_live()` ignores provider role |
| Distinct states, never aliased to one shadow flag | HELD | `Authority{OBSERVE,SHADOW,TOEHOLD,AUTONOMOUS,HALT}` separate from provider role |
| OFF/HALT revokes new entries; open positions still need reconcile + protection policy | NOW ADDRESSED | `autonomy.tick()` reconciles even under HALT; new `exit_policy.py` supplies the protection policy (OFF by default) |

Divergence to note: the patch's `sovereign.py` implements a *new* multi-model
council (Strategist/Regime/Options/Catalyst) that requires options-flow, news,
smart-money and R:R features Alpha does not currently produce. Running it as the
live gate would (a) HOLD on everything for lack of inputs and (b) constitute a
**second shadow brain** parallel to the existing `wild_adaptive_core_v2` +
`LocalState` learner. Per operator instruction we therefore wire the **existing**
shadow as the active gate and keep `sovereign.py` behind
`ALPHA_SOVEREIGN_MODEL=rise` for the eventual RISE-primary phase.

## 2. What was integrated (this work)

- Landed patch files: `sovereign.py`, `sovereign_bridge.py` (RISE reference,
  gated off), plus the additive `engine.py` sovereign hook and the
  `autonomy.py` reconcile-before-tick change.
- `sovereign_shadow.py` (NEW): the ACTIVE gate. Builds real features from
  `market_data_pool` (quote + `get_technical_indicators` + daily volume),
  loads the brain's learned weights from the EXISTING `LocalState`
  (`/app/data/sovereign/alpha/state.json`), runs the EXISTING
  `wild_adaptive_core_v2.run_adaptive_core`, and records the decision back into
  that SAME `LocalState` decision log. One shadow, not two. No fabricated data:
  missing technicals collapse to a low-confidence HOLD.
- `exit_policy.py` (NEW): deterministic stop / take-profit / trailing-stop /
  max-hold exit policy + a runner that flattens via the engine's
  broker-authoritative `close_position`. OFF unless `ALPHA_V2_EXIT_POLICY=1`.
- `worker.py` (NEW): scheduled loop, single-worker cross-process lease
  (`/tmp/alpha_v2_worker.lock`), uses the shadow gate by default. OFF unless
  `ALPHA_AUTONOMY_WORKER=1`.
- `receipts.py`: added `last_entry_price(symbol)` for the exit policy.
- `server.py`: lifespan start/stop wiring for the worker, guarded by the lease
  and the OFF-by-default flag.

## 3. Lifecycle: proven vs unverified

PROVEN (unit-tested, no broker/live):
- Sovereign hook advisory HOLD → candidate still flows through Core v2.
- Sovereign hook enforced HOLD (enforce AND live) → candidate BLOCKED at DECIDE.
- Shadow gate maps real market features → run_adaptive_core → BUY/HOLD and
  writes the decision to the EXISTING LocalState (not a new store).
- Exit policy decisions: stop, target, trailing, max-hold, and "hold".
- Worker lease: a second worker in the same/other process no-ops.
- Worker OFF by default → `run_worker` returns immediately.
- autonomy.tick() reconciles before every cycle incl. HALT.

UNVERIFIED (needs live broker + market hours + operator arming):
- Real entry → ACK → fill → position-reconcile canary (still the Monday RTH item).
- Exit policy against a real open Public position (needs a live fill first).
- Shadow learning loop end-to-end (decision → real outcome → weight update)
  in production against live fills.
- 24/5 session-aware freshness (still strict 15s).

## 4. Flags required to ACTIVATE (all currently OFF)

Shadow observation (safe, no orders):
- `ALPHA_AUTONOMY_WORKER=1` — start the scheduled worker (dry cycles under OBSERVE).

Autonomous exit protection (needs a live position to matter):
- `ALPHA_V2_EXIT_POLICY=1` — enable the exit runner in the worker.
- tune: `ALPHA_V2_STOP_PCT`, `ALPHA_V2_TAKE_PROFIT_PCT`, `ALPHA_V2_TRAIL_PCT`,
  `ALPHA_V2_MAX_HOLD_S`.

Live autonomous entries (do NOT set until canary + shadow review done):
- `ALPHA_AUTONOMY_AUTHORITY=toehold` (or `autonomous`)
- `ALPHA_AUTONOMY_LIVE=1`
- `ALPHA_CORE_V2=1`
- `ALPHA_SOVEREIGN_ENFORCE=1` — only to let the shadow gate VETO live entries.

## 5. Verified findings (this session)

Persistence of the existing shadow state (`/app/data/sovereign/alpha/state.json`):
- CONFIRMED persistent. `LocalState.save()` uses an atomic temp-file + `os.replace`
  rename, and `/app/data` is on the container filesystem that survives supervisor
  restarts (only excluded from git, which does not affect runtime). File was
  actively updated (mtime moved during this session) and reloads intact.

Weight provenance (operator's "verify learned weights came from intended outcomes"):
- The persisted weights are still Alpha's DEFAULT identity weights
  `{trend: 0.85, macd: 0.65, rsi: -0.25}` — i.e. NOT meaningfully diverged from
  defaults despite 200 recorded decisions + 50 outcomes.
- Recorded outcomes are dominated by CRYPTO paper trades (BTC/ETH/SOL/XRP/LINK/
  AVAX/BNB) with only a few equities (e.g. AAPL). So today's shadow "learning"
  reflects the crypto paper sidecar, NOT a learned EQUITY edge.
- Implication: the shadow gate applied to EQUITY candidates currently behaves as
  Alpha's *default trend-follower prior*, not a trained equity model. This is
  acceptable for advisory shadow observation but MUST be reviewed before
  `ALPHA_SOVEREIGN_ENFORCE=1` is ever set on live equity entries.

No-clobber decision:
- The crypto sidecar rewrites `state.json` every ~60s. To avoid a cross-process
  last-writer-wins clobber of its learning loop, the V2 gate reads weights
  READ-ONLY and does NOT write decisions back into `state.json`. The shadow
  verdict is recorded on the V2 side instead (engine `[core-v2:sovereign]`
  structured log per candidate). This keeps ONE shadow brain while protecting
  the existing learner's state. Verified by `test_load_weights_reads_existing_
  shadow_readonly` (file bytes + mtime unchanged after a gate call).

Neither provider role nor a learned weight grants order authority:
- `AutonomyState.execute_live()` ignores provider role entirely (unit-tested).
- `ShadowGate` can only return a veto/proceed proposal; the engine honors a veto
  ONLY when `sovereign_enforce AND live`, and can never itself submit an order.

## 6. Test results (all green, no broker/live)

`tests/test_alpha_autonomy.py` — reconcile-before-tick incl. HALT; live only when
fully armed; provider PRIMARY never grants authority.
`tests/test_alpha_v2_sovereign_shadow.py` — advisory HOLD flows; enforced HOLD
blocks only when live; verdict mapping; read-only shadow state.
`tests/test_alpha_v2_exit_policy.py` — stop/target/trail/max-hold/hold/no-anchor
+ runner disabled-by-default and force-exit.
`tests/test_alpha_v2_worker_lease.py` — lease acquire/renew/release, fresh
foreign lease blocks, stale reclaim, worker OFF no-op.
Plus existing `test_alpha_core_v2.py` / `test_alpha_core_v2_review_fixes.py`
regression. Total: 58 passed.

Backend boots clean with the new lifespan wiring; log shows
`[alpha-v2-worker] startup: disabled (ALPHA_AUTONOMY_WORKER unset)` — nothing
armed, no runtime behavior change.

