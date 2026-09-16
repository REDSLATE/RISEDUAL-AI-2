# RISEDUAL AI — PRD

## Latest Update — 2026-09-16 (Core v2 M1 hardening: fresh execution quote + explicit fill/position facts — FROZEN)

Two execution-correctness items added before the freeze (not feature creep):

### #4 — Fresh execution quote + re-size (Public = source of truth)
- New `ExecutionQuote(symbol, price: Decimal, timestamp, source)` contract (small; NO freshness subsystem). `PublicBroker.get_execution_quote()` uses Public's native `get_quote()`.
- Critical section reordered: lock → **idempotency** → position → risk → **QUOTE** → **re-SIZE from execution mark** → affordability invariant → submit. Quote unavailable → **FAILED `execution_quote_unavailable`**; too old (`> ALPHA_V2_QUOTE_MAX_AGE_S`, default 15s) → **BLOCKED `stale_execution_quote`**. Discovery mark answers "interesting?"; execution quote answers "what price am I buying at?" — distinct contracts.
- Sizing is recomputed from the execution mark; affordability invariant enforced: `estimated_cost = qty×exec_price ≤ spendable_bp` (guards the $1-min bump; else BLOCK `notional_exceeds_buying_power`). Quote price/source/age recorded on every receipt from QUOTE on.

### #5 — Fill vs position facts made explicit (kept deferred, as designed)
- Removed ambiguous `broker_confirmed`. Receipt now carries two distinct broker facts: `order_acknowledged` + `broker_reported_fill_qty` (fact #1, at submit) and `position_reconciled` + `reconciled_position_qty` (fact #2, established later by `reconcile_outstanding`). Store column `position_reconciled`; `outstanding_orders` = TRADED & not-yet-reconciled.

### Verified
- 16/16 v2 tests (added `execution_quote_unavailable`→FAILED, `stale_execution_quote`→BLOCKED, size-from-execution-mark; delayed-fill asserts fact#1 True / fact#2 False → reconcile sets fact#2). Full regression 63/63 (v2 + reconciler + executor + round-trip + silent-gaps). Backend healthy, routes 715, v2 route 401 unauth.
- Real-Public preview (execution OFF): exec quote AAPL $331.82 `source=public` age 869s (market closed) → correctly BLOCKED `stale_execution_quote`; discovery mark (999) ignored in favor of Public price. During RTH, liquid names quote sub-second.

### FROZEN. Next info comes from a small real Public canary, not features.
Canary note: if a thinly-traded name false-blocks on staleness during RTH (Public timestamp = last-trade time), tune `ALPHA_V2_QUOTE_MAX_AGE_S`. M1 gap remains: v2 EXIT/close (M2 prep) for a full round-trip.

---


## Latest Update — 2026-09-16 (Core v2 M1 FROZEN: concurrency/idempotency gate + pre-live validation)

Per operator: froze Milestone 1 (no new strategy/diagnostic/broker/UI features). Added the one required pre-live test and validated production-readiness in preview.

### Race/idempotency gate (the one thing added before live)
- **Per-symbol `asyncio.Lock` + in-flight idempotency key** wrap the position-check→size→submit critical section in `engine.py`. The lock alone can't stop a double-submit (both workers can read `held=False` before either fills); the in-flight key blocks the second submit until the broker reflects the position (cleared on reconcile confirm/rejection).
- Tests: two simultaneous identical candidates → **exactly 1 submission** (1 TRADED, 1 BLOCKED `concurrent_duplicate`); 8 simultaneous → 1 TRADED / 7 BLOCKED. **15/15 v2 tests green.**

### Pre-live validation (preview, real Public account, execution OFF)
Flag OFF ✓ · broker reachable ✓ · account $193.76/$173.02 ✓ · positions (6 real: PPCB/CYPH/VRPX/POET/QQQ/III) ✓ · full pipeline runs but never submits (`core_v2_disabled`) ✓ · sizing provenance on receipt (`desired→risk_cap→BP→reserve→affordable→final→qty`) ✓ · reconcile ✓ · invariant balanced ✓. Account already holds 6 ≥ max-5 → live entries correctly BLOCK at RISK (honest).

### Deploy gate (operator-driven, NOT done by agent)
1. Save to GitHub → redeploy. 2. Confirm prod runs the v2 build: `GET /api/admin/alpha-v2/health` returns 200 (404 = old build still live) + owner-auth `GET /api/admin/runtime/stamp` git_sha. 3. On prod, validate /health broker+account+position access and `POST /reconcile`. 4. Keep `ALPHA_CORE_V2=0` until all pass. 5. THEN arm canary: `ALPHA_CORE_V2=1` + one small `POST /run-cycle?live=true` during RTH. Judge by lifecycle correctness (candidate→…→receipt→reconcile), NOT P&L. M2 = discernment, and v2 EXIT/close logic (not in M1).

---


## Latest Update — 2026-09-16 (Alpha Core v2 — Milestone 1: lean broker-authoritative engine, flag-gated OFF)

### Decision
After root-causing the "total bust" as architectural debt (stale ledger + fixed sizing + veto sprawl), operator chose a controlled rebuild: **freeze Legacy, build a lean Core v2 beside it**, prove it against the known failure cases, then canary. Legacy keeps trading in production untouched.

### Milestone 1 objective (met)
> Core v2 independently discovers an opportunity, decides, dynamically sizes from the ACTUAL Public account, submits fractionally, confirms the broker result, reconciles, and accounts for every candidate exactly once — with **no dependency on Legacy's runtime state**.

### Shipped — `services/alpha_core_v2/` (new, isolated package)
- **Flag-gated OFF** — `ALPHA_CORE_V2=0` default. Own env namespace (`ALPHA_V2_*`), own SQLite store, own discovery. Does NOT touch Legacy's tick loop, ledger, env, or execution.
- **Broker = authority; Alpha SQLite = history.** Positions/account/orders/fills always come from Public. `receipts.py` NEVER answers "is a position live" — that guardrail prevents rebuilding the stale-ledger failure.
- **Lifecycle** FIND→DECIDE→RANK→RISK→ACCOUNT→POSITION→SIZE→ORDER→CONFIRM→RECONCILE (`engine.py`). Own minimal discovery (`discovery.py`: universe→snapshot→simple reclaim/strength pattern→rank) via the shared market-data pool, not Legacy's scanner.
- **Strict terminal contract**: every candidate ends as exactly one of **TRADED | BLOCKED | FAILED**. **RESIZED is NOT terminal** — it's an execution transformation recorded on the receipt. Invariant `candidates_in == traded+blocked+failed` enforced in `run_cycle` (logs ACCOUNTING VIOLATION) and tested.
- **First-class sizing provenance** (`sizing.py` → `SizePlan`): desired_notional / risk_capped_notional / buying_power / buying_power_reserve / affordable_notional / final_notional / quantity / resize_reason. `notional = min(desired ceiling, equity×alloc_pct, buying_power−reserve)`; fractional `floor` with $1-min bump. `PUBLIC`-style hard-reject only when `< $1`.
- **Public-only broker port** (`broker.py` `BrokerPort` Protocol + `PublicBroker`). MooMoo pluggable later; NOT wired in M1. Pre-ORDER broker position check is also the **double-submission guard** (if Legacy holds it, broker reports held → v2 BLOCKS duplicate). Broker-unreachable → **FAIL CLOSED** (`broker_position_unknown`, never opens blind).
- **4 minimal endpoints** (`routes/admin_alpha_v2.py`, owner-only): `GET /health`, `GET /receipts`, `POST /run-cycle?live=` (live refused unless `ALPHA_CORE_V2=1`), `POST /reconcile` (restart-safe). No dashboards/panels per directive.

### Explicitly EXCLUDED from M1 (operator list)
Dashboards, Why-Not-Trade, funnel, provenance/freshness panels, watchdog frameworks, Mongo event streams, HMM/regime + Edge Engine work, shorts, options, MooMoo execution, multi-broker routing, new strategy/pattern work.

### Tests — 13/13 green (`tests/test_alpha_core_v2.py`), full failure corpus
phantom-local-open/broker-flat (reconciles+trades), genuine broker position (dup block), broker-position lookup unavailable (fail closed), BP-below-desired (resize+trade w/ provenance), BP-below-$1 (below_minimum block), fractional rounding (never exceeds affordable), unusable quote, rejected order (FAILED), accepted+delayed fill (TRADED pending → reconcile finalizes), partial fill (remaining visible), flag-off (full pipeline, never submits), mixed-cycle accounting invariant, engine-exception still terminal. Backend healthy, routes 711→715, `/health` 401 unauth (mounted). No regressions.

### Next (per operator): DON'T add features — canary
Arm `ALPHA_CORE_V2=1`, run a deliberately small `POST /run-cycle?live=true` during RTH, verify entry→fill→position→exit→reconcile on real Public receipts, then retire Legacy. NOTE: exit path is not in M1 scope (entries + reconcile only) — closing logic is the immediate M2 item before a full round-trip canary.

⚠️ Preview-verified. Save to GitHub to redeploy. Legacy remains the production engine until v2 canary passes.

---


## Latest Update — 2026-09-15 (REGRESSION FIXED — "total bust": broker-authoritative positions + account-aware sizing)

### Root cause (regression bisect, not a redesign)
Live trades flowed through ~09-03/09-10 then stopped. Setups + intents were STILL being generated daily (09-15: 39 setups / 24 intents) — the break was purely **intent → broker**, where `broker_submitted` went to 0 after 09-10. Two stacked regressions:

1. **Ledger drifted from broker truth → `dup_open_row` self-lockout (primary).** The open_long duplicate gate trusted Alpha's local `equity_live_trades` ledger, never the broker. The exit/reconcile path had stopped writing closes, so **19 phantom `open` rows** (some since June) permanently blocked re-entry on SPY/AAPL/MSFT/NVDA/TSLA/GOOGL/META/AMZN/JPM/AVGO/etc. Read-only broker probe confirmed: of 21 "open" ledger rows, only **2 were real** (QQQ, CYPH); broker actually held CYPH/VRPX/PPCB/POET/III/QQQ. This is also the "wasn't recording/showing trades" symptom.
2. **`PUBLIC_LIVE_NOTIONAL_USD=350` on a $173 account (secondary).** Pre-trade check `if bp < notional: reject("insufficient_buying_power")` — account has equity $193.74 / BP $173.02, so every intent that wasn't already `dup_open_row`-blocked died at buying-power (24 rejects 09-14, 21 09-15). Every historical fill was $1 notional.

### Fix (operator design — broker = truth for money/positions; Alpha = truth for strategy/history)
- **New `services/alpha_position_reconciler.py`** — `reconcile_symbol` / `reconcile_all` / `reconcile_open_positions_with_broker`. Phantom `open` rows the broker no longer holds are marked `status=closed, close_reason="broker_reconciled_missing"` (timestamped, **preserved not deleted** — audit/edge history intact).
- **Broker-authoritative dup gate** (`public_equity_live_executor.py` open_long): ask Public for the live position. Held → real `dup_open_row` block. Not held → reconcile any stale row and PROCEED. Broker unreachable → **fail closed** (`broker_position_unknown`, never opens blind).
- **Account-aware sizing** — `PUBLIC_LIVE_NOTIONAL_USD` is now a CEILING, not a requirement: `notional = min(ceiling, equity×PUBLIC_LIVE_ALLOC_PCT[def 0.20], buying_power − PUBLIC_LIVE_CASH_RESERVE_USD[def $5])`; reject only if `< $1.00` (`below_minimum_trade_size`). Fractional `math.floor` to 4 dp with a $1-min bump. Opens only — closes always run.
- **Reconcile runs in 3 places**: startup (server.py, force=True, WARNING+exc_info on failure), periodic (alpha_day_trader tick tail, 300s throttle), and immediately before each buy veto.

### Verified
- Startup sweep already cleared **19 phantom rows**; only QQQ+CYPH (real) remain open. New movers pass; held blocks; broker-down fails closed; BP-below-target sizes down (BP $63 → $58, 0.232 sh); tiny acct → below_minimum; big acct → $350 ceiling.
- Tests: `tests/test_alpha_position_reconciler.py` (5, new) + executor/exits/silent-gaps/round-trip (42) + broker/router/short/reconciler suites (149) all green. `test_alpha_round_trip_proof` fixture updated so broker holds nothing at open, AAPL at close (accurate under broker-authoritative flow).

### Still on the table (NOT done — flagged, no unrequested work)
- **Why the exit/reconcile path stopped writing closes in the first place** (the deeper bug behind phantom accumulation). Periodic+startup reconcile now self-heals it, but the exit writer itself should be traced.
- Account genuinely holds only ~$194 — if operator expected more cash, that's broker-side.
- `.env PUBLIC_LIVE_NOTIONAL_USD=350` left as the ceiling (correct under new sizing).

⚠️ Preview-verified. Ledger reconcile already ran against the shared DB; the CODE fix reaches risedual.ai only after **Save to GitHub → redeploy**. Prod runs a labeled build (`GIT_SHA=alpha-r1`); confirm via owner-auth `GET /api/admin/runtime/stamp` after redeploy.

---


## Latest Update — 2026-06 (Lifecycle accounting hole CLOSED — the "unaccounted candidates" root cause)

### Operator finding (production)
Funnel showed 15,900 scanned → 11,576 candidates → 8,816 rank_culled + 4,324 degraded + **2,760 unaccounted**, with **0 setups**. The 2,760 candidates exited the per-candidate loop without stamping any terminal reason — a lifecycle-accounting hole between candidate and setup.

### Root cause
In the scan loop, `setup, is_new = _get_or_create_setup(...)` only counted when `is_new=True`. When a valid detected pattern **deduped to an already-active setup** (`is_new=False`, matching `dedup_key`=symbol+type+reference_price in WATCHING/ARMED/TRIGGERED), the `if is_new:` block was skipped and **nothing** was stamped. Those were the "unaccounted" — and it ALSO explains 0 NEW setups: the pattern engine WAS firing, but every detection mapped to an existing active setup.

### Fix (targeted terminal accounting — no thresholds/floors/broker touched)
- New terminal `setup_existing` (the `is_new=False` dedup path) — bumped + observed.
- Per-candidate guards `candidate_error` around BOTH `patterns.detect()` and `_get_or_create_setup()` so a raise stamps a terminal instead of aborting the loop / escaping accounting.
- End-of-scan **lifecycle invariant**: `len(ranked) == _ranked_handled`; on mismatch logs `LIFECYCLE ACCOUNTING HOLE` with escaped symbols + bumps `lifecycle_escaped`. Verified **0 on every completed tick**.
- Funnel + why-not-trade gates + Why-Not-Trade card now include `setup_existing` / `candidate_error` / `lifecycle_escaped`.

### What this reveals next (for the operator to decide — NOT changed unilaterally)
If production shows `setup_existing` dominating with `setups_created=0`, it means a population of **stale active setups is blocking all new ones via dedup** (they never resolve/expire). The next targeted fix would be active-setup expiry/lifecycle — strategy-adjacent, needs go-ahead. On preview, setups DO form (23 today), so preview is not dedup-blocked. `rank_culled` being high is the by-design top-N-per-tick cap (rank-before-execute), and the invariant confirms culled candidates are accounted, not lost.

### Tests
69 backend tests pass (funnel, day_trader, phase_c, why_not_trade, adaptive_freshness, market_daily_ordering). Live preview ticks confirm `setup_existing` increments and `lifecycle_escaped=0`. Server tick wrappers upgraded from silent debug to WARNING + traceback so a crashing production tick is visible.

⚠️ Preview-verified. Save to GitHub to redeploy to production.

---

## Latest Update — 2026-06 (Source-relative ADAPTIVE freshness + data provenance — Stage 1 + Stage 2, flag-OFF)

### Context
The Why-Not-Trade card proved Monday's failure was a DATA problem. The operator's directive: freshness must be **source- and session-relative** (a per-broker capability metric), not a universal hardcoded "quote < 5s" rule. "Fresh enough to decide" ≠ "fresh enough to route." Also: don't conclude the broker is slow — localize WHERE staleness enters (provider vs Alpha's own cache/hydration).

### Shipped (Stage 1 measurement + Stage 2 adaptive gate, feature-flagged OFF by default)
- **Data provenance probe** — `market_data_pool.probe_provenance()` + `GET /api/admin/alpha-daytrader/data-provenance?symbols=...`: side-by-side LIVE broker quote (provider, broker tick time, receive time, bid/ask/book) vs Alpha's 5-min cache vs the snapshot `_snapshot_symbol` actually builds, with a plain-English `verdict` (SOURCE / ENDPOINT / BAR-PROVIDER / ALPHA-HYDRATION / HEALTHY) + route-wide `broker_data_limited`.
- **Per-(broker, session) lag profile** — `services/broker_freshness_profile.py`: rolling 500-sample p50/p95/p99, `trusted` only at ≥30 samples, `route_key` normalization, `assess_route_health` (route-wide `broker_data_limited`: missing_book OR age>5×p95 across most probed symbols → block entries, allow exits). `GET /broker-freshness-profile` exposes it.
- **Adaptive gate** in `provider_policy`: `compute_freshness_limit = max(configured_minimum(2s CORE), broker_p95×2, session_floor)`; session ceilings PREMARKET/AFTER_HOURS=120s, OVERNIGHT=600s, CRYPTO=3s. Gates on the broker's **true tick timestamp** (real staleness), records every execution quote's tick age into the profile. **Feature-flagged `EXECUTION_ADAPTIVE_FRESHNESS` (default OFF)** — while OFF, behavior is byte-for-byte the legacy 5s gate; measurement still runs.
- **internal_snapshot_stale** — `_snapshot_symbol` fails closed (+ invalidates daily cache to self-heal) when the cache-served bar is older than the newest LIVE-verified bar (`market_data_pool` high-water-mark). This is the AAPL smoking gun (cache served 09-11 while live had 09-14), labeled distinctly from a source-side stale bar.

### To enable Stage 2 in production (operator action, after samples accumulate)
Set `EXECUTION_ADAPTIVE_FRESHNESS=1`. Watch `GET /broker-freshness-profile` until each route/session shows `trusted:true` (≥30 samples), then flip it.

### Tests
Testing agent iteration 204: **109/109 backend pass, zero issues** (flag-off legacy safety, adaptive-on tick-timestamp gating, internal_snapshot_stale, broker_data_limited, both endpoints live w/ auth). New: `tests/test_adaptive_freshness.py`, `tests/test_adaptive_freshness_stage2.py`.

### NOT changed
No trading thresholds/floors/execution behavior while the flag is OFF. Stage 3 (RoadGuard comparing each trade against the profile of the route that will EXECUTE it) is partially covered since `fetch_execution_quote` already uses the executing broker's profile; full multi-route wiring remains.

⚠️ Preview-verified. Save to GitHub to redeploy to production.

---

## Latest Update — 2026-06 (ROOT CAUSE FOUND & FIXED — broker daily bars were newest-first)

### 🎯 THE root cause of Monday's "16,138 candidates → 0 setups"
The live Why-Not-Trade card (shipped in the previous update) reported **100% `Data Degraded`, 0 reached the floor** on PRODUCTION — proving it was a DATA problem, not policy. Investigation of the broker feed found:
- **Public.com (the primary market-data provider / execution broker) returns daily bars NEWEST-first.** `broker_service.PublicTradingService.get_daily_bars` explicitly does `rows.sort(key=date, reverse=True)` ("newest-first to match the other providers"). Alpha Vantage (`reverse=True`) and Polygon (`sort=desc`) do the same.
- **Every consumer assumes ASCENDING** (`bars[-1]` = today): `alpha_day_trader._snapshot_symbol`, `public_equity_live_executor` move-calc (bars[-1]/bars[-2]), `market_regime`, `fast_intraday_regime`.
- Net effect: Alpha read the **OLDEST** bar (e.g. 2026-04-22, ~5 months stale) as "today" → `pct_change` computed from stale closes ≈ noise → every candidate fell into the strict `low_vol_no_news` floor → 0 setups. The classical pattern engine (double_bottom/H&S) was also analysing months-old geometry (explains the inflated historical setup counts that never executed).

### ✅ Fix (single source of truth)
`services/market_data_pool.py` → new `_normalize_daily()` guarantees **ascending-by-date** output from `market_daily()` on BOTH the fresh-fetch and cache-read paths, for ALL providers. `bars[-1]` is now always the most recent session regardless of which broker/backup served it. ISO `YYYY-MM-DD` dates sort chronologically; bars missing a date are left untouched (no scramble).

**Broker data locked in:** Public.com is already the priority-1 provider (`pool_config.get_market_data_provider_pool`); its quote carries live `bid`/`ask` (healthy book), so production quotes are not false-flagged by the integrity guard. Alpha now trades on the same feed where its orders fill.

### Verified
- Live feed after fix: AAPL/MSFT/SPY `bars[-1]=2026-09-14` (latest), real `pct_change` (0.244 / 1.973 / -0.446), `degraded=False`. Before: `bar_date=2026-04-22`, all degraded.
- New regression `tests/test_market_daily_ordering.py` + affected-consumer suites: **90 tests pass** (market-daily ordering, day trader, phase_c, funnel, family floor, extreme-move validator, round-trip proof, polygon). Backend healthy.

### Next
- Watch the Why-Not-Trade card on the next live session: candidates should now flow PAST Data Degraded into the floor/pattern buckets and produce setups. If `Below Floor` then dominates on the healthy feed, tune the family floor (separate evidence-backed step). Do NOT touch execution until Intents > 0.

⚠️ Preview-verified against the live feed. Save to GitHub to redeploy to production.

---

## Latest Update — 2026-06 (P0 zero-setup debug — Why-Not-Trade observability + market-data integrity)

### 🎯 Operator finding
Live session: ~16,138 candidates processed → **0 setups**, and no UI existed to see WHICH gate killed them. Diagnosis (high confidence): the only gate that can silently reject 100% of candidates is the pre-pattern family floor (`opportunity_score_rejected`), fed by degraded/stale intraday features (no live quote → pct≈0 + rvol<1 → everything dumped into the strict `low_vol_no_news` 0.447 floor). Preview repro confirmed all market-data providers were failing (Public creds unavailable, AV/Finnhub/Polygon errors).

### 🎯 What shipped (Operator "Option A" — observability + data integrity ONLY; NO gate/threshold/execution changes)
**1. Market-data integrity in `_snapshot_symbol`** (`services/alpha_day_trader.py`): new `MarketSnapshot` fields `data_degraded`, `degraded_reason`, `quote_available`, `bar_date` + `_bar_is_stale()` helper. Degradation requires **corroborating feed evidence** (quote fetch failed, OR no live book AND no spread, OR zero today-volume, OR stale bar > 4 days) — never pct≈0 + rvol<1 alone. A genuinely quiet-but-quoted stock is NOT flagged (verified). Degraded candidates are excluded from ranking (never masquerade as `low_vol_no_news`); the trigger loop **fails closed** on degraded data (no trade on a stale price) with a diagnostic receipt.

**2. Full funnel reconciliation counters**: scan loop now bumps `symbols_scanned`, `market_data_degraded`, `rank_culled`; per-candidate loop bumps `opportunity_score_rejected`, `wave_danger_pause`, `no_pattern_match`. `get_counters` adds a `funnel` block where `candidates_seen = rank_culled + opportunity_score_rejected + wave_danger_pause + no_pattern_match + setups_created` (+`candidates_unaccounted` = 0 once fully deployed). No candidate silently disappears.

**3. Why-Not-Trade UI** (`components/admin/WhyNotTradeCard.jsx`, new): reconciliation banner + per-gate tiles (counts, top symbols, sub-reasons) on the Alpha Day Trader panel; "Data Degraded" cell added to Today lifecycle. `market_data_degraded` registered in `alpha_why_not_trade.REJECTION_GATES`.

### Decision tree the operator can now run on the LIVE card
- ~10k+ `opportunity_score_rejected` with HEALTHY data → investigate scoring/floor (separate evidence-backed step, NOT done yet).
- Thousands `market_data_degraded` → fix the feed/features, not policy.
- Setups appear once valid data restored → floor was being fed garbage (confirmed hypothesis).
- Setups appear but triggers/intents stay 0 → move downstream (Trigger Watcher / rank-before-execute). Don't touch Public/MooMoo execution until Intents > 0.

### Tests
- Testing agent iteration 202: **100% backend + 100% frontend**. Degradation unit-verified (no-quote/no-book/stale-bar/zero-vol flagged; healthy quiet stock NOT flagged). Regression suites green (`test_alpha_funnel`, `test_alpha_family_floor`, `test_alpha_why_not_trade`, day-trader).
- Known pre-existing failure (unrelated): `test_alpha_day_trader_phase_c.py::test_symbol_lock_reentrant_for_same_setup` (asserts removed reentrant-lock behavior).

### Explicitly NOT changed (per operator doctrine)
Opportunity-score thresholds, family floors, chasing caps, bearish vetoes, pattern sensitivity, and all execution paths are untouched. Floor tuning is deferred until the live card proves the floor (not the feed) is the culprit on a healthy feed.

⚠️ **Preview-only.** Save to GitHub to redeploy so the observability + integrity fix reach production.

---

## Latest Update — 2026-02 (P1-B Discernment Patch — rank-before-execute, installed unwired)

### 🎯 What shipped

Operator ship: the P1-B Discernment patch (extracted from Foundation v2.3, minus the parts that would conflict with P1-A). Adds a direction-neutral opportunity ranker so Alpha can compare BUY and SELL_SHORT candidates head-to-head with penalties for chasing / execution risk / short borrow cost — while `ENABLE_SHORT_EXECUTION=false` remains the default and the proven long path is untouched.

**1. Direction-neutral opportunity ranker** (`services/alpha_opportunity_ranker.py`) — pure, execution-agnostic:
- `compute_opportunity_score(signal, entry_quality, liquidity, market_alignment, relative_strength, chase_risk, execution_risk, borrow_cost_risk, weights)` → 0..1 capital-allocation score. Discernment dominates (0.50 weight); penalties total up to 0.36 so they can materially demote a signal without ever driving the score negative.
- `rank_actionable(candidates, context_by_symbol, min_score)` → best-first ordered list. **WATCH / REJECT / HOLD candidates are filtered out BEFORE scoring** — they cannot consume capital.
- `borrow_cost_risk` is a short-only knob (defaults to zero for longs) — the ranker treats a legitimate long and a HTB short comparably, and lets borrow cost demote the short below a slightly weaker but cleaner long.

**2. Advisory discernment helper** (`services/foundation_v23_discernment.py`) — namespaced pattern classifier extracted from v2.3 as an ADVISORY feed. NOT wired into Alpha's production five-pattern matcher; sitting on the shelf for A/B validation later. Zero import from Alpha's live path.

**3. Tests: 113/113 green**
- `tests/test_alpha_opportunity_ranker.py` (3 tests) — best-first ordering, non-actionable filtering, HTB demotion.
- `tests/test_alpha_opportunity_ranker_extra.py` (7 tests) — WATCH+REJECT+HOLD filter in one call, high-scoring REJECT can't leak, chase-risk demotes late entries, execution-risk demotes stale quotes, **borrow cost is independent of the other penalty knobs**, longs are never penalized for a short-only cost, score clamps to [0, 1] under adversarial input.
- All existing P1-A / P1-B / Round-Trip / executor / router / fill-writer tests still green (103 pre-patch → 113 post-patch).

### What was intentionally EXCLUDED from Foundation v2.3

Per the patch's `FILES_EXCLUDED_FROM_FOUNDATION.txt` and operator directive — these were **not** transplanted because they'd overwrite production infrastructure that's already correct:
- `execution_client.py` — would clobber P1-A's Public REST short executor.
- `orchestrator.py` — Alpha's current wiring is authoritative.
- `models.py` — would replace production schemas.
- `risk_gate.py` / `entry_timing.py` / `kill_switch.py` — current gates are authoritative.
- `ledger.py` — current accounting is authoritative.
- `config.py` / `.env.example` — would overwrite production flags/defaults.

**P1-A short executor untouched. `ENABLE_SHORT_EXECUTION=false` remains the default.**

### Deliberately deferred: the loop wire-in

The ranker module is **installed but unwired**. The current `alpha_day_trader.py` loop scans and immediately dispatches per symbol; the target flow is scan → collect ACTIONABLE → rank → dispatch in ranked order. That refactor:
- Touches a proven long path — must be done carefully to avoid regressing P1-B / Round-Trip / P1-A.
- Requires `build_opportunity_context()` mapping existing Alpha intent metadata into the ranker's 7 context fields (chase_risk from chasing filter, execution_risk from quote-freshness + broker-health, borrow_cost_risk from P1-A ladder for shorts, etc.).
- Should follow the deployment doctrine: (1) ship rank-before-execute + logging with EXEC off; (2) confirm ranking improves selection quality; (3) THEN run the $25 P1-A canary.

Backlog task with a clear entry point — no engineering surprises left.

### Safety pins

- **P1-A short executor untouched.** Verified by tests asserting SDK's `place_order` is never called on short paths.
- **`ENABLE_SHORT_EXECUTION=false` remains the default.**
- **No new raw-event journal in MongoDB** per the patch's doctrine — the existing SQLite hot-store + rollups are the outcome path.
- **Ranker is pure.** No async, no I/O, no broker calls, no imports of executor modules — cannot accidentally submit an order.
- **WATCH/REJECT/HOLD filter is BEFORE the score** — a high-scoring non-actionable candidate cannot leak into the ranked capital-allocation queue.

---

## Historical Update — 2026-02 (P1-A Short Execution Port — Public REST direct-path)

### 🎯 What shipped

Operator directive: the installed Public Python SDK is behind Public's current REST contract (April 2026 short-selling API). Don't force `openCloseIndicator=OPEN` / `useMargin=True` into the SDK's `OrderRequest`. Build a narrow REST fallback that speaks the current endpoint directly. Keep `ENABLE_SHORT_EXECUTION=false` until the ladder proves out.

**1. Public short eligibility ladder** (`services/public_short_eligibility.py`) — 4-rung fail-closed check:
- **Rung 1** `check_account_eligibility` — account must be `brokerageAccountType=MARGIN` AND `tradePermissions=BUY_AND_SELL`. Anything else hard-fails.
- **Rung 2** `check_no_existing_position` — Public forbids direct long↔short flips. Any existing long OR short position on the symbol blocks a new short.
- **Rung 3** `check_instrument_shortable` — reads `shortingAvailability`. NOT_SHORTABLE hard-fails; EASY_TO_BORROW / HARD_TO_BORROW pass and expose `hardToBorrowPercentageRate` for policy.
- **Rung 4** `preflight_short` — Public's single-leg preflight returns `buyingPowerRequirement`, `marginImpact`, `upTickRuleRequired`, `maxLocateQuantity`. Preflight rejection or max locate < requested qty hard-fails.
- `run_full_ladder` short-circuits on first failure so a rung-1 fail never wastes rungs 2-4 API calls. Optional `max_htb_rate_pct` policy cap.

**2. REST short executor** (`services/public_short_executor.py`) — direct HTTP path to Public's `/trading/{acctId}/order` with the newer fields (`openCloseIndicator`, `useMargin`) that the installed SDK's `OrderRequest` doesn't know about. Whole-share qty only (`compute_whole_share_qty` floors to integer; refuses fractional shorts). SELL+OPEN to enter, BUY+CLOSE to cover. Never touches the SDK's `place_order`.

**3. Executor wiring** (`services/public_equity_live_executor.py`):
- New env knobs: `ENABLE_SHORT_EXECUTION` (default OFF), `PUBLIC_LIVE_SHORT_FIRST_NOTIONAL_USD` (default $25 canary), `PUBLIC_LIVE_SHORT_MAX_HTB_PCT` (optional policy cap).
- `open_short` intent_kind: refused with `short_execution_disabled` when the flag is unset. When armed, runs the full 4-rung ladder BEFORE submitting. Any rung failure logs `short_ladder_<reason>`.
- `close_short` (BUY_TO_COVER): bypasses the ladder because covering is an exit, not new short exposure.
- Short paths route through the REST helper (`submit_short_order`); long paths continue via the SDK's `place_order`. Clean separation, verified by tests asserting `place_order` is never called on short paths.
- Whole-share qty on shorts uses the canary budget (never the standard $350 long allocation for a first-ever short).

**4. Tests: 103/103 green.**
- `tests/test_public_short_eligibility.py` (16 tests): every rung + full ladder short-circuit + HTB policy gate.
- `tests/test_public_short_executor.py` (12 tests): whole-share math + REST body construction + bad-input refusals + auth failure + broker rejection + custom client_order_id.
- `tests/test_public_short_execution_e2e.py` (7 tests): disabled-flag skip, whole-share happy path, cash-account block, NOT_SHORTABLE block, preflight-rejection block, close_short bypasses ladder, broker-truth position blocks fresh short.
- All existing P1-B / P1-B exit / Round-Trip Proof / executor / router tests still green.

### Safety pins

- `ENABLE_SHORT_EXECUTION=false` remains the default. Alpha will NOT short in production without an explicit operator flip.
- The 4-rung ladder ships enabled behind the flag — no half-baked short paths that could leak.
- Router (from P1-B) still guarantees: SELL_TO_CLOSE cannot become SELL_SHORT; BUY_TO_COVER cannot create a new long.
- Whole-share qty enforced at the sizing helper — Public rejects fractional shorts and we honor that at the smallest possible bound.
- First-fire notional separate from long notional (`PUBLIC_LIVE_SHORT_FIRST_NOTIONAL_USD`, default $25) so the first live short is a small canary, not the standard long size.

### What this unlocks

Alpha can now be armed for its first live short — after the operator:
1. Confirms the Public account is margin-enabled and has BUY_AND_SELL permissions
2. Sets `ENABLE_SHORT_EXECUTION=1`
3. Optionally sets `PUBLIC_LIVE_SHORT_MAX_HTB_PCT=<cap>` to cap HTB rate
4. Verifies the first short round-trip reconciles: Alpha short → Public short position → BUY_TO_COVER → position back to zero → outcome row with correct realized_r

### Production acceptance condition (deferred)

> **First completed live long must produce a broker-reconciled round-trip receipt before we call the lifecycle production-verified.**

The signed trade-lineage receipt (compact SQLite hash-chain: `trade_id`, `setup_id`, entry+exit intent_id / broker_order_id, fills, realized_pnl / realized_r, `previous_hash`, `record_hash`) is architected and queued as backlog — build after the first live long round-trip validates the pipeline against the broker.

---

## Historical Update — 2026-02 (Round-Trip Proof — Alpha completes a full long lifecycle end-to-end)

### 🎯 What shipped

Operator directive (P1-B follow-up): before enabling short entries, prove the existing long lifecycle works completely — no stage may be inferred. Broker order/fill IDs must link the entry and exit to the outcome row.

**1. Round-Trip Proof test** (`tests/test_alpha_round_trip_proof.py`) walks every stage explicitly:
```
Long intent → Public submit → ACK → fill → position tracking →
  SELL_TO_CLOSE → ACK → fill → reconcile position → outcome → realized_r
```

The proof fails at any stage where an artifact is missing:
- Stage 1: `equity_live_trades` row exists with `status="open"` and non-empty `broker_order_id`
- Stage 2: Entry broker order ID propagates from Public (not fabricated)
- Stage 3: Fill price recorded (`entry_price > 0`)
- Stage 4: `track_open_excursions` updates `peak_price` on a mid-trade tick
- Stage 5: SELL_TO_CLOSE with `exit_only=True` transitions the same row to `status="closed"` with `close_order_id` set and `close_filled_qty == entry qty`
- Stage 6: Exit broker order ID differs from entry order ID (proving they're distinct broker submissions)
- Stage 7: `resolve_closed_outcomes` upserts `alpha_outcomes` with `entry_broker_order_id`, `exit_broker_order_id`, `direction`, `entry_fill_price`, `exit_fill_price` — the audit-join foundation
- Stage 8: `realized_r` math verified against actual fills — a +$10 move on a $5 stop-risk long = exactly 2.0R

Plus a symmetric guard test: `SELL_TO_CLOSE + exit_only=True + no position → place_order MUST NOT be called`. If exit paths ever silently open a short, this test breaks.

**2. Wire fixes discovered by the proof** (real gaps closed):

- `public_equity_live_executor.py` OPEN branch now carries the `alpha_daytrader` payload (`setup_id`, `stop_price`, `target_price`, `confirmation_price`) onto the `equity_live_trades` row. Previously the row had no back-link to the setup, so `alpha_fill_writer.resolve_closed_outcomes` couldn't join back to `alpha_outcomes` and outcomes silently never resolved.
- Stop price and target price now snapshotted on the row at open time so realized_r has a stable risk-distance anchor even if the caller's intent shape changes later.
- `alpha_fill_writer._resolve_metrics` now writes `entry_broker_order_id`, `exit_broker_order_id`, `close_filled_qty`, and `close_requested_qty` onto the outcome doc. The whole point of the proof: an auditor can join outcome → entry order → exit order without inferring anything.

**3. Test results:** 158/158 green across all executor / fill-writer / exit / round-trip / day-trader suites. Zero regressions.

### Doctrine pin

> **Every stage of the lifecycle must be verifiable from persisted state.** If the outcome row doesn't carry the entry+exit broker order IDs, we can't prove the outcome corresponds to the trades we think it does. Alpha is now testing an autonomous trading lifecycle, not just an entry engine.

### What this unlocks

P1-A (Foundation v2.2 Short Execution) can now proceed on a proven lifecycle foundation. Every property the operator required is enforceable and asserted:
- SELL_TO_CLOSE cannot become SELL_SHORT (router + executor tests)
- BUY_TO_COVER cannot create a new long (router + executor tests)
- Broker position wins over Mongo belief (P1-B executor tests)
- Exit intents are idempotent (`close_in_flight` guard)
- Partial fills are resolvable (`status=partial_closed` + `close_remaining_qty`)
- Outcome row links back to real broker orders (Round-Trip Proof)

---

## Historical Update — 2026-02 (P1-B Position-aware Trade Exits — SELL_TO_CLOSE + BUY_TO_COVER)

### 🎯 What shipped

Operator directive: before enabling short entries (P1-A), prove position closing works cleanly on both sides. An exit must never accidentally create a new directional position, the broker always wins over Alpha's internal belief, and partial fills stay resolvable.

**1. Position-aware exit router** (`services/alpha_exit_router.py`) — pure classifier that resolves ambiguous intents:
- `SELL_TO_CLOSE` against LONG → `close_long`, never `open_short`
- `BUY_TO_COVER` against SHORT → `close_short`, never `open_long`
- `OPEN_LONG` blocked when SHORT open (`ambiguous_reverse_short_open`) and vice versa — reversing exposure must be an explicit two-step.
- `exit_only=True` flag guarantees no new position can be opened.
- Explicit `intent_action` (SELL_TO_CLOSE / BUY_TO_COVER / OPEN_LONG / OPEN_SHORT) wins over direction inference.

**2. Public equity executor now supports `close_short`** (`services/public_equity_live_executor.py`):
- Order side mapping: `open_long→BUY`, `close_long→SELL`, `open_short→SELL`, `close_short→BUY`.
- Early broker-position probe when the intent could be an exit (exit_only, SELL-family, explicit close, or existing Mongo open row).
- **Broker qty wins.** If Mongo believes 2.3 shares but Public reports 1.8, the close order is sent for 1.8 and the discrepancy is logged as a warning — never a phantom order for the missing 0.5.
- **`close_in_flight` idempotency.** Flag stamped on the row before `place_order`, cleared on success/failure. 120s stale window prevents wedging. Concurrent SELL re-fires are refused with `close_in_flight`.
- **Partial-fill handling.** If broker returns `filled_qty < qty`, row is marked `status="partial_closed"` with `close_remaining_qty`; the fill writer / reconciler will finish the exit on the next sweep. Prior behaviour silently marked the whole row `closed`, losing residual exposure.

**3. Direction-aware Outcome Engine metrics** (`services/alpha_fill_writer._resolve_metrics`):
- SHORT PnL = (entry − close) × size; LONG unchanged.
- For SHORT, MFE uses `trough_price` (favorable drop), MAE uses `peak_price` (adverse rise).
- Entry/exit slippage_bps sign-normalized so positive always means "worse than reference."

**4. Safety pins:**
- `open_short` intent_kind is refused unless `PUBLIC_LIVE_SUPPORTS_SHORTS=1` — pending P1-A / v2.2. Closes are always allowed (they're exits, not new short exposure).
- `open_long` blocked when any existing open row is present (long or short) — prevents both duplicate opens and short→long auto-reverse.
- `no_op` router verdicts log to `intent_skip_log` with `router_*` reason codes so the taxonomy dashboard can surface why an exit was refused.

### Tests

- `tests/test_alpha_exit_router.py` — 18 unit tests: explicit actions, direction inference, exit_only guard, ambiguous-reverse, zero-qty edge cases, negative-qty normalization.
- `tests/test_alpha_fill_writer_short.py` — 7 tests: SHORT PnL, SHORT MFE/MAE (trough vs peak), sign-normalized slippage.
- `tests/test_public_executor_exits.py` — 8 integration tests: broker qty wins on close_long, explicit BUY_TO_COVER, BUY-vs-short auto-covers-never-opens-long, SELL-no-position + shorts-disabled, exit_only guard, partial close, close_in_flight blocks / stale clears, OPEN_LONG blocked by open short.
- `tests/test_public_equity_live_executor.py` — 29 pre-existing tests unchanged and still passing.

**Total: 63/63 passing. Zero regressions across broader executor / fill-writer suites (backend testing agent iteration 200: 100% success).**

### Contract shift for callers

`_build_intent_dict` in `alpha_day_trader.py` and any other executor caller can now include:
- `intent["exit_only"] = True` when the intent is guaranteed to be an exit (e.g. from `day_trade_exit_monitor`).
- `intent["intent_action"] = "SELL_TO_CLOSE" | "BUY_TO_COVER" | "OPEN_LONG" | "OPEN_SHORT"` for explicit override; otherwise direction+position inference applies.

Neither is required — legacy callers continue to work with just `direction`.

### Doctrine pin

> **The broker is the authority.** For every close, we ask `get_positions` first and use that qty. Alpha's Mongo row is a hint; the broker's report is the truth. Discrepancies get logged and reconciled on the next sweep.

---

## Previous Update — 2026-02 (Chasing filter forensic audit + skip-log dedup + extreme-move validator)


# RISEDUAL AI — PRD

## Historical Update — 2026-02 (Chasing filter forensic audit + skip-log dedup + extreme-move validator)

### 🎯 What shipped

Operator directive: before loosening the chasing_filter (24/24 rejections yesterday), expose the anchors, dedup the log-amplification, and route extreme moves through a validator that distinguishes "real 27% move" from "broken anchor / split-desync."

**1. Sizing cap raised** — `PUBLIC_LIVE_NOTIONAL_USD` 1 → 350, `MOOMOO_MAX_NOTIONAL_USD` 50 → 350. Code-level hard clamp stays at $1,000 (safe ceiling for later $500-750 experiments). Verified fractional sizing math across $10-$800 marks.

**2. Enriched chasing_filter payload.** Every chasing_filter rejection now writes a full audit shape:
- `move_pct`, `cap_pct`, `excess_over_cap`, `setup_type`, `direction_class`, `knife_mult`
- `reference_price` (prev_close), `today_close`, `signal_price` (from intent)
- `move_at_signal_pct`, `adverse_drift_since_signal_pct`, `signal_age_seconds`
- `extreme_move_verdict` (new — see #4 below)

**3. Skip-log dedup with amplification counter.** `_log_skip` upserts into a 5-min rolling window keyed by `(symbol, reason, direction)`. Same key within window → increment `refire_count`, update `last_seen` + latest `detail`. Different key or expired window → new row with `refire_count=0`, fresh `first_seen`. Audit endpoint now returns both `total` (unique events) and `total_observations` (unique + refires) plus `amplification_factor`. Collapses the "19 SMCI at 4.96% + 6 ADBE at 7.24%" amplification into single entries with an honest refire count.

**4. Extreme-move validator (EXTREME_MOVE_REQUIRES_VALIDATION at 15%+).** `services/alpha_extreme_move_validator.py` runs 5 checks on any move exceeding threshold:
- `reference_integrity` (structural — both closes from single provider row)
- `quote_freshness` (bar timestamp within 96h)
- `session_boundary` (1-5 days between prev/today, catches spanning-corporate-event gaps)
- `corporate_action` (adjustment_factor 0.95-1.05 if provider exposes it; skipped otherwise, not failed)
- `cross_broker_confirm` (MooMoo canary quote if bridge ≥ DATA_READY; skipped otherwise)

Verdicts:
- `EXTREME_MOVE_CONFIRMED` — ≥2 checks passed, none failed → move is real. Cap still blocks the entry on its own merits. Audit classifies as `clearly_extended`.
- `REFERENCE_PRICE_ANOMALY` — any hard check failed → anchor broken. Alpha won't use this reading to argue anything about the cap. Audit classifies as `reference_anomaly`.
- `EXTREME_MOVE_UNVERIFIED` — insufficient signal → trade blocked, tagged for review. Audit falls through to normal classification.

The validator NEVER unblocks a trade — the chasing filter's block stands regardless. Verdicts are purely diagnostic.

**5. Audit endpoint** `GET /api/admin/alpha-daytrader/chasing-filter-audit?since_seconds=N` — classifies rows into 5 buckets (`clearly_extended`, `marginally_over`, `stale_signal`, `reference_anomaly`, `insufficient_data`), returns `caps_by_pattern` (per-pattern effective envelope + knife multiplier), `amplification_factor`, and a one-sentence recommendation biased toward "prove the anchors first."

**6. Coarse fallback for pre-enrichment historical data.** Old rows (only `move_pct` + `cap_pct`) still classify as extended vs marginal by ratio to cap. Stale/anomaly detection only applies to enriched rows.

### 7-day audit at deploy (historical, pre-enrichment)

```
7-day: 44 unique | 44 total obs | amplification 1.0x
★ clearly_extended    25 (57%)  — worst: PCLA +27%, PARAW +19%, ADBE +7.24%
★ marginally_over     19 (43%)  — all SMCI +4.96% × 19 (one signal re-firing)
  stale_signal         0        — awaits enriched rows Monday
  reference_anomaly    0        — awaits enriched rows Monday
```

Amplification=1.0× reflects pre-dedup data. Monday's rows will collapse the SMCI/ADBE amplifications automatically.

### Tests

- 116/116 green across new + regression suites:
  - `tests/test_skip_log_dedup.py` — 9 tests (window rollover, key isolation, latest-detail on refire, exception safety)
  - `tests/test_alpha_extreme_move_validator.py` — 15 tests (threshold, verdicts, env override, check-shape)
  - `tests/test_alpha_chasing_audit.py` — 24 tests (5 buckets, extreme_verdict precedence, coarse fallback, recommendations)
  - Plus 68 regression tests from prior workstreams.

### Files

- `backend/services/public_equity_live_executor.py` (enriched chasing payload + dedup upsert + timedelta import)
- `backend/services/alpha_chasing_audit.py` (new — classification, per-pattern caps, recommendations)
- `backend/services/alpha_extreme_move_validator.py` (new — 5-check validator with 3 verdicts)
- `backend/routes/admin_alpha_daytrader.py` (new endpoint `GET /chasing-filter-audit`)
- `backend/.env` (`PUBLIC_LIVE_NOTIONAL_USD=350`, `MOOMOO_MAX_NOTIONAL_USD=350`)
- 3 new test files (48 tests)

### Env config

- `PUBLIC_LIVE_NOTIONAL_USD` (default `25`, now `350`)
- `MOOMOO_MAX_NOTIONAL_USD` (default `50`, now `350`)
- `PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT` (default `4.0`)
- `EXTREME_MOVE_THRESHOLD_PCT` (default `15.0`, clamped 5-50)

⚠️ **Preview-only.** Save to GitHub to redeploy.

---


## Latest Update — 2026-02 (Intent → Broker observability — 3 fixes shipped)

### 🎯 Operator finding

The "27 intents → 0 broker submissions" screenshot revealed the pipeline WAS reporting rejections — but bundled protection (Alpha correctly refusing), infrastructure (plumbing broken), and session (market closed) together as one indistinguishable count, plus 8 gates that returned silently with no reason at all. Operator's principle: **mixing protection and infrastructure hides both. Loosening protection when infrastructure is the real culprit recreates late-entry / bad-entry regressions.**

### 🎯 What shipped

**1. Eight silent-return paths in `maybe_route_live` now log specific reasons.**

- `public_equity_live_executor.py` — each of the following bare `return None` calls is now preceded by `_log_skip(...)` with a distinct reason code:
  - `dup_open_row` (already-open row on `open_long` intent)
  - `sell_no_position` (`close_long` but broker reports 0 shares)
  - `no_mark_price` (mark quote fetch returned None/0)
  - `qty_zero` (fractional-share rounding produced 0)
  - `client_init_failed` (broker client couldn't initialize)
  - `broker_watchdog_frozen` (prior submission stuck in unknown state)
  - `place_order_exception` (Public.com REST raised)
  - `broker_empty_response` (Public.com returned empty body)
- Regression tests source-scan `maybe_route_live` and assert no bare `return None` exists without a preceding `_log_skip` within 30 lines. Guard against re-introducing the silent-skip regression.

**2. Orphan-intent reaper — the fail-visible invariant.**

- `services/alpha_orphan_reaper.py` (new) — `sweep_orphaned_intents(db)` finds intents older than the window (env `ALPHA_ORPHAN_REAPER_SECONDS`, default 600s, min-clamped to 60s) with `submitted=False` and null/empty `reject_reason`. Backfills them with `reject_reason="orphaned:no_terminal_event"` AND writes an `execution_blocked` observation so they surface in the why-not-trade stream. Non-invasive: never touches submitted or already-blocked intents.
- Auto-invoked at the tail of every `_run_alpha_tick_impl` in `alpha_day_trader.py`. Manual trigger: `POST /api/admin/alpha-daytrader/orphan-reaper/sweep`.
- The operator's contract enforced: every intent MUST terminate as `BROKER_SUBMITTED` or `EXECUTION_BLOCKED(reason_code)` — no silent disappearance possible.

**3. Rejection taxonomy — protection vs infrastructure vs session.**

- `services/alpha_rejection_taxonomy.py` (new) — `classify(reason)` maps every known rejection into one of five buckets:
  - **protection** (18 reasons: `chasing_filter`, `luld_roadguard`, `symbol_cooldown`, `confidence_floor`, `hw_kill_switch_tripped`, `dup_open_row`, `sell_no_position`, `qty_zero`, `insufficient_buying_power`, `short_signal_only`, `broker_watchdog_frozen`, etc.)
  - **infrastructure** (11 reasons: `execution_quote_blocked`, `no_broker_price`, `broker_degraded`, `no_mark_price`, `no_broker_creds`, `client_init_failed`, `place_order_exception`, `broker_empty_response`, `moomoo_not_execution_ready`, `moomoo_health_probe_failed`, `orphaned:*` prefix match)
  - **session** (`market_closed`, `live_exec_disabled`)
  - **concurrency** (`exec_lock_conflict`, `intent_deduplicated`)
  - **unknown** — fallback that FORCES taxonomy maintenance (loudly incomplete rather than silently misclassifying)
- `summarize(counts)` folds a `{reason: count}` map into class buckets. `health_hint(taxonomy)` returns a one-sentence tone (`alert`/`warn`/`ok`/`neutral`) — infrastructure dominance always alerts, even tied with protection (bias toward "don't loosen the wrong knob").
- Admin endpoint: `GET /api/admin/alpha-daytrader/rejection-taxonomy?since_seconds=86400` — merges executor `intent_skip_log` with `alpha_observations` (executor_rejected / execution_blocked / execution_quote_blocked / invalidated), aggregates by reason, classifies, returns `{taxonomy, health_hint, total}`.
- Dashboard card: `RejectionTaxonomyCard.jsx` — five side-by-side class tiles with top-6 reasons each, prominent health-hint banner. Auto-refreshes 45s. Rendered after `ConnectMoomooCard` in `AlphaDayTraderPanel`.

### Live data from the fix

24-hour production window at deploy:
- **Session: 103** — `market_closed` (Alpha correctly waiting for RTH)
- **Protection: 24** — `chasing_filter` (Alpha correctly refusing over-extended entries)
- **Infrastructure: 0** — no plumbing failures
- **Concurrency: 0**, **Unknown: 0**
- Health hint: `{"tone": "neutral", "message": "Session state dominates (103). Market is closed or live exec is off — no rejections require action."}`

**Diagnosis**: the "27 intents → 0 broker" screenshot was Alpha correctly refusing to trade. The pipeline was healthy the whole time; observability made it look broken. With the taxonomy tile in place, this exact scenario now shows unambiguously as `session-dominates` (do nothing) instead of `27 rejected` (call debug).

### Tests

- 30 new tests across 3 suites (all green):
  - `tests/test_executor_silent_gaps.py` — 2 tests, source-scan invariant.
  - `tests/test_alpha_orphan_reaper.py` — 6 tests, window floor, no-touch-terminal, exception safety.
  - `tests/test_alpha_rejection_taxonomy.py` — 22 tests, per-reason classification, folding, hint tones, unknown fallback.
- Testing agent iteration 199: 100% backend + 100% frontend. Live probe confirmed 127 real rejections in the correct classes with correct health hint.
- Regression sweep: `test_alpha_funnel.py`, `test_alpha_broker_event_watchdog.py`, `test_media_auth.py`, `test_security_hardening.py`, `test_moomoo_bridge_health.py` — 81 tests green, zero regressions.

### Files

- `backend/services/public_equity_live_executor.py` (8 patched silent returns)
- `backend/services/alpha_orphan_reaper.py` (new)
- `backend/services/alpha_rejection_taxonomy.py` (new)
- `backend/services/alpha_day_trader.py` (reaper invocation at tick tail)
- `backend/routes/admin_alpha_daytrader.py` (2 new endpoints)
- `backend/tests/test_executor_silent_gaps.py`, `test_alpha_orphan_reaper.py`, `test_alpha_rejection_taxonomy.py`, `test_admin_alpha_taxonomy_endpoints.py` (all new)
- `frontend/src/components/admin/RejectionTaxonomyCard.jsx` (new)
- `frontend/src/components/admin/AlphaDayTraderPanel.jsx` (wires the new card)

⚠️ **Preview-only.** Save to GitHub to redeploy.

---


## Latest Update — 2026-02 (Security audit hardening — SEC-001/002/003 closed)

### 🎯 What shipped

Security audit (2026-02) returned PASS with three LOW/P3 hardening items open. Operator directed knock-them-all-out. All three closed in one pass.

**SEC-001 — Broker credential encryption key split from JWT_SECRET.**

- `routes/broker.py::_get_fernet()` — rewritten to use `MultiFernet` with a dedicated `CREDENTIAL_ENC_KEY` as the primary write key and `JWT_SECRET`-derived key as a legacy DECRYPT-only fallback for pre-split rows. New encrypts never touch the legacy key. Zero-downtime rotation: existing rows keep decrypting; new writes are bound to the new secret.
- Hardcoded `"fallback-secret-key"` string is GONE. If neither key is configured, `RuntimeError("broker credential encryption not configured...")` — fail-fast instead of silent fallback.
- One-time boot warning when running on legacy key alone: `[broker-crypto] CREDENTIAL_ENC_KEY not set; using legacy JWT_SECRET-derived key. Set CREDENTIAL_ENC_KEY to a dedicated secret and rotate broker credentials.`
- `.env` seeded with a fresh `CREDENTIAL_ENC_KEY` (`secrets.token_urlsafe(48)`).

**SEC-002 — CORS wildcard: app never emits `*`, ingress noted for ops.**

- `server.py::DynamicCORSMiddleware` — documentation updated to explicitly state the middleware NEVER emits `Access-Control-Allow-Origin: *`. Any wildcard observed on the wire comes from the Kubernetes ingress / CDN layer for the two deliberately-public read routes (`/api/media/landing-video`, `/api/media/file/{id}`). The ops-side fix is to mirror `CORS_ALLOWED_ORIGINS` at the ingress.
- Regression test locked: `TestCorsMiddlewareNeverWildcards` — 5 tests, including a source-scan assertion (`'Access-Control-Allow-Origin"] = "*"'` is not present anywhere in the middleware) and behavioural probes proving allowed origins get reflected (never wildcarded), attacker origins get NO ACAO header, and public route responses don't wildcard from Python code.

**SEC-003 — CSRF exemption: substring match → prefix/exact match.**

- `services/csrf_middleware.py` — the vulnerable `for needle in _SKIP_SUBSTRINGS: if needle in path` block is DELETED. Replaced with a `_SKIP_PREFIXES` tuple checked via `path.startswith(prefix)`. New prefix list is precise: `/api/webhooks/`, `/api/webhook/`, `/api/oauth/`, `/api/broker/oauth/`, `/api/auth/oauth/`, `/api/bots/webhook/`, `/api/billing/webhook`.
- Ripgrep-confirmed every existing OAuth callback + webhook route in the codebase is covered (Stripe billing webhook, Stripe subscription webhook, trading-bots webhook, broker OAuth callbacks). Admin routes like `/api/admin/broker-oauth` correctly stay CSRF-enforced (they're state-mutating admin actions, not OAuth callbacks).
- Regression tests locked: `TestCsrfExemptionPrefixMatch` — 9 tests including the specific attacker-substring scenarios that used to bypass (`/api/foo/oauthx`, `/api/oauthy`, `/api/notreallyoauth`, `/api/pretendwebhook`, `/api/webhookx`, `/api/auth/login/impersonate`, `/api/auth/refresh/steal`), plus a source-scan asserting the `_SKIP_SUBSTRINGS` symbol is completely gone from the module.

### Tests

- `tests/test_security_hardening.py` (new) — 20 tests covering all three fixes:
  - SEC-001: dedicated key round-trip, legacy row backward-compat, primary-key rotation, missing-both-keys raises, hardcoded-fallback-string absent, dedicated-only works.
  - SEC-002: allowed origin reflected (not wildcarded), attacker origin gets no ACAO, public route not wildcarded by app, preflight never wildcards, source scan.
  - SEC-003: legitimate OAuth/webhook still skipped, `/foo/oauthx` no longer bypasses, admin broker-oauth still CSRF-enforced, pre-auth exact paths still skipped, substring-symbol source scan.

Live-verified end-to-end via preview:
- `POST /api/tick` (cookie-auth, no `X-Requested-With`) → 403 `csrf_header_missing`.
- `POST /api/tick` (cookie-auth, with `X-Requested-With`) → 404 (route doesn't exist — CSRF passed, not 403).
- `POST /api/broker/oauth/alpaca/callback` (no header) → 405 (Method Not Allowed — CSRF bypass working for legitimate OAuth path, hit the route).
- `POST /api/webhook/stripe` → 400 (webhook validation, not CSRF — bypass working).
- `GET /api/broker/connections` returns Kraken connection encrypted pre-split — decrypt via legacy fallback works.
- `[broker-crypto]` warning fires only when no `CREDENTIAL_ENC_KEY` set (silent when configured).

### Files

- `backend/routes/broker.py` (SEC-001)
- `backend/services/csrf_middleware.py` (SEC-003)
- `backend/server.py` (SEC-002 documentation)
- `backend/.env` (added `CREDENTIAL_ENC_KEY`)
- `backend/tests/test_security_hardening.py` (new — 20 tests, 46 total green in the security suite: 14 CSRF + 12 CORS + 20 hardening)

### Remaining open

None. All P0/P1/P2/P3 items from the 2026-02 audit are closed. The only remaining SEC-002 residual is at the ingress layer (a K8s config change) — noted in the middleware and documented as an ops task.

⚠️ **Preview-only.** Save to GitHub to redeploy the hardening + new `CREDENTIAL_ENC_KEY` to production.

---


## Latest Update — 2026-02 (MooMoo hybrid Option 2 — local OpenD bridge + readiness ladder)

### 🎯 What shipped

Operator directive: HYBRID Option 2. Build the local-OpenD bridge and Alpha web control panel; **keep Public.com fully operational whether MooMoo is connected or not**. Smoke test must be read-only. Introduce an explicit readiness ladder so Alpha can consult MooMoo for research long before it is trusted for execution.

**Readiness ladder (monotonic):**

```
DISCONNECTED → CONNECTED → DATA_READY → RESEARCH_READY → EXECUTION_READY
```

- `CONNECTED`      — TCP handshake to OpenD succeeds.
- `DATA_READY`     — canary quote fetch returns a fresh price.
- `RESEARCH_READY` — trade context opens AND `account_info()` returns; Alpha may consult MooMoo as a Funnel research witness.
- `EXECUTION_READY`— **both** `MOOMOO_LIVE_ENABLED=1` AND `MOOMOO_EXECUTION_READY=1`. Only at this rung does `moomoo_broker_adapter.submit_equity` submit orders.

### Files

- `services/moomoo_bridge_health.py` (new) — `compute_health()` runs the ladder probe (TCP → quote → account → gates). `run_smoke_test(symbol)` performs the read-only end-to-end path: tunnel → OpenD → account/permission → fresh quote → normalized `BrokerResearchSnapshot`. All probes non-raising; missing OpenD reports, never propagates.
- `services/moomoo_broker_adapter.py` — `submit_equity()` now gates on `compute_health().state == EXECUTION_READY`. Below that rung it returns `SubmitResult(ok=False, error="moomoo_not_execution_ready:<state>")`. Public.com path completely untouched.
- `routes/admin_alpha_daytrader.py` — three new endpoints:
  - `GET /api/admin/alpha-daytrader/moomoo-bridge/health` — ladder state + granular diagnostics (OpenD reachability, TCP latency, quote latency, buying power, trading permission, gate flags).
  - `POST /api/admin/alpha-daytrader/moomoo-bridge/smoke-test?symbol=SPY` — read-only end-to-end probe. Returns per-stage `{ok, latency_ms, error}` + `overall.failed_at` on first failure.
  - `POST /api/admin/alpha-daytrader/moomoo-bridge/endpoint` (owner-only) — runtime override for `MOOMOO_OPEND_HOST`, `MOOMOO_OPEND_PORT`, `MOOMOO_CANARY_SYMBOL`, `MOOMOO_EXECUTION_READY`. Forces trade context reopen against the new endpoint. `.env` unchanged (operator persists there manually).
- `frontend/src/components/admin/ConnectMoomooCard.jsx` (new) — admin card with:
  - Current state banner + full 5-rung ladder visualization
  - Status pills (OpenD, Quote, Account, Live Flag, Exec Ready) with latency/buying-power hints
  - Warnings/errors surface for the current probe
  - **Read-only Smoke Test** button and result panel (pass/fail per stage + duration + broker snapshot summary)
  - **Endpoint config drawer** (host/port/canary/execution-ready toggle) with Save that hits the runtime override endpoint
  - Auto-refreshes every 30s
- `frontend/src/components/admin/AlphaDayTraderPanel.jsx` — imports `ConnectMoomooCard` and renders it right after `WavePanelCard`.
- `tests/test_moomoo_bridge_health.py` (new) — 14 tests covering ladder monotonicity, gate combinations (only LIVE_ENABLED on → still RESEARCH_READY, only EXEC_READY on → still RESEARCH_READY, both on → EXECUTION_READY), TCP probe safety (real bind/probe + unreachable port), smoke-test stage-stop semantics (tunnel-fail short-circuits before market_data), and the critical "smoke test NEVER submits" guarantee (fixture stubs `submit_equity` with an AssertionError; test passes only if the smoke path never touches it).

### Architectural guarantee — Public.com independence

The MooMoo bridge lives entirely in its own module tree. `public_equity_live_executor.py` has zero references to `moomoo_bridge_health` or the readiness state. Public's trade path is not affected whether MooMoo is DISCONNECTED, RESEARCH_READY, or EXECUTION_READY. Alpha's two-broker architecture is preserved with MooMoo strictly opt-in.

### Env config

- `MOOMOO_OPEND_HOST` (default `moomoo-opend`)
- `MOOMOO_OPEND_PORT` (default `11111`)
- `MOOMOO_CANARY_SYMBOL` (default `SPY`)
- `MOOMOO_LIVE_ENABLED` (hardware kill switch — must be `1` for EXECUTION_READY)
- `MOOMOO_EXECUTION_READY` (operator's explicit execution gate — must be `1` for EXECUTION_READY)
- `MOOMOO_ACC_ID` (from operator, already configured)
- `MOOMOO_TRADE_UNLOCK_PASSWORD` (from operator, already configured)

### Live verification

- Backend endpoints tested green against preview: `GET /health` returns `state=DISCONNECTED` with `opend.reachable=false` (correct baseline — no OpenD in pod). Smoke test returns `overall.failed_at=tunnel` with `tcp_error:gaierror` (correct). `POST /endpoint` applies runtime env override, forces trade context close.
- Adapter gate proven at `python -c` boundary: `submit_equity(...)` at DISCONNECTED returns `SubmitResult(ok=False, error='moomoo_not_execution_ready:DISCONNECTED')`. No SDK call attempted below EXECUTION_READY.
- Frontend verified via testing agent: card renders on `/admin` → Alpha DayTrader tab, state banner shows `DISCONNECTED`, ladder rungs render dim/active correctly, Smoke Test button runs and populates result panel (`FAIL at tunnel`), config drawer opens, host/port/exec-ready fields fill, Save closes without error.

### What's still needed from operator to fully activate

1. Run OpenD locally on your Mac/PC.
2. Expose it via a tunnel (ngrok / Cloudflare Tunnel / Tailscale).
3. Update `backend/.env` `MOOMOO_OPEND_HOST` + `MOOMOO_OPEND_PORT` to the tunnel endpoint (or use the runtime override in the admin card).
4. When you're ready to arm MooMoo for execution: set both `MOOMOO_LIVE_ENABLED=1` AND `MOOMOO_EXECUTION_READY=1` in `.env`.

### Testing

- 14/14 bridge unit tests green.
- 6/6 bridge API integration tests green (`tests/test_moomoo_bridge_api.py`, added by testing agent).
- 6/6 frontend UI assertions green.
- No regressions in existing 135+ tests from prior workstreams (SEC-001, watchdog, LULD).

⚠️ **Preview-only.** Save to GitHub to redeploy the bridge to production.

---


## Latest Update — 2026-02 (SEC-001 patch + normalized broker order-event watchdog + LULD RoadGuard)

### 🎯 What shipped (three workstreams, one pass)

Operator directive: equities-first (Public.com + MooMoo). Kraken/crypto out of scope. Watchdog A/A order. The watchdog must FREEZE stale intents and trigger reconciliation — never assume failure, never auto-resubmit. LULD must stay narrow to avoid the overblocking regression.

**1. SEC-001 — Unauthenticated media upload closed (P0, recurring, deferred 2 forks).**

- `routes/media.py` — upload / upload-chunk / list / delete now require admin role via `_require_admin` (mirrors `admin_alpha_daytrader._require_admin`). `uploaded_by` is derived from the authenticated user's email/id via `_uploader_label`, never the hardcoded `"admin"` string. Response echoes the derived `uploaded_by` so clients can confirm attribution without a follow-up GET.
- Public read paths preserved: `GET /api/media/landing-video` and `GET /api/media/file/{id}` remain open (pre-login landing page + public embed).
- Memory-DoS caps: explicit per-chunk cap `_MAX_CHUNK_BYTES = 8 MiB` and per-upload cap `_MAX_UPLOAD_BYTES = 100 MiB`. Chunked staging tracks total bytes so replacing a chunk doesn't double-count.
- Live-verified: unauth POST/DELETE → 401; unauth `/media/landing-video` → 200.

**2. Broker order-event watchdog — Public.com + MooMoo unified.**

- `services/alpha_broker_event_watchdog.py` (new) — broker-agnostic FSM with the operator's normalized event vocabulary: `SUBMITTED / ACKNOWLEDGED / PARTIALLY_FILLED / FILLED / CANCELED / REJECTED / EXPIRED / BROKER_EVENT_STALE / BROKER_STATE_UNKNOWN`. `register_submission()` arms a 5-second timer (env `BROKER_EVENT_STALE_SECONDS`, default 5s). ACK-tier events cancel the timer; timeout marks intent `BROKER_EVENT_STALE`, freezes it, and triggers reconciliation via a registered callback. `is_frozen(broker, symbol, account_id)` is the duplicate-submit guard.
- Reconciliation outcomes route explicitly: `FILLED` / `OPEN → ACKNOWLEDGED` / `CANCELED` / `REJECTED` / `ABSENT → BROKER_ABSENT` (all unfreeze) versus `UNREACHABLE / reconciler exception → BROKER_STATE_UNKNOWN` (freeze retained). A missing broker response never becomes "the order failed".
- `services/alpha_broker_reconcilers.py` (new) — `reconcile_public` (Public.com REST `get_order` + `get_orders` scan) and `reconcile_moomoo` (OpenD `order_list_query`). Both map broker-native statuses (`FILLED`, `PENDING`, `FILLED_ALL`, `SUBMITTED`, …) into the normalized outcome vocabulary. Any exception downgrades to `UNREACHABLE`.
- SQLite mirror in `alpha_hot_store` DB (`alpha_broker_event_watchdog` table, WAL). Fire-and-forget lifecycle events also stream to `alpha_hot_store.lifecycle_events` for audit alongside existing setup lifecycle.
- Executor wiring:
  - `public_equity_live_executor.maybe_route_live` — pre-submit `is_frozen("public", symbol, account_id)` gate; `register_submission` immediately before `client.place_order`; sync response → `record_event` (FILLED / REJECTED / ACKNOWLEDGED) which cancels the timer. Empty response → `REJECTED("empty_response")`. Raised exception → the 5s timer takes over and reconciles.
  - `moomoo_broker_adapter.submit_equity` — same pattern: pre-submit `is_frozen`, `register_submission` before OpenD `place_order`, `record_event` mapped from `_map_moomoo_status(order_status)` on success, `REJECTED` on synchronous `ret != RET_OK`.
- Admin endpoints (`routes/admin_alpha_daytrader.py`):
  - `GET /api/admin/alpha-daytrader/broker-watchdog?only_frozen=0|1` — list entries.
  - `POST /api/admin/alpha-daytrader/broker-watchdog/clear/{broker}/{client_order_id}` — owner-only manual unfreeze after out-of-band reconciliation.
- Boot: `server.py` startup registers both reconcilers (`[alpha_broker_watchdog] reconcilers registered (public, moomoo)`).
- 54 new tests locking: ACK cancels timer, stale freezes + calls reconciler, FILLED / OPEN / ABSENT / UNREACHABLE routing, reconciler exception → UNKNOWN (freeze retained), no reconciler registered → UNKNOWN, `is_frozen` symbol+account+broker isolation, case-insensitive normalization, `clear_frozen`, terminal-event unfreeze, race condition (ACK between timer fire and callback grabbing the lock).

**3. LULD RoadGuard — narrow equity safety gate (P2).**

- `services/luld_roadguard.py` (new) — `check_luld(symbol, mark_price, context, strict=False) → LULDVerdict`. Blocks on:
  - Explicit `halt_status ∈ {halted, trading_halt, pause}`.
  - Explicit `reopening=True` (post-halt auction / collar transition).
  - Derived LULD proximity — only when caller supplies `reference_price` + `luld_tier` (or explicit `band_high` / `band_low`). Uses Reg NMS bands: Tier 1 = 5%, Tier 2 = 10%, sub-$3 = 20%, sub-$0.75 = min(75%, $0.15/price). Buffer = 0.5% inside the band edge (env `LULD_PROXIMITY_BUFFER_PCT`).
- Deliberately does NOT block on absolute move percentage without a reference price. Fail-open on unknown LULD state (`strict=True` opts in to fail-closed). Prevents the "volatile stock = LULD" overblocking regression the operator called out.
- Enforcement toggle `LULD_ROADGUARD_ENFORCE` (default `1`); `0` = shadow log only.
- Wired into `public_equity_live_executor.maybe_route_live` immediately after `mark = _fetch_mark_price(symbol)` and before the `# Execute` block. Reads `intent["luld"]` context dict. Blocks log `luld_roadguard` observation with the full verdict payload.
- 27 tests covering: Reg NMS tier tables, halt/reopening blocks, upper/lower proximity blocks at 5%/10%/20% bands, within-band-but-outside-buffer allows, explicit bands override derived, unknown state fails open (regression-critical), strict opt-in fails closed, shadow-mode enforcement toggle, bad input safety.

### Test results

**135/135 tests green across the three workstreams:**

- `tests/test_media_auth.py` — 14 (SEC-001)
- `tests/test_alpha_broker_event_watchdog.py` — 16 (FSM + freeze semantics)
- `tests/test_alpha_broker_reconcilers.py` — 38 (Public + MooMoo status mapping + reconciliation)
- `tests/test_luld_roadguard.py` — 27 (narrow scope, fail-open guarantee)
- Plus regression sweep: `test_moomoo_broker_quote_freshness.py` (6), `test_symbol_lock_concurrency.py` (5), `test_alpha_funnel.py` (17), `test_chasing_filter_pattern_aware.py` (21), `test_alpha_family_floor.py` (11), `test_alpha_short_side_exhaustion.py` (7).

Live-verified via testing agent (iteration 197, 133/133 green): unauth POST/DELETE/upload-chunk/list all 401/403; landing-video stays public; authenticated upload persists `uploaded_by` = JWT email; admin watchdog list returns `{entries:[],count:0}` when empty; `POST /clear/public/nonexistent` returns 404 for owner; startup log confirms `[alpha_broker_watchdog] reconcilers registered (public, moomoo)`.

### Env config

- `BROKER_EVENT_STALE_SECONDS` (default `5.0`, clamped 0.05..60.0)
- `LULD_ROADGUARD_ENFORCE` (default `1`)
- `LULD_PROXIMITY_BUFFER_PCT` (default `0.5`, applied inside the band edge)

### Deferred / dropped

- Kraken maintenance-advisory gate (crypto — out of scope this session).
- Kraken stocks/xStocks eval (blocked on API order support).
- `.gitignore .env` block — confirmed by operator as expected; Emergent deploy platform injects env separately.

### Files

- `backend/routes/media.py` (rewritten — SEC-001)
- `backend/services/alpha_broker_event_watchdog.py` (new)
- `backend/services/alpha_broker_reconcilers.py` (new)
- `backend/services/luld_roadguard.py` (new)
- `backend/services/public_equity_live_executor.py` (watchdog + LULD wiring)
- `backend/services/moomoo_broker_adapter.py` (watchdog wiring)
- `backend/routes/admin_alpha_daytrader.py` (2 new watchdog endpoints)
- `backend/server.py` (startup reconciler registration)
- 4 new test files (`test_media_auth.py`, `test_alpha_broker_event_watchdog.py`, `test_alpha_broker_reconcilers.py`, `test_luld_roadguard.py`)

⚠️ **Preview-only.** Click **Save to GitHub** to redeploy so SEC-001 + the watchdog land on `algo-trader-ai-1.emergent.host`.

---


## Latest Update — 2026-09-11 (Alpha Funnel — discernment as a process)

### 🎯 What shipped

Operator architectural directive: convert Alpha from "immediate narrow decision" to a **funnel** where broker research replaces speculation with truth, and candidate state is fluid — a strengthening #11 can overtake a weakening #2. Shipped in one batch per the specification (1-B, 2-C, 3-C, 4-C, 5-C).

Pipeline:
```
5-min discovery (49 candidates) → preliminary rank (12 survivors)
  → broker research (up to 8 — MooMoo primary / Public fallback)
  → deep discernment (top 4) → ARMED (up to 3)
  → 60s stream re-evaluates ARMED on every tick
  → ACTIONABLE only during a single trade attempt (never persists)
```

### Files

- `services/alpha_broker_research.py` (new) — normalized `BrokerResearchSnapshot` contract. v1 populates signal/current/drift/bid/ask/mid/spread_bps/broker_ts_age; the rest of the schema slots (recent_bars, positions, open_orders, buying_power, volume_confirmation) surface as `"not_available"` — never synthesized. `compute_research_delta()` treats spread/drift/staleness as objective hard-blocks; everything else is a re-rank delta (0.69 vs 0.70 is a nudge, not a veto).
- `services/alpha_funnel_state.py` (new) — in-memory promotable candidate registry with a SQLite mirror. Score history logged per candidate (`{delta, reason, from_state, to_state, at_ns}`). Restore rehydrates DISCOVERED/RESEARCH/WATCH; ARMED comes back as WATCH (re-earn), ACTIONABLE/EXECUTED never restored.
- `services/alpha_funnel.py` (new) — orchestrator with env-tunable + runtime-overrideable stage sizes. `run_funnel_cycle()` walks all stages, transitions fluid (backward moves allowed).
- `services/alpha_day_trader.py` — 5-min tick now feeds the funnel after ranking; discovery output preserved in full (no tail chop).
- `services/alpha_top10_stream.py` — 60s stream now prefers the funnel's ARMED list; falls back to the legacy top-10 only on a fresh boot before the first funnel cycle. Tick tag distinguishes `stream:funnel_armed:` vs `stream:top10_legacy:`.
- `server.py` — funnel state restored from SQLite on boot with ARMED → WATCH downgrade.
- `routes/admin_alpha_daytrader.py` — `GET /funnel-state`, `GET /funnel-config`, `POST /funnel-config` (runtime overrides).
- `tests/test_alpha_funnel.py` (new) — 17/17 tests green.

### Guardrails locked in tests

- Discovery `max_n` caps NEW additions only; existing candidates never displaced (operator rule: don't shrink Alpha's field of view).
- Score deltas ALWAYS carry a reason string — history bounded at 40 entries.
- Backward transitions supported (ARMED → WATCH on deterioration).
- Strengthening lower candidate overtakes weakening leader (test_rank_promotes_strengthening_over_leader).
- Restore downgrades ARMED to WATCH (never auto-execute after restart).
- ACTIONABLE / EXECUTED skipped on restore.
- Broker research: stale quote / over-spread / over-drift / missing price hard-block; "confidence gap" doesn't (0.69 vs 0.70 is a delta).

### Env config (all runtime-overrideable via `POST /funnel-config`)

- `ALPHA_FUNNEL_DISCOVERY` (default 40)
- `ALPHA_FUNNEL_SURVIVORS` (default 12)
- `ALPHA_FUNNEL_BROKER_RESEARCH` (default 8)
- `ALPHA_FUNNEL_DEEP_DISCERNMENT` (default 4)
- `ALPHA_FUNNEL_PROMOTED` (default 3)

### Live verification

Post-restart on preview (after-hours): forced tick ran, 10 candidates ingested, 8 went to broker research, all 8 hard-blocked correctly (no live broker quote in off-hours — exactly the intended behavior), 0 armed, 0 execution attempts. The funnel refuses to promote on missing broker truth — proving the design is functioning as specified rather than fabricating confidence.



## Latest Update — 2026-09-11 (Foundation v2.1 port — 4 fixes in one batch)

### 🎯 What shipped

Operator uploaded `RISEDUAL_Foundation_v2.1.zip` (reference implementation) and requested four fixes ported in parallel: hardware kill-switch fail-closed, RVOL time-of-day baseline, short-sale capability gate, and NYSE session-state banner. All four landed in one batch, 18 new tests green, live-verified.

### Files

- `services/alpha_hardware_kill_switch.py` (new) — SQLite-backed fail-closed kill switch. Distinct from the operator discipline profile — this is a hardware halt for corrupt state / consecutive errors / drawdown breach. Any malformed row returns TRIPPED with `FAIL_CLOSED:` reason instead of silently permitting trading.
- `services/alpha_volume_baseline.py` (new) — time-of-day RVOL baseline. Same-slot UTC bucket compare vs prior dates. Returns `None` when fewer than 5 samples exist — never fabricates 1.0. Legacy daily-avg RVOL stays as fallback until the baseline warms up.
- `services/alpha_session_state.py` (new) — NYSE session phase with holiday awareness. Enumerated 2026 holidays (incl. Labor Day) and half-day early closes. Renders as a coloured banner on the Alpha panel so "0% intent → broker" on a holiday doesn't look like a bug.
- `services/alpha_day_trader.py` — `_snapshot_symbol` now `observe()`s each bar and prefers the time-of-day RVOL when the baseline is ready; falls back to the legacy daily-avg otherwise.
- `services/public_equity_live_executor.py` — module-level `SUPPORTS_SHORT_SALES=False` capability flag. `maybe_route_live` now hits the HW kill switch AND the short-sale gate at entry, so any `SHORT`/`SELL_SHORT`/`OPEN_SHORT` intent is logged as `short_signal_only` and never submitted.
- `routes/admin_alpha_daytrader.py` — new endpoints: `GET /session-state`, `GET|POST /hw-kill-switch`, `POST /hw-kill-switch/trip`, `POST /hw-kill-switch/reset`.
- `frontend/src/components/admin/AlphaDayTraderPanel.jsx` — session-state banner above "Today lifecycle" (amber on holidays, zinc on weekends/pre-market/after-hours); `SHORT · SIGNAL ONLY` badge on any pattern whose name starts with `short_`, ends in `_breakdown`, contains `_reject`, or equals `failed_breakout`.
- `tests/test_foundation_v21_ports.py` (new) — 18/18 tests green.

### Guardrails locked in tests

- HW kill switch: corrupt-JSON row → `FAIL_CLOSED:state_not_json`; missing required keys → `FAIL_CLOSED:state_missing_required_keys`; N consecutive errors → tripped; drawdown breach → tripped.
- RVOL: returns `None` during warm-up (< 5 samples); UTC slot bucketing is correct (14:33 and 14:44 share a bucket, 14:59 doesn't); zero/negative volumes are dropped.
- Session state: Labor Day 2026 → `closed_holiday` with holiday name; Wednesday RTH → `regular`; Thanksgiving half-day → `regular` before 13:00 ET, `after_hours` after; weekends → `closed_weekend`.

### Env config

- `PUBLIC_LIVE_SUPPORTS_SHORTS` (default `0`) — override when a broker adapter with shorts is wired
- `ALPHA_HW_KILL_MAX_ERRORS` (default `10`), `ALPHA_HW_KILL_MAX_DRAWDOWN_PCT` (default `20`)
- `ALPHA_RVOL_MIN_SAMPLES` (default `5`), `ALPHA_RVOL_LOOKBACK_DAYS` (default `30`), `ALPHA_RVOL_SLOT_MINUTES` (default `15`)

### Live verification

Post-restart on preview: session-state endpoint returned `closed_overnight` (it's after 20:00 ET), HW kill switch state clean at default, forced tick ran (`candidates_seen: 49`) and the RVOL baseline observed each snapshot symbol. The full end-to-end wiring is confirmed.



## Latest Update — 2026-09-07 Labor Day (Compact Authority Receipts)

### 🎯 What shipped

Every resolved trade now produces ONE compact Mongo document that summarises the full authority chain (brain vote → wave intelligence → trigger → intent → seat authority → RoadGuard → broker capability → broker submit). Raw lifecycle events stay in the SQLite hot store (unbounded, cheap to prune); Mongo gets a single bounded-volume audit summary per trade.

### Files

- `services/alpha_authority_receipt.py` (new) — assembler that reads SQLite lifecycle events + joins Mongo outcome + writes one compact upsert receipt keyed on `setup_id`. Idempotent by design.
- `services/alpha_day_trader.py` — `_record_outcome` now fires `alpha_authority_receipt.build_and_persist` as fire-and-forget after every Mongo outcome insert. Receipt failure never affects trade path.
- `routes/admin_alpha_daytrader.py` — 3 new endpoints:
  - `GET /api/admin/alpha-daytrader/authority-receipts?limit=50`
  - `GET /api/admin/alpha-daytrader/authority-receipts/{setup_id}`
  - `POST /api/admin/alpha-daytrader/authority-receipts/rebuild/{setup_id}` (for late-arriving broker events)
- `tests/test_alpha_authority_receipt.py` (new) — 9/9 tests green

### Design contract

- **Never fabricate a pass.** Stages that had no lifecycle event surface as `"not_recorded"`, not fake authority grants.
- **First-in-time block wins.** `failure_stage` attributes the failure to the earliest blocking event.
- **Idempotent persistence.** Upsert by `setup_id` — safe to rebuild from the hot store after late broker events.
- **Bounded Mongo growth.** One doc per trade forever; SQLite carries the detail stream.

### Live verification

After a scheduled tick post-restart, an invalidated setup (`GFAIW`) automatically produced a receipt reporting `final_status: blocked_at_trigger`, `authority_verified: False`, `failure_stage: trigger` — visible via the list endpoint. Exactly the "prove the authority path" audit the brief called for.

### Queued behind this

- Kraken maintenance-advisory gate (hard block on `imminent_5m` / `final_warning_30s` per operator)
- Webull order-event watchdog (5s threshold → `broker_event_stale` per operator)
- LULD RoadGuard input for equities
- Kraken stocks/xStocks evaluation (deferred until API-order confirmed for the account)



## Latest Update — 2026-09-04 (Trade Discernment Layer + Claude Opus 4.8 integration)

### 🎯 What shipped

Same-session postmortem infrastructure that reconstructs setup features at detection + trigger, joins with resolved outcomes, computes seven candidate discriminators, and hands the structured comparison to Claude Opus 4.8 for narrative synthesis. All persistence lands in the same SQLite hot-store DB (Mongo untouched). Explicitly designed as data collection for future learning — no ticker-specific rules, no new hard trading gates from small samples.

### Files

- `services/alpha_discernment_postmortem.py` (new) — full pipeline (extract → compute → compare → synthesize → persist)
- `routes/admin_alpha_daytrader.py` — added `POST /api/admin/alpha-daytrader/discernment-postmortem` (run) and `GET /api/admin/alpha-daytrader/discernment-postmortem?session_date=...` (fetch latest)
- `tests/test_alpha_discernment_postmortem.py` (new) — 12 tests covering discriminators, group comparison, end-to-end persistence, and mocked Claude wrapper

### Seven candidate discriminators

Every feature is optional; missing values stay `None` (never fabricated).

1. `move_maturity_pct` — completed intraday move at detection (over-extension signal)
2. `rvol_delta_detection_to_trigger` — RVOL delta from detection to trigger (fading volume signal)
3. `price_delta_pct_detection_to_trigger` — price movement between detection and actual trigger fire (momentum deceleration signal)
4. `relative_strength_vs_spy` / `relative_strength_vs_qqq` — mirrored from payload when populated
5. `regime_pattern_mismatch` — 1.0 when the pattern fights the regime (momo in chop, mean-revert in trend), 0.0 when aligned, `None` when unclassifiable
6. `detect_to_trigger_ms` — elapsed time from detection to trigger firing (late-trigger signal)
7. `reward_over_risk` — `|target - trigger| / |trigger - invalidation|` computed against the setup's own levels

### SQLite schema (new tables in the hot-store DB)

`alpha_discernment_features` — one row per reconstructable setup with `features_at_detection`, `features_at_trigger`, `outcome`, and `discriminators` as JSON blobs plus `group_label` (`better` / `poorer` / `unlabeled`).

`alpha_discernment_postmortems` — one row per postmortem run with `better_group`, `poorer_group`, `comparison` JSON (feature-by-feature mean delta, direction, sample size, `insufficient_data` markers), `narrative` (Claude Opus 4.8 synthesis), and `warnings`.

### Claude Opus 4.8 wiring

- Provider: `anthropic` via `emergentintegrations.llm.chat.LlmChat`
- Model: `claude-opus-4-8`
- Auth: `EMERGENT_LLM_KEY` (Universal Key)
- System prompt hard-codes the operator's guardrails: no ticker-specific rules, no new hard gates from small samples, flag insufficient-sample features, terse prose only
- Fails open: missing key → placeholder narrative + `model_used="none"`; API error → error text + `model_used="error"`. Postmortem still lands.

### Live verification

`POST /api/admin/alpha-daytrader/discernment-postmortem` with `{"session_date":"2026-09-02","better_group":["WMT","QQQ"],"poorer_group":["TSLA","NFLX"]}` returned an empty reconstructable set on the preview pod (as expected — those trades were on production, not preview) with clear per-symbol warnings. The service correctly reports what it can and cannot see. Same call on production will produce the actual analysis.

### Testing

12/12 discernment tests green: unit (3 regime-mismatch cases, 2 discriminator paths, 2 comparison scenarios), integration (2 end-to-end SQLite persistence + graceful missing-data), and Claude wrapper (2 mocked — invocation + missing-key fallback).



## Latest Update — 2026-09-03 (Code review response — HIGH + MEDIUM defect fixes)

### 🎯 Findings from Code Review Agent

Two material defects surfaced after Streaming Top-10 shipped:

**HIGH — Concurrent double-buy race on the shared symbol lock.**
`alpha_hot_store.try_acquire_symbol_lock` had a "same setup_id + same source ⇒ idempotent re-acquire ⇒ return True" branch. Meant for a single tick retrying its own acquire, but the 5-min `alpha_day_trader` and 60s `alpha_top10_stream` jobs read the SAME active-setup doc → identical `setup_id` + identical `source="alpha_daytrader"` → BOTH concurrent ticks passed the lock → BOTH could submit a broker order for the same symbol.

**Fix:** Removed the idempotent re-acquire branch entirely. Any second acquire on a non-expired lock — same or different setup_id/source — now returns False. The loser's tick records `exec_lock_conflict` and continues. Callers acquire once per setup per tick; no legitimate re-acquire exists. `try_acquire_symbol_lock` default TTL bumped from 90s → 120s to match both call-sites which already passed 120s explicitly.

**MEDIUM — MooMoo-first broker quote defeated the freshness gate.**
The initial MooMoo-primary path stamped `fetched_at` as an ISO string (`datetime.now(...).isoformat()`), but `provider_policy._quote_age_seconds` does `float(ts)` — which raises `ValueError` on ISO strings and returns `None`. Effect: every MooMoo quote was seen as "unknown age" and conservatively rejected, silently neutralising the primary-broker path.

**Fix:** `fetched_at` is now unix-seconds-float (matching Public.com's format from `_dispatch_quote`). Additionally, MooMoo's `data_time` (wire timestamp in ET) is now parsed via `zoneinfo.ZoneInfo("America/New_York")` when present, so a stale previous-close price registers a real age and the freshness gate can reject it. Missing/unparseable `data_time` falls back to `now()` and lets the drift-witness path catch it.

**LOW — Dead ternary in `set_auth_cookies`.**
`samesite_val = "lax" if is_secure else "lax"` — both branches identical. Simplified to `samesite_val = "lax"`. No functional change.

### Tests

`tests/test_symbol_lock_concurrency.py` — 5 new:
- `test_second_acquire_with_same_setup_id_now_blocks` (the double-buy regression)
- `test_second_acquire_with_different_source_still_blocks` (cross-scanner dedup preserved)
- `test_release_frees_lock_for_next_caller`
- `test_expired_lock_is_takeable`
- `test_different_symbols_do_not_conflict`

`tests/test_moomoo_broker_quote_freshness.py` — 6 new:
- `test_moomoo_quote_fetched_at_is_unix_seconds_float` (the critical format-bug regression)
- `test_moomoo_stale_data_time_produces_large_age` (2020 timestamp → 6+ years age)
- `test_moomoo_missing_data_time_falls_back_to_now`
- `test_moomoo_zero_last_price_falls_through_to_public`
- `test_moomoo_disabled_via_env_skips_primary`
- `test_moomoo_exception_falls_through_gracefully`

**11 new tests + 46 adjacent (CSRF, Top-10 state/stream, chasing filter) — all 57 green.** Live smoke-test post-restart: tick with X-Requested-With returns HTTP 200 with valid summary.

### Deferred (LOW severity, no current impact)

- CSRF skip list uses substring match on `/webhook`, `/oauth`. Current authenticated routes are safe; a future route containing those substrings would silently bypass. Prefer prefix/exact matching in the next hardening pass.
- `/api/auth/refresh` is CSRF-exempt. Forged refresh only rotates cookies the attacker cannot read (httpOnly), so impact is negligible; noted for the record.

### Files

- `services/alpha_hot_store.py` — removed idempotent re-acquire branch
- `services/market_data_pool.py` — MooMoo `fetched_at` as unix-seconds-float with wire-time parsing
- `routes/auth.py` — dead ternary simplification
- `tests/test_symbol_lock_concurrency.py` (new)
- `tests/test_moomoo_broker_quote_freshness.py` (new)



## Latest Update — 2026-09-03 (Streaming Top-10 + SEC-002 CSRF hardening)

### 🎯 What shipped

Three requested workstreams in one pass. Alpha still trades on the 5-min cadence for the wider universe, but the highest-opportunity 10 symbols now get re-evaluated every 60s against broker-fresh quotes. CSRF is layered defense: SameSite=Lax at the app layer + a mandatory custom header on cookie-authenticated state-mutating routes.

### Streaming Top-10 — architecture

New files:

- `services/alpha_top10_state.py` — in-process watchlist (10 symbols max) with a Mongo mirror for the diagnostic panel. Deduplicates, truncates, drops blanks; `get_top10()` returns a defensive copy.
- `services/alpha_top10_stream.py` — 60s tick that delegates to `run_alpha_day_trader_tick(db, symbols_only=<top10>)`. Empty watchlist → `skipped: no_watchlist`, never blows up.

Refactored:

- `services/alpha_day_trader.run_alpha_day_trader_tick` now accepts `symbols_only: Optional[set[str]]` and `tick_tag: Optional[str]`. When `symbols_only` is set, the tick SKIPS universe scan + ranking + wave veto entirely and runs only the active-setup trigger loop, filtered to those symbols. The 5-min tick continues to run as before and seeds the top-10 after ranking.
- `services/market_data_pool.fetch_broker_quote` now tries MooMoo OpenD first (`snapshot_quote`) then falls back to Public.com REST. Controlled by `ALPHA_BROKER_QUOTE_PREFER_MOOMOO` (default on).
- `services/scheduling/jobs.py` — added `scheduler.add_job(s._run_alpha_top10_stream, 'interval', seconds=60, id='alpha_top10_stream')`.
- `server.py` — `_run_alpha_top10_stream` server callback.

Live verification:

```
14:24:49 [alpha_top10] refreshed n=10 symbols=MSAIW,CRD,EO,AVGO,SLDPW,VIOT,LCFYW,ABT,HUBCZ,AMZN source=5min
14:25:22 [alpha_daytrader] tick streaming=True tick_tag=stream:2026-09-03T14:25:22Z
```

### SEC-002 CSRF hardening — two-layer defense

Layer 1 — `routes/auth.set_auth_cookies`: default SameSite changed from `none` → `lax`. Operators who genuinely need cross-site cookies can override via `AUTH_COOKIE_SAMESITE=none|lax|strict`. (Observation: the preview Kubernetes/Cloudflare ingress rewrites `SameSite=Lax` back to `SameSite=None; Partitioned` on the wire because the preview URL is iframe-embedded by the Emergent builder. Layer 2 covers this case.)

Layer 2 — `services/csrf_middleware.CSRFHeaderMiddleware`: rejects any cookie-authenticated `POST/PUT/PATCH/DELETE` under `/api` that doesn't carry `X-Requested-With: XMLHttpRequest`. Bearer-token clients are exempt (CSRF-immune by construction). Skips pre-auth endpoints (`/api/auth/login`, `register`, `refresh`, `forgot-password`, `reset-password`) and webhook/oauth callbacks. `CSRF_ENFORCE=0` puts the middleware in shadow-log mode for staged rollout.

Frontend — `utils/csrfDefaults.js`: sets `axios.defaults.headers.common['X-Requested-With'] = 'XMLHttpRequest'` and monkey-patches `window.fetch` so raw-fetch pockets in the codebase inherit the header. Loaded once from `index.js` before any component mounts.

Live verification against preview:

```
--- POST WITHOUT X-Requested-With → HTTP 403 {"code":"csrf_header_missing"} ---
--- POST WITH X-Requested-With    → HTTP 200 {"candidates_seen":49,...} ---
```

### Tests

- `tests/test_alpha_top10_state.py` — 7 tests (dedup, truncation, blank drop, defensive copy, replace, clear)
- `tests/test_alpha_top10_stream.py` — 4 tests (skip on empty watchlist, skip on no db, delegate with symbols_only, verbatim symbols)
- `tests/test_csrf_middleware.py` — 14 tests (mutating vs read-only, cookie vs bearer, skip list, options, shadow mode, case-insensitive header)

**25 new tests green, 45+ existing adjacent tests still green.**

### Skipped

Pattern Coverage Dashboard — operator declined ("Don't need another tab or filter or panel").

### Files

- `services/alpha_top10_state.py` (new)
- `services/alpha_top10_stream.py` (new)
- `services/csrf_middleware.py` (new)
- `services/alpha_day_trader.py` — `symbols_only` + `tick_tag` parameters, top-10 seed after ranking
- `services/market_data_pool.py` — MooMoo-first broker quote path
- `services/scheduling/jobs.py` — 60s stream job registration
- `server.py` — `_run_alpha_top10_stream` callback + CSRFHeaderMiddleware registration
- `routes/auth.py` — SameSite=Lax default with `AUTH_COOKIE_SAMESITE` override
- `frontend/src/utils/csrfDefaults.js` (new)
- `frontend/src/index.js` — install CSRF defaults on boot
- Three new test files



## Latest Update — 2026-09-03 (Chasing filter blocking dip-buy patterns — Alpha "not trading" today)

### 🎯 What was actually killing execution

Live `why-not-trade` at 13:56 UTC showed the pipeline working end-to-end:
`4 setups → 2 triggered → 2 intents → 2 broker-confirmed → 0 submitted`.

Both surviving intents died at the executor's `chasing_filter`:

- **ANET** — `inverse_head_and_shoulders` (classical bullish reversal / dip-buy)
- **GPRO** — `short_side_exhaustion` (mean-revert deep-dip bounce)

Two independent bugs in `public_equity_live_executor._maybe_route_live` chasing filter:

1. **Classical reversal patterns misclassified.** `double_bottom`, `inverse_head_and_shoulders`, `falling_wedge` were only in `CLASSICAL_PATTERNS`, not `MEAN_REVERT_PATTERNS`, so they fell through to the unknown-`abs()` branch. Any −4% dip (the exact setup they detect) blocked them.
2. **`SHORT_SIDE_EXHAUSTION` knife guard too tight.** Mean-revert branch capped negative moves at `−2× cap = −8%`, but this pattern's designed firing range extends to `−12%`. Deep-dip bounces were cut off before their sweet spot.

### The fix

`services/public_equity_live_executor.py`:

- Introduced `is_dip_buy = setup_type in MEAN_REVERT_PATTERNS or setup_type in CLASSICAL_PATTERNS` — all classical bullish reversals now share the dip-buy branch (allow negative moves, guard against knife-catch).
- Widened knife guard to `−3× cap = −12%` for `SHORT_SIDE_EXHAUSTION` specifically, keeping `−2× cap = −8%` for other mean-revert / classical patterns.

### Tests

`tests/test_chasing_filter_pattern_aware.py` — 6 rewritten/new:

- `test_short_side_exhaustion_allows_designed_deep_range` — GPRO regression: -10% at cap 4% must NOT be blocked
- `test_short_side_exhaustion_still_blocks_free_fall` — -13% still blocked
- `test_non_exhaustion_mean_revert_still_blocks_at_minus_two_times_cap` — range_low_bounce keeps its -8% ceiling
- `test_classical_reversal_pattern_allows_negative_dip_buy` — ANET regression: -6% dip on inverse_h&s / double_bottom / falling_wedge must NOT block
- `test_classical_reversal_still_blocks_extreme_knife_catch` — -10% still blocks
- `test_classical_reversal_blocks_chasing_top` — +5% still blocks (buying a top is buying a top)

**21 chasing-filter tests green + 45 adjacent tests (classical patterns, short-side exhaustion, options-status, integration) green.**

### Files

- `services/public_equity_live_executor.py` — dip-buy family unified; SHORT_SIDE_EXHAUSTION gets widened knife guard
- `tests/test_chasing_filter_pattern_aware.py` — new/rewritten regression tests

### Note on yesterday's NFLX

Every Alpha tick logged yesterday (2026-09-02) shows `broker_submitted: 0`. Yesterday's NFLX buy likely came through the parallel `day_trade_scanner` or `smart_order_service` path, not Alpha. Alpha's execution path has been broken both days; today's `why-not-trade` made it visible.



## Latest Update — 2026-02 (Alpha Vantage excluded from drift-witness role — was killing every intent live)

### 🎯 What was actually killing execution

Live query against preview at Monday 10:12 ET (market open ~42 min): 4 intents per hour, ALL killed at the same gate:

```
ADBE: broker=$285.80 (age 0.00001s) vs vendor=$292.79 → drift=244.6bps → data_conflict → BLOCKED
```

Root cause: Alpha Vantage's free-tier `GLOBAL_QUOTE` endpoint silently returns a **15-minute-delayed** price and stamps it with the receive-time timestamp we assigned. So `fetched_at` looks fresh (0.00s) but the actual price is 15 min old. Our drift check saw a "244 bps disagreement" every single tick — it wasn't a real disagreement, it was AV's delay policy pretending to be current data.

### The fix

`services/provider_policy.py` now maintains an explicit `DELAYED_QUOTE_PROVIDERS = {"alphavantage"}` set. A vendor whose `provider_name` starts with any of those is:

* Still surfaced in `ExecutionQuote` for observability (`vendor_price`, `disagreement_bps` populated)
* Never allowed to set `data_conflict=True`
* Still usable as a fallback price for `no_broker_price` context

Matching is prefix + lowercase, so `alphavantage`, `alphavantage-backup`, and `ALPHAVANTAGE-primary` all bypass veto — a rename can't accidentally re-arm it.

Also added `EXECUTION_VENDOR_MAX_AGE_SECS=30` — a second gate that catches any FUTURE vendor whose `fetched_at` stamp actually reflects the source-time age (e.g. if we add a provider that stamps the exchange's tick time). Defense-in-depth.

### Tests

`tests/test_provider_policy.py` — 3 new:
* `test_stale_vendor_cannot_veto_fresh_broker` — exact ADBE $285.80 vs $292.79 case; must not conflict, must allow execution
* `test_alphavantage_name_variants_all_bypass_drift` — prefix matching (`alphavantage-backup`, `ALPHAVANTAGE-primary`) all correctly bypass
* `test_vendor_age_boundary_at_configured_ceiling` — 30s ceiling behaviour for future non-delayed providers
* `test_vendor_missing_fetched_at_does_not_veto_fresh_broker` — unknown age is conservative

**411 tests green** (alpha + policy + broker + chasing + universe + regime). Live smoke test confirmed: 0 `data_conflict` blocks in the 90s post-restart window (vs 3 in the 300s pre/post window).

### Files

- `services/provider_policy.py` — added `DELAYED_QUOTE_PROVIDERS`, `EXECUTION_VENDOR_MAX_AGE_SECS`, drift check gated on both freshness + non-delayed provider
- `tests/test_provider_policy.py` — 3 new regression tests

⚠️ **Preview-only.** Production still has the pre-fix drift check that will keep killing every intent Monday. Redeploy required.

---


## Latest Update — 2026-02 (Chasing filter no longer blocks dip-buy setups)

### 🎯 What the diagnostic revealed

Live Sunday-afternoon query against the 24h Why-Not-Trade window:
```
9 setups → 5 triggered → 5 intents → 1 broker-confirmed → 0 submitted
```
That single broker-confirmed intent (ADBE, $289.64, fresh broker quote) was killed by `executor_rejected: chasing_filter`. Root cause: the filter used `abs(move_pct) >= cap` which blocked BUYs on any ≥4% move — up OR down. That's correct for momentum "chasing the top" but wrong for every mean-reversion pattern that fires ON a dip (SHORT_SIDE_EXHAUSTION explicitly targets -3% to -12% moves, so 100% of its qualifying candidates were guaranteed to be blocked).

### The fix

`services/public_equity_live_executor.py::maybe_route_live` chasing gate is now pattern-aware:

| Pattern family | Block rule |
|---|---|
| **Momentum** (BREAKOUT, HOD_BREAK, VWAP_RECLAIM, MOMENTUM_REACCELERATION) | `move_pct >= cap` — only the original "chasing top" case |
| **Mean-revert** (SHORT_SIDE_EXHAUSTION, RANGE_LOW_BOUNCE, VWAP_FADE_LONG, OPENING_DRIVE_FADE, PULLBACK) | `move_pct >= cap` OR `move_pct <= -2× cap` — buy-the-dip allowed, but still block catch-a-knife (< -8% at cap 4%) |
| **Unknown / classical** (DOUBLE_BOTTOM, IHS, FALLING_WEDGE, blank) | `abs(move_pct) >= cap` — keep the historical safe default |

Data-outage fail-open behaviour is preserved: `_intraday_move_pct` returning None → allow the trade (a provider hiccup must never punish a legit signal).

### Also expanded

`why-not-trade` diagnostic window: max lifted from 24h → 7 days so an operator on Sunday can query Friday's session (hot store retains 14 days).

### Tests

`tests/test_chasing_filter_pattern_aware.py` — 18 tests: every mean-revert family allowed on -6% moves, extreme knife-catch still blocked, boundary at -2× cap, momentum families still block +5% chase, momentum allowed on -3.5% pullback, classical/unknown patterns keep abs() safety, data-outage fail-open.

Full sweep: **423 alpha/policy/broker/chasing/universe tests green**. Backend healthy (689 routes).

### Files

- `services/public_equity_live_executor.py` — pattern-aware chasing filter
- `services/alpha_why_not_trade.py`, `routes/admin_alpha_daytrader.py` — 7-day window support
- `tests/test_chasing_filter_pattern_aware.py` (new)
- `tests/test_alpha_why_not_trade.py` (updated clamp test)

⚠️ Preview-only — click **Save to Github** to redeploy so the fix takes effect Monday's open.

---


## Latest Update — 2026-02 (Broker execution circuit breaker — Alpha stops pounding a wedged Public.com)

### 🎯 What this fixes

The Why-Not-Trade diagnostic (see earlier entry) revealed that `execution_quote_blocked` was the new ceiling — Alpha creates intents fine, but Public.com either times out or doesn't quote AV small-cap movers. Without a breaker, every tick still fires the same 10s HTTP timeout for each blocked symbol, starving the loop of time it should spend on healthy candidates.

### State machine

`services/broker_circuit_breaker.py` (module-level singleton, thread-safe):

| State | Behaviour |
|---|---|
| `CLOSED`   | Healthy. Every `fetch_broker_quote` call goes through. Failures counted in rolling window. |
| `OPEN`     | Degraded. `fetch_broker_quote` returns `None` immediately (no HTTP). `fetch_execution_quote` marks results `broker_degraded` and still blocks auto-execute (we NEVER submit without broker confirmation). |
| `HALF_OPEN` | After cooldown, next call is allowed as a probe. Success → CLOSED. Failure → OPEN for another full cooldown. |

Env-tunable defaults: `BROKER_CB_ERROR_THRESHOLD=5`, `BROKER_CB_WINDOW_SECONDS=300`, `BROKER_CB_COOLDOWN_SECONDS=120`. All values gracefully fall back to defaults on malformed env.

### Why-Not-Trade distinguishes coverage from outage

`fetch_execution_quote` now reports two different `reason` values when the broker is silent:

* **`no_broker_price`** — CLOSED breaker + broker returned None = symbol not in coverage. Expected for AV small-cap movers like KLXER.
* **`broker_degraded`** — OPEN breaker = Public.com is throttled / down. Operator sees this distinctly in the diagnostic.

### Admin endpoints

* `GET /api/admin/alpha-daytrader/broker-circuit` — state, failures-in-window, cooldown remaining, last transition reason (read-only, admin).
* `POST /api/admin/alpha-daytrader/broker-circuit/reset` — force CLOSED (owner-only) for known-transient outages.

### Tests

`tests/test_broker_circuit_breaker.py` — 14 tests:
* State machine: starts CLOSED, trips at threshold, stays CLOSED below threshold, success from CLOSED tracks counter
* Cooldown/recovery: OPEN → cooldown elapses → HALF_OPEN, probe success → CLOSED, probe failure → OPEN for full cooldown
* Rolling-window pruning (old failures don't count)
* Manual reset
* `fetch_broker_quote` short-circuits with zero HTTP calls when OPEN
* `fetch_broker_quote` records failures on provider exceptions and trips at threshold
* `fetch_execution_quote` reports `broker_degraded` vs `no_broker_price` correctly

Full sweep: **402 tests green** (alpha + policy + broker + universe + regime).

### Files

- `services/broker_circuit_breaker.py` (new)
- `services/market_data_pool.py` — `fetch_broker_quote` wrapped with breaker guard + success/failure recording
- `services/provider_policy.py` — `fetch_execution_quote` distinguishes `broker_degraded` from `no_broker_price`
- `routes/admin_alpha_daytrader.py` — new `/broker-circuit` GET + `/broker-circuit/reset` POST
- `tests/test_broker_circuit_breaker.py` (new)

⚠️ Preview-only — click **Save to Github** to redeploy so the circuit breaker takes effect in production.

---


## Latest Update — 2026-02 (Selective floor + live movers + short-breakdown pattern — Alpha starts firing setups)

### 🎯 Went from 0 → 2 setups per tick

The Why-Not-Trade diagnostic revealed the real bottleneck: 8/10 candidates were dying at the flat 0.447 opportunity floor even when they had NVDA-shaped features. After this patch:

**Live tick, market closed**:
```
48 candidates → 10 ranked → 2 setups detected → 2 triggered → 2 intents → 0 broker-confirmed → 0 submitted
```

The new ceiling is broker coverage — AV's live movers (AESPW, ANGHW, KLXER, etc.) aren't quoted by Public.com, so the execution-quote gate correctly refuses to fire on them (`no_broker_price` / `data_conflict`). This is the *right* failure mode; the circuit breaker item now makes real sense as the next step.

### What shipped

**1. Selective opportunity floor** (`_family_floor(snap, default_floor)`):

| Family | Rule | Floor |
|---|---|---|
| `penny_breakout` | price ≤ $5, +3%+, rvol ≥ 2.0 | 0.38 |
| `short_breakdown` | pct ≤ -2%, rvol ≥ 0.5 | 0.36 |
| `large_cap_momo` | price ≥ $20, +0.5%+, rvol ≥ 1.5 | 0.35 |
| `low_vol_no_news` | anything else | 0.447 |

Order matters — penny is checked BEFORE large-cap so a $3 stock on 3× rvol doesn't sneak through the 0.35 floor. Every rejection is logged with `family_tag` so the diagnostic shows which family the candidate landed in.

**2. Live movers universe** (`services/alpha_live_movers.py`) — Alpha Vantage `TOP_GAINERS_LOSERS` polled at most every 120s (env-tunable, clamped 30..3600s to protect the 25/day free-tier quota), cached in Mongo `alpha_live_movers` singleton, fails closed to the previous cache on any AV error / rate-limit. Wired as source #2 in `_candidate_universe`, right after operator watchlist. Gainers → losers → actives ordering; deduped against every other source.

**3. `SHORT_SIDE_EXHAUSTION` pattern** — a mean-reversion long entry for PLTR-shaped moves:
- `pct_change` in [-12%, -3%] (sharp but not free-fall)
- `relative_volume` in [0.5, 3.0] (not still panicking)
- Current bar stabilizing: `price ≥ low * 1.003` OR `price ≥ open * 0.995`
- Base score 0.58, mean-revert family so it gets the chop boost

Public.com is cash-only, so the pattern buys the bounce — it never opens a short.

### Tests

- `tests/test_alpha_family_floor.py` (11): every family, boundary cases, precedence rules (penny before large-cap), spec guardrail
- `tests/test_alpha_short_side_exhaustion.py` (7): PLTR shape fires, free-fall doesn't fire, panic-rvol doesn't fire, open-price fallback works, family classification regression
- `tests/test_alpha_live_movers.py` (14): AV parse, rate-limit handling, TTL, cache-on-error fallback, dedupe across gainers/losers/actives, env clamping
- `tests/test_alpha_candidate_universe.py` — added 3 tests for the movers source: order, failure survival, cross-source dedup

Full alpha/policy sweep: **350 tests green**.

### Files

- `services/alpha_live_movers.py` (new)
- `services/alpha_day_trader.py` (adds `_family_floor`, wires `_candidate_universe` source #2, adds `SHORT_SIDE_EXHAUSTION` detector + enum, extends `MEAN_REVERT_PATTERNS`)
- `tests/test_alpha_family_floor.py`, `tests/test_alpha_short_side_exhaustion.py`, `tests/test_alpha_live_movers.py` (new)
- `tests/test_alpha_candidate_universe.py` (extended)

⚠️ Preview-only — click **Save to Github** to redeploy so the new floors, live movers and short-side pattern take effect in production.

---


## Latest Update — 2026-02 (Why-Not-Trade diagnostic — Alpha now tells you exactly which gate killed each candidate)

### 🎯 The operator's #1 question, answered by one HTTP GET

`GET /api/admin/alpha-daytrader/why-not-trade?since_seconds=300` returns a per-gate rollup of every rejection in the window: gate name → count, distinct symbols, sub-reason distribution, sample payloads. Joins the SQLite hot store (per-tick lifecycle) with Mongo `alpha_outcomes` (resolved rollup) so a single endpoint tells the whole story.

### First live query immediately surfaced the actual bottleneck

Tick: 50 candidates → 10 ranked → 0 setups → 0 submissions. Endpoint said:

- **8 of 10** killed at `opportunity_score_rejected` — opp scores 0.33–0.36 vs floor 0.4472 (~sqrt(0.2))
- **2 of 10** killed at `no_pattern_match` — e.g. PLTR: `pct_change=-7.55%, rvol=0.76, slow_regime=choppy_meanrevert, fast_regime=trend_up`. Move too big to mean-revert, regime mismatch blocked momentum.

### Registered gates (8 total)

**Pre-pattern (new instrumentation)** — used to be silent:

1. `opportunity_score_rejected` — opp score below floor, never reached pattern engine
2. `wave_danger_pause` — Wave Intelligence per-symbol veto
3. `no_pattern_match` — cleared opp+wave, but pattern engine found no shape (dumps snap features so operator can eyeball sensitivity)

**Setup lifecycle** — already emitting observations:

4. `invalidated` — shape broke before trigger
5. `intent_deduplicated` — fingerprint hash matched an in-flight ticket
6. `exec_lock_conflict` — cross-scanner lock already held
7. `execution_quote_blocked` — broker-first freshness/drift gate (stale, drift > 50 bps, no broker price)
8. `executor_rejected` — downstream executor said no

Every response also carries pass-through totals: `setups_detected`, `triggers_fired`, `intents_created`, `execution_quote_confirmed`, `broker_submitted`.

### Files

- `services/alpha_why_not_trade.py` (new) — aggregator with per-gate rollup
- `services/alpha_hot_store.py` — added `events_since(since_ns, events, limit)` read helper
- `services/alpha_day_trader.py` — added 3 pre-pattern `_record_observation` calls
- `routes/admin_alpha_daytrader.py` — added `GET /why-not-trade` route
- `tests/test_alpha_why_not_trade.py` (new) — 10 tests including a guardrail that fails CI when a new `_record_observation` rejection event isn't registered in `REJECTION_GATES`

### Also fixed in this session

- **Test suite** 4796/0 (was 4785 / 2 failed):
  - `test_no_unguarded_mongo_datetime_math` — regex missed `timedelta` aliases like `_td(...)`, fixed with a call-expression negative lookahead
  - `test_broker_sort_and_watchlist_merge` (10 subtests) — legacy `asyncio.get_event_loop().run_until_complete` replaced with module-scoped private-loop `_run_async(coro)` helper so Motor's fixture-scoped client stays valid across all subtests

---


## Latest Update — 2026-02 (Decision-type-based provider policy — broker-first execution truth, vendor witness + fallback)

### 🎯 The right routing model

Operator: *"Broker data should be primary for execution-time truth. Vendor data should support research, history, and fallback."*

Earlier iterations reduced this to a single flat priority list, which is wrong: a broker is authoritative for "what can I trade at what live price with what buying power right now?" — a vendor is authoritative for "what's the broader history / news / context?" Mixing those two chains was the underlying error behind both "Alpha never trades" (broker rate-limits ate the whole pool) and the earlier over-corrections that flipped it the other way.

### What shipped

**1. `services/provider_policy.py` (new)** — decision-type table:

```python
PROVIDER_POLICY = {
  "execution_quote": ["broker", "finnhub", "polygon", "alphavantage"],
  "account_state":   ["broker"],
  "positions":       ["broker"],
  "open_orders":     ["broker"],
  "intraday_regime": ["broker", "finnhub", "polygon"],
  "daily_history":   ["polygon", "alphavantage", "finnhub"],
  "news":            ["finnhub", "alphavantage", "polygon"],
}
```

**2. Freshness gate** — `EXECUTION_QUOTE_FRESHNESS_SECS` (default 5s). Broker quotes older than that are not trusted on the execution path.

**3. Disagreement gate** — `EXECUTION_QUOTE_MAX_DRIFT_BPS` (default 50 bps). When broker and vendor disagree beyond the ceiling, the result is flagged `data_conflict=True` and auto-execution is BLOCKED. Upstream must re-quote the broker.

**4. `fetch_broker_quote` / `fetch_vendor_quote`** split in `services/market_data_pool.py` — bypass the MongoDB price cache so the freshness / drift gates always see a real wire-time reading. Every quote is stamped with `fetched_at` (unix seconds) at dispatch.

**5. Alpha wired in** — `alpha_day_trader._run_alpha_tick_impl` calls `fetch_execution_quote(symbol)` immediately before `maybe_route_live`. Rejects observed as `execution_quote_blocked` observations with the full drift/age payload. Confirmed submissions annotate the intent with `broker_confirmed_price`, `execution_quote_source`, and `execution_quote_age_seconds`.

### Tests

- `tests/test_provider_policy.py` (16 tests): policy shape, `get_provider_chain` fallback, drift math, all four gate paths (fresh happy path, stale broker, drift > 50 bps → data_conflict, broker missing, everyone silent, missing `fetched_at`).
- `tests/test_market_data_broker_vendor_split.py` (5 tests): broker-only dispatch, vendor-only dispatch skipping broker, `fetched_at` stamping.

Full suite: 132 alpha/regime/policy tests green. 4785 total passing (2 pre-existing failures unrelated: `test_no_unguarded_mongo_datetime_math` and `test_broker_sort_and_watchlist_merge` — both reproduced without my changes).

### Files

- `services/provider_policy.py` (new)
- `services/market_data_pool.py` (`fetch_broker_quote`, `fetch_vendor_quote`, `fetched_at` stamp)
- `services/alpha_day_trader.py` (execution-quote gate before `maybe_route_live`)
- `tests/test_provider_policy.py`, `tests/test_market_data_broker_vendor_split.py` (new)

⚠️ **Deployment**: fix is preview only. `algo-trader-ai-1.emergent.host` needs redeploy to inherit — production still routes without the freshness/drift gates until then.

---


## Latest Update — 2026-02 (Regime detector reads today's tape, not yesterday's close)

### 🎯 Root cause of stubborn "session_chop" verdict

Operator: "SPY +0.52%, Tech +0.61%, Fear/Greed 71 GREED. Definitely not flat." But `fast_intraday_regime` returned `session_chop` anyway. Live query proved it:
- Regime saw yesterday: `SPY -0.55%  QQQ -0.84%  IWM -0.57%` (mildly down)
- User saw today live: **SPY +2.68%  IWM +3.35% intraday**

Daily bars **finalize after market close**. During RTH, `bars[-1]` is yesterday's completed bar — the classifier was scoring an entire trading day *behind* the actual tape.

### Two-part fix

**1. Live-quote overlay** — `_classify_bars(bars, *, live_quote=None)` now accepts a live quote. If provided, `prev_close` becomes yesterday's close and `close` becomes the live intraday price. Return computes off today's actual move.

**2. Overlay-mode vol_ratio normalization** — when overlay is active, we don't yet have today's real intraday high/low. The classifier's `vol_ratio < 1.4` trend-day gate would otherwise fail because "price move as range" inflates the ratio. Anchored to 1.0 in overlay mode so trend-day gate can fire on real intraday moves.

**3. Also loosened classifier defaults** — `trend_ret 0.4%→0.3%`, `body_ratio 0.5→0.25`, env-tunable (`ALPHA_REGIME_TREND_RETURN`, `ALPHA_REGIME_TREND_BODY`).

### Live-verified after fix

```
composite label: trend_up
family:          up
benchmarks:      {'SPY': 'trend_up', 'QQQ': 'trend_down', 'IWM': 'trend_up'}
  SPY: return=+2.68%  vol_ratio=1.00  body=+1.00  live=True
  QQQ: return=-1.36%  vol_ratio=1.00  body=-1.00  live=True
  IWM: return=+3.35%  vol_ratio=1.00  body=+1.00  live=True
```

Before fix: `session_chop` (yesterday's -0.5% down day). After: **`trend_up`** with per-benchmark truth surfaced.

**Files**: `services/fast_intraday_regime.py`, `tests/test_regime_live_overlay.py` (new — 6 tests covering fallback, quote-flip, malformed input safety).

**Full test count**: 757 alpha/regime/executor/wave/classical tests green.

⚠️ **Deployment**: fix is preview only. `algo-trader-ai-1.emergent.host` needs redeploy to inherit — until then production regime will keep reading yesterday's bar during RTH.

---


## Latest Update — 2026-02 (Pattern sensitivity + throughput bottleneck)

### 🎯 Honest diagnosis then targeted fix

Operator report: "It's stuck on SPY, market isn't flat, my Webull made +$54." Two claims investigated:

**Claim 1: signal_dispatcher stuck on SPY** — **NOT true.** Last 6h of `predictions` collection: NVDA (4), META (4), SPY (2), AMD (2), WMT/MSFT/UNH/PLTR/COIN/AVGO/AAPL (1 each) — 11 distinct symbols. The "stuck on SPY" perception came from a single log line that happened to be SPY at that moment. Dispatcher is fine.

**Claim 2: Alpha isn't converting signals to trades** — **TRUE**, and the real problem was upstream of pattern detection: two throttle bottlenecks culled the candidate pool from 50 → 2 BEFORE the pattern engine ever ran.

### The two bottlenecks

1. **`_max_active_setups()` defaulted to 2** — the opportunity ranker returned only top 2, so 48 of every 50 snapshotted candidates were silently discarded before pattern detection. Bumped default to **10** (bounded 1..25, env `RISEDUAL_ALPHA_DAYTRADER_MAX_SETUPS`).

2. **`_min_opportunity_score()` at 0.60** — killed the tail of the ranker's output too aggressively. Lowered default to **0.50** and made it scale by `1/sqrt(sensitivity)` so operators tune both knobs in lockstep. Bounded 0.30..0.75.

### And a real pattern-shape loosening

Added `ALPHA_PATTERN_SENSITIVITY` scalar (default **1.25**, bounded 0.5..2.5). Applied consistently across all 8 patterns:
- **rvol floors** divided by sensitivity (higher sens = LESS rvol required)
- **distance windows** multiplied by sensitivity (higher sens = WIDER windows)
- **pct-change floors** divided (higher sens = smaller moves count)

Concrete effect at default 1.25:
- VWAP_RECLAIM rvol: 1.5 → 1.20
- HOD_BREAK rvol: 2.0 → 1.60, accel: 1.25 → 1.00
- BREAKOUT rvol: 2.5 → 2.00
- PULLBACK rvol: 1.2 → 0.96
- MOMENTUM_REACCEL move %: 3.0% → 2.4%, accel: 1.5 → 1.20

Sensitivity=1.0 exactly reproduces the pre-fix strict defaults for operators who want to dial back.

### Verified live

Before this batch: `candidates_seen: 50, ranked: 2, setups: 0`
After: `candidates_seen: 50, **ranked: 10**, setups: 0`

Pattern engine now sees 5× more candidates per tick. When a real setup shape forms on any of the 10 (vs. previously 2), it will fire.

**Files:**
- `services/alpha_day_trader.py` — `_max_active_setups()` default 2→10, `_min_opportunity_score()` default 0.60→0.50 with sensitivity scaling, `AlphaPatternEngine._sensitivity()` + `_rvol_floor/_distance_window/_pct_floor` helpers applied to all 8 patterns
- `tests/test_pattern_sensitivity.py` — new (10 tests covering the scalar behavior, strict-mode reproduction, and relaxed-mode arming of borderline setups)

**Full test count**: 733 alpha/regime/executor tests green.

---


## Latest Update — 2026-02 (50-symbol universe + composite SPY/QQQ/IWM regime)

### 🎯 Two long-standing structural blockers fixed

Operator diagnosis: "Alpha only analyses 20 symbols per tick and only reads SPY for regime." Both correct. Investigation confirmed:

1. `services/alpha_day_trader.py:1123` — `for sym in universe_symbols[:20]:` capped tick fetches at 20 (was called "cap fetches per tick")
2. `services/market_regime.py:48` + `services/fast_intraday_regime.py:41` — both hardcoded `_MARKET_PROXY = "SPY"`
3. **NOT found**: `effective_universe = requested | held`, `symbols[:1..2]`, `MAX_SYMBOLS`, or any code collapsing the universe to held+requested. All 11 operator-named seeded workhorses (AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, JPM, V, JNJ, WMT) verified present in `top_universe` (312 total).

### Fixes shipped

**A. 20 → 50 symbol cap per tick**
- New `_max_symbols_per_tick()` helper reads `ALPHA_MAX_SYMBOLS_PER_TICK` env (default 50, bounded 1..100 to prevent broker-rate-limit exhaustion)
- Tick loop uses this cap for both `_candidate_universe()` fetch and the snapshot loop
- **Verified live**: manual tick now shows `candidates_seen: 50` (was 20)

**B. Composite SPY/QQQ/IWM regime**
- `services/fast_intraday_regime.py` refactored to loop across `ALPHA_REGIME_BENCHMARKS` (default `SPY,QQQ,IWM`)
- Per-benchmark labels classified individually, then aggregated with a **majority-vote family rule**: `chop` (session_chop, volatility_expansion), `up` (trend_up, momentum_ignition_up), `down` (trend_down, momentum_ignition_down, risk_off). Family with ≥⌈n/2⌉ wins; modal label within that family is the composite output. No majority → mode label + `family=mixed`
- Full breakdown persisted on the state doc: `benchmarks: {SPY: ..., QQQ: ..., IWM: ...}` + `composite: {family, family_count, total}` for observability
- One benchmark returning `UNKNOWN` (broker hiccup) is filtered out of the vote — other two still produce a valid composite

**C. Seeded 50-symbol floor (fail-safe against "collapse to nothing")**
- New `SEEDED_50_SYMBOLS` tuple in `alpha_day_trader.py` (exact operator-requested mega-caps + sector reps + benchmarks + SMH)
- Applied as the LAST source in `_candidate_universe()` — so if `top_universe` is empty (weekly rebuild crashed, fresh DB), watchlist empty, and no live signal_dispatcher predictions, Alpha still gets 50 real symbols to scan
- **Verified live**: `test_all_sources_failing_returns_seeded_floor_not_empty` — Mongo down on all 3 sources still returns 50 symbols

**Files**:
- `services/alpha_day_trader.py` — `_max_symbols_per_tick()`, `SEEDED_50_SYMBOLS`, tick loop bumped to 50
- `services/fast_intraday_regime.py` — composite refactor with `_benchmarks()`, `_classify_bars()`, `_aggregate()`
- `tests/test_universe_and_composite_regime.py` — new (18 tests: seeded floor invariants, cap env bounds, composite aggregation rules, unknown-filtering)
- `tests/test_alpha_candidate_universe.py` — updated existing tests to reflect the new seeded-floor behavior

**Full test count**: 723 alpha/regime/executor/universe/wave tests green (was 638; +85 in this session).

### Deferred (larger scope)
- **Real-time streaming**: user proposed "snapshot 50 every 60s, stream top-10 candidates + held positions". Alpha currently ticks every 5min via scheduler — moving to sub-minute streaming is a separate architecture change (broker subscription management, promote/demote FSM). Worth doing but not in this batch.

---


## Latest Update — 2026-02 (SEC-001 fix: CORS allowlist)

### 🔒 Security audit HIGH severity finding closed

**What the audit flagged**: `server.py:143-158` reflected the caller's `Origin` header verbatim into `Access-Control-Allow-Origin` with `Allow-Credentials: true`. Combined with `SameSite=None` login cookies (`routes/auth.py:52-57`), any external website a logged-in operator visited could silently read private trading data or trigger state-changing endpoints as them (place/close orders, toggle Alpha runtime, force ticks).

**Fix shipped**:
- `DynamicCORSMiddleware` now reads **`CORS_ALLOWED_ORIGINS`** (comma-separated allowlist) with `FRONTEND_URL` fallback for backwards-compat
- Non-allowlisted origins get NO `Access-Control-Allow-*` headers → browser rejects response, attack fails
- `Vary: Origin` on every response (allowed or not) so shared caches can't cross-contaminate
- `CORS_ALLOW_LOCALHOST=1` opt-in for local dev
- `.env` seeded with the real prod + preview origins: `algo-trader-ai-1.emergent.host,risedual-trading.preview.emergentagent.com,risedual.ai`

**Verified against live backend**:

| Origin | Access-Control-Allow-Origin | Verdict |
|---|---|---|
| Preview (allowlisted) | ✅ Present | Working |
| Prod `algo-trader-ai-1.emergent.host` (allowlisted) | ✅ Present | Working post-redeploy |
| `evil.com` (attacker) | ❌ Absent | **Blocked** |
| Preflight from `evil.com` | ❌ Absent | **Blocked** |

**Regression**: 102 auth/cors/middleware tests + 12 new CORS allowlist tests all green.

### Backlog (P3 hardening — not exploitable, deferred)
- **Broker credential encryption key** (`routes/broker.py:30`) derives from `JWT_SECRET`. Single-secret compromise blows both auth *and* broker creds. Consider splitting into a dedicated `BROKER_CREDS_ENCRYPTION_KEY`.

### Deployment note
**The fix lives in preview only.** For the prod URL (`algo-trader-ai-1.emergent.host`) to be protected, redeploy. `CORS_ALLOWED_ORIGINS` is already set in `.env` so the deployed backend will pick it up automatically.

**Files:** `backend/server.py` (CORS middleware refactored), `backend/.env` (allowlist populated), `backend/tests/test_cors_allowlist.py` (new, 12 tests).

---


## Latest Update — 2026-02 (Wave Panel UI + Paper trading backlog removed)

### Wave Panel Card shipped

Small admin dashboard card next to `BotStatusCard` in `AlphaDayTraderPanel`:

- **Mode distribution** — grid of 4 tiles (DANGER_PAUSE red / TREND_FOLLOW green / RANGE_GRID sky / WAIT slate) with count + % over the last 4h
- **Danger leaderboard** — top 5 symbols by max danger score with color-coded progress bars (≥0.72 red, ≥0.5 orange, else slate)
- **Auto-refresh** every 45s
- Backed by `GET /api/admin/alpha-daytrader/wave-observations?since_hours=4&limit=50`

Verified with seed data: `SHOCK` symbol at 0.80 danger correctly appears as top row with red bar; TREND_FOLLOW / RANGE_GRID symbols populate the distribution. Component lints clean.

**Files:** `frontend/src/components/admin/WavePanelCard.jsx` (new), `frontend/src/components/admin/AlphaDayTraderPanel.jsx` (import + placement between BotStatusCard and OperatorWatchlistCard).

### Backlog removed: Signal Producer Hunt (`paper_trading` feature)

User explicitly stated they don't want to reintroduce paper trading. Dropping "hunt for the missing signal_dispatcher / paper_trading producer" from the roadmap. Alpha's universe now runs entirely on `top_universe` (312 A-tier) + `operator_watchlist` + any live signal_dispatcher entries — no need to revive paper as an upstream source.

---


## Latest Update — 2026-02 (Wave Intelligence per-symbol regime + DANGER veto)

### 🎯 Per-symbol volatility guardrail — Alpha can now see what SPY-level regime can't

**Problem this closes:** Alpha's existing regime detectors are SPY-wide (slow: `choppy_meanrevert`; fast: `session_chop`). If AAPL had a 3σ volatility expansion but SPY was quiet, Alpha had no way to see it and would happily arm a setup into the shock. Wave Intelligence adds a per-symbol observe-only state machine that catches this.

**What shipped (3 slices):**

**P0 — DANGER_PAUSE veto** (safety):
- Every tick with ≥5 daily bars runs `WaveIntelligenceMachine.evaluate()` on the symbol's own bars
- Modes: `WAIT` / `TREND_FOLLOW` / `RANGE_GRID` / `DANGER_PAUSE`
- On `DANGER_PAUSE` (volatility expansion, price shock, wide spread) → **hard skip** with `wave_danger_vetoes` counter bumped
- Live verified: quiet bars → `RANGE_GRID`; +10% shock bar → `DANGER_PAUSE danger=0.80`

**P1 — Observation persistence** (research):
- `alpha_wave_observations` Mongo collection with 30-day TTL matching pattern research
- All observations persisted (not just DANGER) so later we can join against `alpha_outcomes` and answer "did TREND_FOLLOW trades pay better than RANGE_GRID?"
- Same fire-and-forget pattern as pattern-research log

**P1 — Admin endpoint** (visibility):
- `GET /api/admin/alpha-daytrader/wave-observations?symbol=&mode=&since_hours=&limit=`
- Returns rows + `mode_counts` rollup + `danger_leaderboard` (top symbols by max danger)

**Deferred (deliberately, per honesty pitch):**
- **Wave-mode pattern biasing** — overlaps with existing chop-regime boost for mean-reversion patterns; wait 2-4 weeks of real observations before deciding if a per-symbol bias overlay adds real signal
- **Grid trading integration** — the `grid_step_price` output is provided by the machine but Alpha doesn't do grid trading

**Files:**
- `services/wave_intelligence.py` — new (540 lines, unmodified from the user's upload)
- `services/alpha_wave_persistence.py` — new (audit log + rollups)
- `services/alpha_day_trader.py` — module-level `WaveIntelligenceMachine` singleton + veto wiring
- `routes/admin_alpha_daytrader.py` — new `/wave-observations` endpoint
- `route_registry.py` — TTL indexes on startup
- `tests/test_alpha_wave.py` — new (12 tests: persistence, filters, rollup, leaderboard, machine sanity)

**Full test count:** 638 alpha/regime/executor/bot-status/classical/fingerprint/research/wave tests green (was 626).

---


## Latest Update — 2026-02 (Economic Fingerprint Dedup + Pattern Research Log)

### 🎯 Two IGNISpilot-inspired safeguards shipped

**1. Economic Fingerprint Dedup** — prevents duplicate intents piling up on consecutive scanner ticks. Ported from IGNIS's `economic_fingerprint()`. Hashes `(symbol, setup_type, direction, timeframe, ATR-scaled entry_zone bucket)` → SHA-256. Bucket width = `max(atr * 0.25, |entry| * 0.001)` so tiny price wiggles collide (same $185.20 → $185.21 setup dedupes) but real moves don't (185 → 189 fires as a new setup). Dedup window: 15 min (env `ALPHA_INTENT_DEDUP_WINDOW_MIN`). Fingerprints stored in `alpha_intent_fingerprints` collection with 24h TTL. When a duplicate is caught, Alpha bumps `intents_deduplicated` counter and logs an observation for the operator.

**2. Pattern Research Log** — every tick that runs classical assessments writes ALL SIX pattern verdicts (bullish + bearish, including `blocked`/`forming`/`invalidated`) to `alpha_pattern_research`. The negatives are the whole point — needed to answer "how often does a *forming* inverse H&S actually confirm?" 30-day TTL. Fire-and-forget writer (Mongo hiccup never blocks a tick).

**New admin endpoints:**
- `GET /api/admin/alpha-daytrader/pattern-research?symbol=&pattern=&state=&limit=` — filtered log rows + counts-by-(pattern,state) rollup
- `GET /api/admin/alpha-daytrader/fingerprint-dedup?symbol=&limit=` — recent fingerprints + current dedup window

**Live verification:** Both round-trips work on production Mongo — collision behaviour ($185.19/$185.21 collide, $185/$189 don't), all 6 assessments persist and read back correctly.

**Files:**
- `services/alpha_fingerprint.py` — new (pure-math SHA-256 hash, zero deps)
- `services/alpha_fingerprint_index.py` — new (TTL/compound index management)
- `services/alpha_pattern_research.py` — new (append-only audit log helpers)
- `services/alpha_day_trader.py` — dedup check + fingerprint write + research write in tick flow
- `routes/admin_alpha_daytrader.py` — 2 new read endpoints
- `route_registry.py` — wires TTL indexes on startup
- `tests/test_alpha_fingerprint.py` — new (17 tests: collision, direction/symbol/timeframe flips, bad-input safety, penny-stock buckets)
- `tests/test_alpha_pattern_research.py` — new (9 tests: writes, filters, rollup, failure fail-open)

**Full test count:** 626 alpha/regime/executor/bot-status/classical/fingerprint/research tests green (was 589).

---


## Latest Update — 2026-02 (IGNISpilot classical chart patterns ported to Alpha)

### 🎯 Six multi-bar patterns from IGNISpilot handoff → Alpha's engine

**Context:** User shared a sanitized handoff of the IGNISpilot (Camaro/Next.js/Convex) trading assistant. Most of it is TS/Convex plumbing that doesn't port to RISEDUAL's Python stack — but the pattern-detection modules (`camaroBullishPatterns.js`, `camaroBearishPatterns.js`) are pure math with no framework dependency. Ported cleanly.

**Six new patterns added to `AlphaPatternEngine`:**

Bullish (tradeable, arm as `ActiveSetup`):
- `DOUBLE_BOTTOM` (5 bars, conf 0.78)
- `INVERSE_HEAD_SHOULDERS` (7 bars, conf 0.81)
- `FALLING_WEDGE` (6 bars, conf 0.74)

Bearish (Public.com is cash-only — used as *invalidation gates* on longs):
- `DOUBLE_TOP` / `HEAD_AND_SHOULDERS` / `RISING_WEDGE`
- When a confirmed bearish pattern is active on a symbol, Alpha **vetoes** any bullish setup this tick (no long entries against a broken structure)

**Data plumbing:** `MarketSnapshot` gained a `recent_bars: list[dict]` field (last 10 OHLC daily bars). Populated in `_snapshot_symbol()` from the same `market_daily()` call Alpha already makes — zero extra API load. Empty `recent_bars` (older snapshots) falls through cleanly to single-bar detectors.

**Engine wiring priority:**
1. **Classical bearish veto** → return None if a confirmed bearish pattern is live
2. **Classical bullish confirmed** → return that ActiveSetup (0.74–0.81 score beats single-bar setups)
3. **Mean-reversion / momentum single-bar patterns** → fall through as before

**Pattern lifecycle** (from IGNISpilot spec): `blocked` → `forming` → `confirmed` → `invalidated`. Only `confirmed` patterns short-circuit the detector; `forming` patterns fall through so momentum setups still fire.

**Tests:** 37 new tests total (16 pure-module + 21 integration + 5 pattern-lifecycle scenarios) locking the exact IGNIS reference confidences (0.78/0.81/0.74) so a future edit can't silently drift them.

**Files:**
- `services/alpha_classical_patterns.py` — new (461 lines, ported from JS)
- `services/alpha_day_trader.py` — new SetupType values + `_detect_classical()` in `AlphaPatternEngine` + `recent_bars` on MarketSnapshot
- `tests/test_alpha_classical_patterns.py` — new (module unit tests)
- `tests/test_alpha_classical_integration.py` — new (engine wiring tests)

**Full test count:** 589 alpha/regime/executor/bot-status/classical tests green (was 568).

---


## Latest Update — 2026-02 (Chop-regime playbook: Alpha now trades in every regime)

### 🎯 Fixed "Alpha never trades" — mean-reversion pattern family shipped

**Symptom (recurring user complaint):** Alpha would sit idle for days when SPY was in `session_chop` / `choppy_meanrevert`. The Bot Status card confessed *"Standing down by design"* but the user's manual Webull P&L on the same days proved chop *is* tradable — Alpha just didn't know how.

**Root cause:** `AlphaPatternEngine` only shipped 5 momentum-family setups (`VWAP_RECLAIM`, `HOD_BREAK`, `BREAKOUT`, `PULLBACK`, `MOMENTUM_REACCELERATION`). Every one required rising volume + trending price, so during chop the detector produced **zero** setups. Combined with a 0.65 executor confidence floor tuned for momentum, chop days were structurally silent.

**Fix (2-part):**
1. **Mean-reversion pattern family** in `services/alpha_day_trader.py`:
   - `VWAP_FADE_LONG` — price stretched 0.4–3% below VWAP, red bar exhaustion (long-only, Public.com is cash-only)
   - `RANGE_LOW_BOUNCE` — price at bottom quarter of intraday range, low rvol (sellers exhausted)
   - `OPENING_DRIVE_FADE` — gap-down reclaiming open on rvol ≥ 1.0
   All three arm outside chop too (raw shape gate), they just get a +0.10 score boost when either slow or fast regime reads as chop. Momentum patterns get a symmetric −0.05 penalty in chop but **never suppressed** (they still fire the same shape checks).
2. **Regime-aware executor confidence floor** in `services/public_equity_live_executor.py`:
   - Intents tagged with a chop regime → floor drops to 0.55 (new env `PUBLIC_LIVE_CONFIDENCE_FLOOR_CHOP`, default 0.55)
   - Intents in trending regimes → floor stays at 0.65 (existing `PUBLIC_LIVE_CONFIDENCE_FLOOR`)
   - Missing regime → default floor (fail-safe)

**Bot Status update:** `routes/admin_bot_status.py` no longer emits `severity=holding` in chop — headline now reads *"Bot trading — chop regime, mean-reversion playbook armed"*. The old "standing down by design" narrative is gone.

**Test coverage:** New file `tests/test_alpha_mean_reversion_patterns.py` (26 tests, all green): chop-regime detection, per-pattern activation, boost/penalty invariants, effective-floor selection, env override bounds.

**Regression status:** 547 alpha/regime/executor/bot-status tests pass. Existing pattern behaviour is preserved outside chop.

**Files touched:**
- `services/alpha_day_trader.py` — added `MEAN_REVERT_PATTERNS`, `MOMENTUM_PATTERNS`, `CHOP_REGIME_TOKENS`, `_is_chop_regime`, 3 new `SetupType` values, regime-aware `AlphaPatternEngine.detect(..., slow_regime=, fast_regime=)`, tick-level regime prefetch, regime stamping on the intent dict.
- `services/public_equity_live_executor.py` — added `_live_confidence_floor_chop()` and `_effective_confidence_floor(intent)`; gate uses the regime-aware floor.
- `routes/admin_bot_status.py` — chop no longer flagged as `holding`.
- `tests/test_alpha_mean_reversion_patterns.py` — new (26 tests).
- `tests/test_admin_bot_status.py` — updated chop verdict test.

---


## Latest Update — 2026-02 (System Atlas wired, safely)

### 🧭 RISEDUAL System Atlas integrated as observation-only

The parked Atlas package is now wired into the trading pipeline through a **fire-and-forget bridge** (`services/atlas_bridge.py`) that is structurally incapable of hanging or slowing a trade — addressing the exact reason it was parked before (SQLite `BEGIN IMMEDIATE` + 5s `busy_timeout` freezing the trade path under contention).

**Safety architecture (see [POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md](../docs/POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md) rule 8):**

- **No synchronous ledger call on the trade-critical path.** Every write is scheduled via `asyncio.create_task(...)` in a thread executor with a **100 ms hard timeout** — timeout cancels the task, trade proceeds untouched.
- **Observation-only, always.** Duplicate suppression is never enforced — no gate lives here. The overlay lesson applied verbatim.
- **Kill switch:** `RISEDUAL_ATLAS_ENABLED` env var (default ON). Flip to `0` to disable everything without a code deploy.

**5 seams wired:**
1. **Startup init** (`server.py`) — creates ledger at `/app/backend/var/risedual_atlas.sqlite3`.
2. **Intent-ingest claim** (`public_equity_live_executor.maybe_route_live`) — fires an async claim just before `place_order`.
3. **Lifecycle transitions** — background APPROVED → SUBMITTED → TERMINAL/REJECTED as the broker call resolves.
4. **Cycle traces** (`day_trade_scanner.run_scan`) — single background batch write per scan (no inline `mark()` calls on the hot path).
5. **Admin diagnostics** — `GET /api/admin/atlas/{status,intents,traces/{trace_id}}`.

**Latency-budget contract enforced by test suite** (`tests/test_atlas_bridge_latency.py`, 7/7 pass): even with a deliberately-stuck ledger (every write blocks 10 seconds), every bridge helper returns from the caller's perspective in **under 5 ms**. Stuck writes are cancelled by the 100 ms budget.

**Also cleaned up in this drop:**
- Deleted inert `services/promotion_gate.py` + `services/promotion_gate_service.py` (never wired in, landmine removed).
- Updated overlay post-mortem doc with rule 8: "No synchronous SQLite/network call on the trade-critical path."

---

## Latest Update — 2026-02 (Overlay post-mortem doc)

### 📄 Account-Aware Overlay post-mortem published

The Account-Aware Decision Layer (Alpha Overlay) that self-promoted from SHADOW → HARD_GATE on a 7-day timer and halted production trading was fully removed earlier this session. To prevent any future agent (or human) from rebuilding the same silent auto-enforcing pattern, a formal post-mortem is now checked into the repo.

**Doc:** [`/app/docs/POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md`](../docs/POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md)

**Highlights (any new "decision gate" MUST follow):**
- No self-promotion — SHADOW → ENFORCE is a manual operator toggle, never a timer.
- Ship behind a global kill-switch env var, default OFF.
- `EXISTING_POSITION` is never a BLOCK (it's a scale-in signal in this codebase).
- Every block is operator-visible, logged, and has a one-click override endpoint.
- Rollback must be possible via env var flip / admin endpoint, not a code deploy.

The doc contains a **Pre-Flight Checklist** that must be pasted into any PR that introduces a decision gate over Alpha. Reviewers: reject the PR if the checklist is missing or unchecked.

---

## Latest Update — 2026-08-06 (Intent producer observability)

### 🔍 Fixed "why doesn't anything fire?" — from silent to fully instrumented

**Symptom**: All scanner candidates end up `executor_skipped` with empty `executor_skipped_reason`. Predictions/scanner alive; executor's `maybe_route_live` returns bare `None` on every rejection.

**Fix**: New helper `_log_skip(db, symbol, reason, intent, detail)` writes structured docs to a new `intent_skip_log` collection at every `return None` gate in `maybe_route_live`. Non-blocking (Mongo failures swallowed at debug).

**8 skip reason tags**:
1. `live_exec_disabled` — `RISEDUAL_PUBLIC_LIVE_EXEC` not `1`
2. `empty_symbol`
3. `non_directional` — signal wasn't BUY/SELL variant
4. `confidence_floor` — confidence < `PUBLIC_LIVE_CONFIDENCE_FLOOR`
5. `not_in_allowlist` — `PUBLIC_LIVE_SYMBOLS` filter
6. `symbol_cooldown` — recent fire, within `PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN`
7. `chasing_filter` — intraday move ≥ `PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT`
8. `no_broker_creds` — no active Public.com connection
9. `insufficient_buying_power` — bp < notional

**New admin endpoints** (owner/admin only):
- `GET /api/admin/intent-audit/summary?hours=24` — reason-aggregated view with skip/fire ratio
- `GET /api/admin/intent-audit/events?hours=24&reason=X&symbol=Y&limit=200` — raw event log

**Validation** (testing_agent iteration_189): 16/16 tests pass. Zero critical/minor issues.

**Operator playbook**: after deploy, hit `GET /api/admin/intent-audit/summary` on prod. The top reason is your culprit — likely `live_exec_disabled` (fix: set `RISEDUAL_PUBLIC_LIVE_EXEC=1` in Prod Secrets) or `no_broker_creds` (fix: reconnect Public.com via Broker Connect UI). Whatever it is, no more guessing.



## Latest Update — 2026-08-06 (Trading Bots UI wired to live executor)

### 🔌 mode=live bots now inherit Ring 1 + Ring 3 protection

**Problem**: The Trading Bots UI (RED, BTC Grid, Tier3 Accumulator × 5, etc.) had a `mode=live` path that called `client.place_order` directly on the broker — bypassing every gate the autonomous loop just gained (confidence floor, per-symbol cooldown, chasing filter, evidence multiplier, structured `equity_live_trades` audit).

**Fix**: `_execute_bot_trade` in `services/trading_bot_service.py` now dispatches `mode=live` through `public_equity_live_executor.maybe_route_live` with a full intent dict:
- `strategy_id = "bot:<name-or-id>"` — bot fires get their own evidence bucket, distinct from the autonomous loop's `signal_dispatcher:v1`
- `source_signal = "trading_bot:<type>:<bot_id>"` for audit
- `intent_kind = open_long` (BUY) / `close_long` (SELL) — SELL correctly bypasses cooldown + chasing gates
- `confidence = bot.min_ai_confidence` (fallback 0.65)
- Returns `{'error': 'gated by executor safety checks'}` when any Ring 1 gate blocks; returns `{'status': 'filled', ...}` with `trade_id` + `broker_order_id` on success.

**Preserved**: `mode=paper` path unchanged; circuit-breaker pre-flight still runs for both modes; dead-code cleanup completed.

**Test surface**: `/app/backend/tests/test_bot_live_routing.py` (9 tests, all pass). Old `test_trading_bot_broker_contract.py` deleted (contract has moved).

**Validation** (testing_agent iteration_188): 9/9 new tests pass. Zero critical issues. All prior iteration regression files pass individually.

**Deploy**: Preview → Prod. No env-var changes. Users' bots stay `mode=paper` until they explicitly flip them; nothing fires until then.



## Latest Update — 2026-08-06 (Resend removed)

### 🗑️ Resend email provider fully removed (subscription not renewing)

**What was removed:**
- `services/email_service.py` — dropped `import resend`, `_send_via_resend`, all direct `resend.Emails.send` call sites (send_toxic_spikes_email, send_referral_success now route through `_routed_send`). SendGrid is the sole provider.
- `services/pool_config.py` — `get_email_provider_pool` no longer builds a resend-primary entry.
- `services/key_vault.py` — `RESEND_API_KEY` no longer mapped to the email lane.
- `routes/vault.py` — `_validate_resend` function and validator registration removed.
- `routes/provider_health.py` + `services/digest_service.py` — docstring/comment updates (Resend → SendGrid).
- `backend/.env` — `RESEND_API_KEY=` value cleared.
- `backend/requirements.txt` — `resend==2.27.0` removed. Package uninstalled from the pod.

**SENDER_EMAIL default** changed from `onboarding@resend.dev` to `noreply@risedual.ai`.

**Validation** (testing_agent iteration_187): **12/12 removal tests pass**, backend boots without the `resend` package installed, all email helper functions dispatch via SendGrid, empty pool returns `False` cleanly. 84/86 regression tests pass (2 pre-existing flaky tests unrelated).

**Deploy note**: Push to prod. Also remove `RESEND_API_KEY` from prod Secrets panel — it's no longer read but tidy hygiene.

**Regression guard**: `/app/backend/tests/test_resend_removal.py` (created by testing agent) will catch any future re-introduction of Resend.



## Latest Update — 2026-07-30 (Options 520 + Chasing filter)

### 🐛→✅ Bug A: Options "Buy to Open" modal showed Cloudflare 520
**Root cause**: `services/brokers/registry.py::_PROVIDERS` had `alpaca/tradier/tastytrade/ibkr` — but `_user_provider(user)` defaults to `"public"`. `get_options_adapter("public")` raised `ValueError`, the route's generic `except Exception` returned 502, Cloudflare rendered its own 520 error HTML into the modal.

**Fix**: registered `"public": lambda: StubOptionsAdapter("public")` in the factory. `/api/options/status` now returns HTTP 200 with `{enabled: false, provider: "public", details: "public options support coming soon"}`. The modal shows a clean "not enabled" state.

### 🐛→✅ Bug B: Buying tops (P&L calendar -20/-56/-35/-19/-25 sequence)
**Root cause**: no intraday-move sanity check — momentum signals arriving *after* a big run still fired.

**Fix**: new chasing filter in `public_equity_live_executor.maybe_route_live`:
- `_max_intraday_move_pct()` reads `PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT` (default **4.0%**)
- `_intraday_move_pct(symbol)` reads market_data_pool (Public → Finnhub → TwelveData → Polygon failover) to compute `(current - prev_close) / prev_close * 100`
- If `abs(move_pct) >= threshold` → OPEN_LONG rejected + log line
- Data outage (all providers down) → **fail-open** (allow trade + log) so a provider hiccup can't block legit signals
- Scoped to **OPEN_LONG only** — SELL/close paths never gated
- Env `PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT=0` disables the gate entirely

**Validation** (testing_agent iteration_186): 17/17 tests pass. Zero critical or minor issues. Live smoke — `/api/options/status` HTTP 200.

**Deploy**: push to prod. Recommended tuning after 1-2 days: if the filter is still too permissive (i.e., still catching tops of smaller moves), tighten to `PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT=2.5` in Prod Secrets — no code deploy needed.



## Latest Update — 2026-07-30 (Duplicate React key spam fix)

### 🐛→✅ Zero more "Encountered two children with the same key" warnings

**Root cause**: two components used bare `${side}-${price}` / `${ticker}-${price}` keys without an array index, so React's reconciler flagged every collision as sibling lists updated on the SSE stream.

**Fixes:**
- `WhaleRadar.jsx` L187 — key = `${ev.id}-${w.side}-${w.price}-${idx}` (was `${w.side}-${w.price}`)
- `DarkPoolData.jsx` L101 — key = `${w.ticker}-${w.price}-${idx}` (was `${w.ticker}-${w.price}`)
- `OrderFlowHeatmap.jsx` L281 (defensive) — key = `${ev.type}-${ev.price}-${ev.time || ''}-${idx}` (added idx per test-agent recommendation)

**Validation** (testing_agent iteration_185): Zero duplicate-key warnings across 60+ seconds of live SSE-driven WhaleRadar re-rendering with 10 pairs and up to 11 walls per event. 100% frontend pass.



## Latest Update — 2026-07-30 (Ring 1 + Ring 3 — Fix "trash picks")

### 🐛→✅ Root cause of every-trade-scored-0.92 bug

**Forensic finding**: `calibration_layer` had shipped a degenerate 4-knot isotonic model fit on only 147 samples in May. Its curve mapped ANY raw confidence ≥ 0.47 to a single sink value of **0.9167**. The calibrator was stamped with `applies_to: ['tier3_readiness_only']` but `day_trade_scanner` read `calibrated_confidence` unconditionally — so every fire hit the 0.92 sink, `PUBLIC_LIVE_CONFIDENCE_FLOOR=0.55` became meaningless, and JPM / UNH / SPY re-fired 3× each. First evidence run confirmed the 11 legacy live trades had a **Sharpe of -0.42** — mathematically losing.

### Ring 1 fixes (data quality + gates)

1. **`day_trade_scanner.scan_universe`** — respects `calibration_applies_to` scope: uses `calibrated_confidence` only when scope is empty or includes `day_trade_scanner`/`all`/`*`. With the current `tier3_readiness_only` scope, the scanner now uses raw confidence — so a 0.55 raw signal falls below the 0.65 live floor and is refused, breaking the pathological re-fire pattern.
2. **`strategy_id` propagation** — every `ScanCandidate` and every `_intent` dict now carries `strategy_id` (falls back through `model_version` → `signal_dispatcher:v1`). Downstream `equity_live_trades` insert stores it.
3. **Per-symbol cooldown** — `PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN=60` (default). Blocks `OPEN_LONG` on any symbol within N minutes of its last live BUY; `SELL/close` paths bypass so exits are never gated.
4. **Enriched OPEN_LONG insert** — `equity_live_trades` rows now carry `strategy_id`, `raw_confidence`, `calibrated_confidence`, `regime`, `predicted_move_pct`, `notional` (mirrors `live_notional_usd`), `notional_baseline`, `evidence_multiplier`, `evidence_enforced`, `evidence_bucket`, `evidence_hit_rate`, `evidence_sharpe`, `evidence_trade_count` — full provenance without needing a `predictions` re-join.

### Ring 3 (Evidence Worker + governor multiplier)

5. **New `services/evidence_worker.py`** — nightly cron at 03:15 UTC (or manual trigger). Groups closed `equity_live_trades` by `strategy_id`, computes `hit_rate`, `mean_return`, `stdev_return`, Sharpe (mean/stdev), expectancy. Upserts to `strategy_evidence_scores`, logs run to `strategy_evidence_runs`.
6. **Bucket → notional multiplier decision table**:
   | Bucket | Condition | Multiplier |
   |---|---|---|
   | `proven` | ≥5 trades AND Sharpe ≥ 1.0 | 1.00× |
   | `ok` | ≥5 trades AND Sharpe ≥ 0.0 | 0.50× |
   | `losing` | ≥5 trades AND Sharpe < 0.0 | **0.10×** |
   | `untested` | < 5 trades | 0.25× |
7. **Governor pre-flight in `maybe_route_live`** — reads `strategy_evidence_scores`, multiplies notional. **SHADOW mode by default** — `RISEDUAL_EVIDENCE_ENFORCE=1` in Prod Secrets to activate real notional reduction.
8. **Admin UI** — new Admin → Evidence tab (`data-testid='evidence-panel'`). Per-strategy table with color-coded buckets, SHADOW/ENFORCING banner, Refresh + Recompute buttons.
9. **Endpoints**: `GET /api/admin/evidence/scores`, `POST /api/admin/evidence/recompute` (owner/admin only).

### New env knobs
- `PUBLIC_LIVE_CONFIDENCE_FLOOR=0.65` (existing; unchanged)
- `PUBLIC_LIVE_SYMBOL_COOLDOWN_MIN=60`
- `PUBLIC_LIVE_UNTESTED_NOTIONAL_MULT=0.25`
- `RISEDUAL_EVIDENCE_WINDOW_DAYS=30`
- `RISEDUAL_EVIDENCE_MIN_TRADES=5`
- `RISEDUAL_EVIDENCE_ENFORCE=` (unset = SHADOW; `1` = enforce)

### Validation
testing_agent iteration_184: **16/16 new backend + 53/53 regression tests passed**. Frontend Evidence panel renders, buttons functional. Zero critical or minor issues.

**Recommended Prod rollout**:
1. Deploy the code
2. Leave `RISEDUAL_EVIDENCE_ENFORCE` unset for 3-5 days (SHADOW) — watch scores populate as new fires accrue with `strategy_id` tagged
3. Once `signal_dispatcher:v1` has ≥5 closed trades and a real Sharpe reading, flip enforcement on



## Latest Update — 2026-07-30 (Code-review P1 bug bundle)

### 🐛→✅ 8 confirmed bugs from external code review, all fixed

**Front-end (1)**
- `OrderFlowHeatmap.jsx` — invalid Tailwind class `border-slate-400/30/60` → `border-slate-400/30`.

**LLM prompt correctness (1)**
- `crew_definitions.py` prediction crew — `ADVERSARIAL CHECK` list renumbered from `1,2,3,4,4` → `1,2,3,4,5`.

**Concurrency / stability (2)**
- `orderflow_ws_service.OrderFlowStream` — whale-alert `asyncio.ensure_future(...)` task references now held in `self._bg_tasks: set[asyncio.Task]` with a done-callback so the GC can't collect them mid-flight.
- `_run_stream` — fixed 3-second reconnect sleep replaced with exponential backoff (base=3s → cap=60s); resets to base after a successful message so brief blips don't inflate the delay.

**Deprecation (1)**
- `crew_engine.run_parallel_crew` — deprecated `asyncio.get_event_loop()` → `asyncio.get_running_loop()`.

**Auth consolidation (2)**
- `routes/auth.py` no longer defines `get_current_user`, `get_optional_user`, or `get_jwt_secret` — it now imports them from `services/auth_helpers.py` (single source of truth). Re-export preserves every existing `from routes.auth import get_current_user` call site across ~20 admin/route modules and 6+ tests.
- `server.py` startup now validates `MONGO_URL`, `DB_NAME`, `JWT_SECRET` and raises a clear `RuntimeError` before app instantiation instead of a cryptic `KeyError` deep in the first authenticated request.

**Observability (1)**
- `crew_definitions.run_war_room_crew` + `run_hypothesis_crew` — the two `except Exception: pass` blocks around memory-context and order-flow-context fetches now emit `logger.warning(...)` with `[war_room_crew:<symbol>]` / `[hypothesis_crew:<symbol>]` tags so ChromaDB / memory-service outages are visible.

**Skipped (product decision, not a bug)**
- `AdversarialHub.jsx` — the "adversarial pipeline" panel is a stylized visualization, not a live agent verdict. Design intent, not a bug. Flagged for future work if it should reflect the real crew output.

**Validation** (testing_agent iteration_183): 12/12 fix tests + 41/41 regression tests passed. Live smoke — admin login + `/api/auth/me` + `/api/admin/retention/status` all 200, proving the auth re-export path works across modules.

**Deploy note**: All changes on Preview. Push Deploy for prod.



## Latest Update — 2026-07-21 (Retention & Backlog Purge)

### ✨ New feature: Operator Control → Retention panel

**Mirrors the mission.risedual.ai retention flow.** Keeps Atlas breathing by evicting stale telemetry on a configurable window while executed trades and user data are preserved forever.

**Retention windows:**
- Telemetry (default): **72h** — `predictions`, `day_trade_targets`, `mc2_heartbeats`, `mc2_contributions`, `mc2_stances`, `mc2_intents`, `signal_dispatcher_events`, `alert_events`
- Paper trades (closed only): **24h** — active/open/pending paper trades preserved
- **Never purged**: `equity_live_trades`, `trade_orders`, `broker_connections`, `users`, `watchlists`, `portfolio_snapshots`, `portfolio_history`

**Endpoints (owner/admin only):**
- `GET /api/admin/retention/status` — per-collection totals + expired backlog, lifetime purged, last-purge timestamp, "more remains" flag
- `POST /api/admin/retention/purge` — idempotent batch drain (default 5000/call); click again if `more_remains: true`

**Env knobs:**
- `RISEDUAL_RETENTION_HOURS=72`
- `RISEDUAL_RETENTION_PAPER_HOURS=24`
- `RISEDUAL_RETENTION_PURGE_BATCH=5000`

**Hourly sweeper**: `retention_hourly_purge` scheduler job auto-drains one batch/hour with `triggered_by='scheduler'` audit trail.

**UI**: New "Retention" tab in Admin panel (Operations group). PURGE BACKLOG NOW button (data-testid `purge-backlog-btn`), per-collection breakdown table, 4 summary tiles, "more remains" banner.

**Validation** (testing_agent iteration_182, 18/18 backend + 100% frontend passed): audit log verified, live trades survive purge, active paper trades preserved, admin auth enforced, click-again pattern works, all regressions (iter_180, iter_181) still green.

**Deploy note**: Fix is on Preview. Push Deploy to enable on Production. Once deployed, open Admin → Retention and click PURGE BACKLOG NOW a few times to drain the backlog accumulated under the old no-retention setup — Atlas will breathe much easier.



## Multi-Trader Scaling Model (decided 2026-07-07)

**Model chosen: BYO API keys (Option A)** — each trader connects their own Public.com / Kraken / broker keys via the BrokerConnect UI. RISEDUAL only orchestrates signals, decision logic, and UX. Traders own their capital and broker relationship end-to-end.

Rationale:
- **Zero custody risk** — RISEDUAL never touches customer funds. Removes need for broker-dealer registration (SEC/FINRA), state money-transmitter licences, AML/KYC-at-scale, or FDIC/SIPC insurance.
- **No PDT bottleneck on RISEDUAL** — pattern-day-trading caps apply per-user's own broker account, not the platform.
- **Clean scaling primitive** — the existing `broker_connections` collection is already keyed by `(user_id, broker_id)`; every service (executor, watchlist merge, portfolio sync) already looks up the requesting user's keys. Nothing structural needs to change.
- **Regulatory model is well-understood** — RISEDUAL is a "signal service / trade automation tool", not an advisor or broker. Terms of service can require users to acknowledge they're operating their own account and RISEDUAL is not providing investment advice.

Future path (hybrid Prime tier):
- If a Prime/VIP tier is ever offered where users want RISEDUAL to trade on their behalf, that requires an RIA (Registered Investment Advisor) shell entity + signed advisory agreement per user + custody via a qualified custodian (never on RISEDUAL infra). Deferred until product-market fit at retail is proven.



## Original Problem Statement
Build a functional clone of a trading app named **RISEDUAL AI**. Multi-model AI consensus, Realtime P&L Tracker, Thread-Safe Native Multi-Agent Engine, Live Order Flow Heatmaps, Paper Trading capabilities, Global Safety Kill-Switch System, Multi-broker Live Options Trading flow, advanced Research Shadow Layer for ML adaptation, "Dual-Stack Architecture", and a "Market State Awareness" Terminal UI.


## Latest Update — 2026-07-07 (Sort + Watchlist bug fixes)

### 🐛→✅ Broker Positions/Orders sort + Watchlist auto-merge

**Bug 1 — Trades not alphabetical**: `/api/broker/positions/{broker_id}` and `/api/broker/orders/{broker_id}` returned rows in broker's native order. Fixed with `formatted.sort(key=symbol)` in both endpoints; orders break ties on `submitted_at`.

**Bug 2 — Watchlist ignored broker accounts** (root cause): `_sync_watchlist()` in `broker.py` wrote to `db.watchlists.symbols` while every reader (and the manual add/remove routes) uses `db.watchlists.tickers`. Field-name mismatch = watchlist appeared empty even when portfolio-sync had run. Fixed:
1. `_sync_watchlist` rewritten to `$addToSet` into `tickers` (canonical field).
2. New `_merge_broker_holdings` helper in `workspace.py` — `GET /api/workspace/watchlist` now unions live broker positions on every read + persists new symbols back. Fails silently on broker error (returns manual list).

**Perf indexes added** (`routes/auth.py::create_indexes`):
- `trade_orders.symbol`, `trade_orders (user_id, symbol)`
- `paper_trades.symbol`, `paper_trades.ticker`
- `watchlists.user_id`

**Validation** (testing_agent iteration_181, 14/14 passed): sort logic verified against stubbed broker clients; watchlist merge tested with legacy `symbols`+`tickers` shape; index presence confirmed at startup.



## Latest Update — 2026-07-07 (P0 Search War Room 520 fix)

### 🔴→✅ Search War Room no longer times out on production

**Problem**: `/api/web-intel/war-room` was taking ~17-19s (AI_ANALYSIS_TIMEOUT=18s + parallel provider phase with individual timeouts up to 15s), triggering Cloudflare/ingress 520 errors.

**Fix landed**:
1. `/app/backend/services/search_war_room/orchestrator.py` — `AI_ANALYSIS_TIMEOUT` reduced 18 → 5s; new `PROVIDER_PHASE_TIMEOUT = 5.5s` wraps `asyncio.gather` with graceful partial-result collection on timeout (pending tasks are cancelled and marked `status="timeout"` in the response instead of hanging the whole request).
2. `/app/backend/services/search_war_room/registry.py` — individual provider timeouts capped 4-6s (was 10-15s): sec 8→6, stockfit 12→6, tavily 10→6, av_news 12→6, finnhub_news 10→6, ddg/ddg_news 7→5, newsapi 15→6.
3. `/app/backend/services/search_war_room/adapters/ai_analysis.py` — `max_tokens` reduced 600 → 400 for faster AI turns.

**Validation** (testing_agent iteration_180, 9/9 backend tests passed):
- TSLA outlook: 10.70s | AAPL fundamentals: 11.04s | MSFT earnings: 12.76s | GOOGL news: 11.65s | Fed rate decision: 10.39s | Phase-cap hang guard: 11.32s
- All responses HTTP 200 with valid payload (brief/engine_results/warnings)
- AI analysis engine still returns `status="ok"` on typical queries
- Total worst-case wall-clock now ~11-13s (was ~19s+) — safely under Cloudflare ingress limit

**Deployment note**: Fix is on Preview only. User needs to hit Deploy for it to reach Production.



## Latest Update — 2026-06-26 (Alpha armed for Friday open — full BUY/SELL via Public.com)

### 🎯 Five blockers cleared in one session — Alpha now trades both sides

1. **Scanner feature-tag mismatch fixed** — `day_trade_scanner.py` was hunting for `feature: "paper_trading"` predictions (zero matches since paper was disabled). Alpha emits `feature: "signal_dispatcher"` (3k+ fresh). Filter widened to `$in [signal_dispatcher, paper_trading]`.
2. **Scanner → executor wiring (Phase 4b)** — scanner now calls `public_equity_live_executor.maybe_route_live(...)` on the chosen winner. Previously wrote `day_trade_targets` rows that nothing consumed.
3. **Public.com primary market-data provider** — added `get_quote()` + `get_daily_bars()` to `PublicTradingService`. Pool reordered: Public p1, AV p2, Finnhub p3, Polygon p5. Discovered correct historic-data endpoint from operator's Portfolio.mht: `GET /userapigateway/historicdata/{type}/{symbol}/{period}/{aggregation}` with `User-Agent: public-dev-docs` header, response under `regularMarket.bars`. Verified for AAPL/JPM/BAC/UNH/MSFT.
4. **Sovereign sidecar closed-loop** — supervisor-level `alpha-sidecar` was still POSTing to severed `mission.risedual.ai`. New `LocalMCClient` writes to local Mongo (`mc2_heartbeats`, `mc2_contributions`, `mc2_stances`, `mc2_intents`). Sidecar identical surface, single-line import switch when `is_standalone_mode()`. Watchdog respawn loop killed. Log err growth went from ~2 MB/day to flat.
5. **Executor now handles BOTH sides** — removed dead "alpaca_position_closer" doctrine. New behavior:
   - BUY/STRONG_BUY + not held → open $1 long
   - BUY + already long → idempotency skip
   - SELL/STRONG_SELL + long held → **close the long** (via Public.com sell)
   - SELL + not held → clean skip (cash account; no margin shorts)

**Final state heading into Friday open**:
- Public.com: $194.09 buying power, $277.54 equity, account `5LG34065`, JWT auth verified
- All four executor branches tested with synthetic intents (markets-closed 400s confirmed wiring reaches `place_order`)
- Pool: Public primary for quotes + bars, AV/Finnhub/Polygon as backup
- Sidecar: closed-loop, 60s tick cadence to local Mongo
- All MC outbound paths severed (`mission.risedual.ai` returns zero hits in any service)
- Scheduler heartbeat: 11k+ beats, alive
- Confidence floor 0.55 / $1 notional / no symbol allowlist
- First expected fill: BAC STRONG_BUY @ 9:35 ET if it survives 15-min TTL


## Previous Update — 2026-06-23 (Alpha is fully closed-loop / Public.com primary)

### 🎯 Sovereign sidecar severed from remote MC — now writes to local Mongo

**Problem found**: The supervisor-level `alpha-sidecar` process (separate from the backend's in-process MC2 layer) was still POSTing every heartbeat / contribution / stance to `https://mission.risedual.ai`. The remote MC was severed weeks ago and no longer recognises "alpha" — every call had been 400/404-ing, the err log had grown to 5.4 MB, and the watchdog was respawning the sidecar every ~2 minutes. `RISEDUAL_STANDALONE_MODE=1` had only severed the in-process paths; this supervisor process was untouched.

**Fix landed**:
1. **`/app/backend/sovereign/local_mc_client.py`** — new sync `LocalMCClient` that mirrors the exact public surface of `MCClient` (`heartbeat`, `post_contribution`, `post_stance`, `post_intent`, `close`) but writes to local Mongo collections instead of HTTP. Validates payloads with the same `build_*` helpers so any malformed contribution still fails fast.
2. **`/app/backend/sovereign/sidecar.py`** — boots `LocalMCClient` when `is_standalone_mode()` is true (env flag or `MC_BASE_URL=local://*` or empty). Identical surface, single import switch. Both main client and independent heartbeat client swap together.
3. **`/app/backend/.env`** — `MC_BASE_URL`, `MONOREPO_BASE_URL`, `RISEDUAL_MC_URL` all set to `local://standalone`.
4. **`/etc/supervisor/conf.d/alpha-sidecar.conf`** — env block updated: `MC_BASE_URL=local://standalone` and `RISEDUAL_STANDALONE_MODE=1` added.

**Collections written by the local sidecar (every tick)**:
- `mc2_heartbeats` — one doc per 30s
- `mc2_contributions` — one doc per 60s (mode, weights, lr, recent_outcomes, notes)
- `mc2_stances` — only when there's an open position to vote on
- `mc2_intents` — already shared with the async backend; sidecar can write here too

**Verification (29 post-restart log lines)**:
| Metric | Value |
|---|---|
| Local writes (`mc2_*`) | 16 |
| Remote calls to `mission.risedual.ai` | **0** |
| Any httpx HTTP calls | **0** |
| Ticks completed | 5 (every 60s, cadence preserved) |
| Heartbeat thread errors | 0 (was: 1/tick × 90+/h) |
| Err log growth rate | flat (was: ~2 MB/day) |

**Other outbound paths confirmed already gated by `is_standalone()`** (no code change needed): `services/mc_inbox_poller.py`, `services/risedual_monorepo_client.py`, `services/mc_checkin/__init__.py`, `services/crypto_mc_intent_emitter.py`.


## Previous Update — 2026-06-21 (Public.com is primary; Alpaca demoted to placeholder)

### 🎯 Alpha trades on Public.com — Alpaca preserved for BYO-key customers

**Operator directive**: "Just wire Public. I'm not using Alpaca. It can be a
placeholder if a future customer has their keys. I just want Alpha trading
on Public."

**What changed**:
1. **Removed the Alpaca hard-refusal (410) at `POST /api/broker/order/{broker_id}`** — `/app/backend/routes/broker.py`. Alpaca orders now flow through the standard mode/connection guards just like any other broker, so a future customer with their own Alpaca keys can still place trades without site-level blocking.
2. **`POST /api/broker/order/alpaca`** previously returned `410 alpaca_retired`; now returns the same `403 wrong_mode` / `404 not_connected` paths as every other broker. Verified by curl.
3. **Options trading default broker flipped** in `/app/backend/routes/options_trading.py::_user_provider` — fallback is now `public` (was `alpaca`).
4. **`QuickTrade.jsx`** — default `broker` state changed from `'alpaca'` → `'public'`, broker `<Select>` now lists **Public.com** first and **Alpaca (BYO keys)** as a dimmed secondary option.
5. **`BrokerConnect.jsx`** — `recommended: true` moved from Alpaca → Public.com so the connection UI highlights Public as the primary broker.
6. **Bonus deliverable**: `/app/alpha_standalone.py` — a self-contained, copy-pasteable distillation of Alpha's Bull/Bear/Commander decision engine for use as a brain in the new multi-brain stack. Pure Python stdlib, no Mongo/FastAPI deps. MIT.

**Verification**:
- `POST /api/broker/order/alpaca` → `403 wrong_mode` (not 410) ✅
- `POST /api/broker/order/public` → `403 wrong_mode` (identical path) ✅
- Backend boots clean (635 routes registered, no errors)
- Lint clean on all modified files
- Standalone Alpha engine runs end-to-end (LONG / SHORT_OR_AVOID / NO_TRADE all verified)


## Previous Update — 2026-06-20 (Monday-ready: preview armed end-to-end)

### 🎯 Alpha is wired to trade Monday morning on preview

**What landed**:
1. **Preview env armed** — `RISEDUAL_STANDALONE_MODE=1` +
   `RISEDUAL_PUBLIC_LIVE_EXEC=1` + tight risk caps
   (`PUBLIC_LIVE_NOTIONAL_USD=1`, allowlist `AAPL,MSFT,SPY,QQQ`).
   Alpaca trading forced OFF, paper trading sealed, crypto live
   explicitly OFF for Monday's canary.

2. **Public.com vault upsert + live JWT exchange verified** —
   credentials for account `5LG34065` (cash brokerage, $244 buying
   power, $42.41 SPCX position) stored encrypted. JWT auth-exchange
   path works end-to-end.

3. **`PublicTradingService.get_account` + `get_positions` fixed** —
   they were hitting `/trading/account` (which actually returns a
   LIST of accounts, not balances). Correct endpoint is
   `/trading/{accountId}/portfolio/v2`. Now parses the nested
   `buyingPower` dict + `equity` list correctly.

4. **Two safety layers wired (operator directive "C")**:
   - **Pre-trade cash check** — `maybe_route_live` calls
     `get_account()` first, refuses if buying_power < notional.
     Prevents noisy Public.com rejection logs.
   - **Confidence floor for live execution** — env knob
     `PUBLIC_LIVE_CONFIDENCE_FLOOR` (default 0.65). Refuses signals
     below the floor. Substitute for peer-brain veto (severed in
     MC2 standalone mode). Clamped to [0, 0.95] so values colliding
     with the toxic-spike saturation cap can't silently disable
     live trading.

5. **6 new tripwires** in `tests/test_public_equity_live_executor.py`
   pin every gate.

**End-to-end verification** (preview):
| Layer | State |
|---|---|
| Standalone mode | ✅ active |
| Public live executor | ✅ armed |
| Vault | ✅ connected, JWT exchange working |
| Account read | ✅ $244.09 cash, 2 positions visible |
| Conf floor block (0.55 < 0.65) | ✅ verified |
| Allowlist block (TSLA blocked) | ✅ verified |

**Tests**: 4,322 → 4,328 passing (+6 safety layer pins, zero
regressions). Backend healthy on preview, 635 routes.

### Activation steps for prod (Monday-ready)

1. Redeploy preview → prod.
2. Mirror env flags on prod's `backend/.env`:
   ```
   RISEDUAL_STANDALONE_MODE=1
   RISEDUAL_PUBLIC_LIVE_EXEC=1
   PUBLIC_LIVE_NOTIONAL_USD=1
   PUBLIC_LIVE_SYMBOLS=AAPL,MSFT,SPY,QQQ
   PUBLIC_LIVE_CONFIDENCE_FLOOR=0.65
   BROKER_ALPACA_TRADING_ENABLED=false
   PAPER_TRADING_ENABLED=false
   RISEDUAL_CRYPTO_LIVE_EXEC=0
   ```
3. Upsert Public.com credentials into prod's
   `broker_connections` (via UI panel or matching one-liner script).
4. Restart prod backend.
5. Run toxic-purge + backfill on prod:
   ```
   POST /api/admin/toxic-purge/all?confidence_floor=80
   python3 -m scripts.backfill_grade_misses
   ```
6. Monday morning before open — confirm `/api/admin/mc2/state` shows
   standalone active + no outbound to mission.risedual.ai in logs.


## Latest Update — 2026-06-18 (Saturation root cause found + backfill complete)

### 🎯 RCA closed — found the upstream 100%-confidence source

After running today's backfill, **256 cap-hit log lines** fired —
proving the 100% values had been written historically by a real
upstream source, not a one-off bug.

**Smoking gun**: `services/sovereign_ai_core.SovereignCoordinator.aggregate`
applied catalyst delta + options alignment boosts with `min(1.0, ...)`
ceilings. A strategist signal at 0.98 + bullish catalyst delta +0.05
→ saturated at 1.0. Options alignment +0.03 → still 1.0. Stuck at
the ceiling forever after.

**Fix**: Replaced every `min(1.0, ...)` in the Sovereign confidence
pipeline with `min(0.95, ...)`. Symmetric change in
`services/sovereign_promotion_gate.maybe_apply_contribution` (was
also `min(1.0, ...)`). Together with this morning's
`normalize_confidence` cap at the persistence boundary, saturation
is now blocked at THREE layers: source → aggregator → storage.

**+2 tripwires** in `tests/test_toxic_spike_seal.py` source-pin the
new caps so any future agent can't silently restore the `min(1.0)`
pattern.

### ✅ Feb-2026 backfill re-run on preview

Per the previous toxic-spike incident's playbook
(`/app/memory/DEPLOYMENT_NOTES.md` 2026-02-20):
- Ran `scripts/backfill_grade_misses.py` on preview Mongo + Chroma.
- **11 historical 'miss' rows promoted to NEUTRAL** (no longer
  counted as failures by the calibration query).
- All 1479 Chroma episodes confidence-normalised under the new
  scale-uniform contract.
- 256 cap-hit log lines emitted — visibility into the upstream
  saturation history (no longer hidden, no longer poisoning Chroma).

### ✅ Alert dedup verified intact

`services/alert_dedup.py` (Feb 2026) is wired correctly into
`market_memory_service._send_toxic_alerts` — uses atomic
`record_alert` with unique-index gate on `alert_id`, plus
persistence escalation. The "same alert 3 nights in a row" failure
class is structurally prevented.

**Tests**: 4,322 passing (was 4,320, +2 saturation-source pins).
Backend healthy on preview, 635 routes.


## Latest Update — 2026-06-18 (Toxic spike seal + paper trading permanently sealed)

### 🔒 Toxic Spike Alert RCA + three-layer seal

**Operator's screenshot**: "Toxic Spikes Alert" — 31 high-conf failures,
7 at EXACTLY 100.0% confidence (MSFT, QQQ, SPY×2, AVGO, AMD, SMCI, WMT).
The 100% values were the smell: no honest model outputs 1.0; that's a
sigmoid saturation or hardcoded boost. Those failed predictions were
landing in Chroma as `toxic_lesson` rows, where kNN perception kept
re-surfacing them as precedent for new signals — re-seeding the same
"toxic spike" pattern next cycle.

**Three-layer seal**:

1. **`services/prediction_tracker.normalize_confidence`** — hard cap
   at 95.0. Anything ≥95% collapses to 95.0 and emits a
   `[normalize_confidence] cap-hit` log line so the next saturation
   event is visible. Negative values clamp to 0. No more 100%
   confidence predictions can enter the system.

2. **`services/market_memory_service.purge_toxic_lessons`** — new
   async helper that DELETES (not re-tags) any episode with
   `outcome="toxic_lesson"` OR `outcome="miss"` AND
   `confidence > floor`. Replaces the previous re-tag-only doctrine
   that was letting bad precedent survive in Chroma's embedding space.

3. **`routes/admin_toxic_purge.py`** — 3 new owner-only endpoints:
   - `POST /api/admin/toxic-purge/chroma` — wipes Chroma toxic rows
   - `POST /api/admin/toxic-purge/mongo` — wipes Mongo predictions
   - `POST /api/admin/toxic-purge/all` — both at once

### 🗂️ Paper trading PERMANENTLY sealed (no env-var escape)

Last session's `PAPER_TRADING_ENABLED` env-gate was insufficient — the
operator's tests / conftest fixtures could re-enable it. This session
hardcodes the seal at the entry point with no read-from-env:

- `services/crypto_paper_trader.run_crypto_symbol` — first line
  returns `{"reason": "paper_trading_retired"}`. Below that line is
  dead code, kept for git history only.
- `tests/conftest.py` — `collect_ignore` adds 4 legacy paper-pipeline
  test files (`test_crypto_paper_bot.py`,
  `test_crypto_paper_trader_adl_receipts.py`,
  `test_crypto_web_research_shadow_integration.py`,
  `test_crypto_adversarial_phase_wiring.py`). They exercise behaviour
  that no longer happens by design.

### 📦 Preview DB wiped (verified on this pod)

- **Mongo `predictions`**: 69 toxic rows deleted (matched
  `verified_24h.correct=False` + `confidence>80`).
- **Chroma episodes**: 150 toxic rows deleted (matched
  `toxic_lesson` or `miss`+conf>80; scanned 1625 total).

### Test coverage

- **18 new tests** in `tests/test_toxic_spike_seal.py` — covers
  every cap edge, log emission on cap-hit, no-log when below cap,
  Chroma purge targeting (correct rows only), no-collection
  graceful fail, empty-collection clean exit.
- **3 new tripwires** in `tests/test_paper_trading_disable.py` —
  source-level pin that the sealed return is the FIRST line + the
  parametrize across all truthy env values still returns the sealed
  reason.

**Tests**: 4,320 passing (was 4,352 → 36 collect-ignored legacy
paper-pipeline tests, +21 new tripwires; zero regressions in the
active suite). Backend healthy on preview, **635 routes** (+3 for
toxic-purge endpoints).

### Operator activation steps (prod)

1. Redeploy preview → prod.
2. After redeploy, hit:
   ```
   POST https://risedual.ai/api/admin/toxic-purge/all?confidence_floor=80
   ```
   with owner JWT. Wipes both Chroma + Mongo on prod.
3. Watch the next 24h of `[normalize_confidence] cap-hit` log lines.
   Any source still trying to claim 100% confidence becomes visible
   without poisoning the memory store.
4. Next nightly cleanup will re-purge any new toxic rows that slip
   through (cap-hit reduces but doesn't eliminate `outcome=miss` +
   confidence in (80, 95] band).


## Latest Update — 2026-06-16 (Paper trading retired)

### 🗑️ Paper trading removed

**Operator directive**: "Paper needs to be removed. Public doesn't
offer it, neither does Kraken."

**Backend kill**:
- New master env knob `PAPER_TRADING_ENABLED` (default **OFF**).
- Gated at two paper-trade entry points:
  - `services/crypto_paper_trader.run_crypto_symbol` — earliest
    decision after `db is None`; returns
    `{"reason": "paper_trading_disabled"}`.
  - `services/ml_orchestrator` Tier-2 path — skips the
    `maybe_paper_trade` call; the live Public.com path below is
    untouched.
- Existing closers untouched — they keep draining the historical
  open paper rows so nothing rots.
- Existing `paper_trades` / `crypto_paper_trades` collections
  preserved for historical analysis + ML backtests (hot-transition
  per the agreed scope).
- Pytest conftest autouses `PAPER_TRADING_ENABLED=true` so the ~30
  legacy crypto-paper pipeline tests keep their assertions. Default
  OFF in production env stands.

**Frontend kill**:
- `components/TradingModePill.jsx` rewrite — Paper/Live toggle modal
  removed (367 lines → 31 lines). The pill now renders a static
  "LIVE · Public + Kraken" indicator. Same `data-testid` preserved
  so e2e selectors keep working.
- `components/TradingModeBanner.jsx` rewrite — mismatch/switch CTA
  gone (90 lines → 51 lines). Always renders the live indicator.
  Stale `expectedMode="paper"` call sites see an explicit
  "live only" pill so the operator notices.
- Trading-mode hooks / `/api/trading-mode/*` routes still wired in
  the backend (untouched) — toggling them server-side is now a
  no-op from the UI's perspective.

**Test coverage**: 8 new tests in
`tests/test_paper_trading_disable.py` pinning:
- Default OFF behavior for crypto paper trader
- Garbage env value coerces to OFF
- Truthy values re-arm the gate (and downstream still runs)
- Source-level pin: paper gate runs BEFORE the crypto-symbol firewall
- Source-level pin: ml_orchestrator's Tier 2 branch consults the env

**Tests**: 4,344 → 4,352 passing (+8, zero regressions).
Backend healthy on preview, 632 routes. Frontend lint clean.

**Activation steps (prod)**:
1. Redeploy preview → prod.
2. In prod's `backend/.env`, ensure `PAPER_TRADING_ENABLED` is unset
   OR set to `false`. (Default is OFF, so unset suffices.)
3. Restart backend.
4. Verify: `tail` the logs — no `[crypto_paper] ... opened` lines
   should appear on the next consensus tick. The crypto live wire
   (`[crypto-live] FILL ...`) and the Public.com equity wire
   (`[public-live] FILL ...`) keep operating normally.

**Cold-start caveat**: ML retrain / Sovereign learning / dissent
scorer continue eating from the historical paper corpus (hot
transition). New training samples now come from `equity_live_trades`
+ `crypto_live_trades` only. Watch sample-size dashboards for the
first few weeks — re-armable via `PAPER_TRADING_ENABLED=true` if
needed.

**Stale marketing copy (FYI, not auto-fixed)**: `LandingPage.jsx`,
`LegalPages.jsx`, `HelpCenter.jsx`, and the OAuth demo pages still
reference "autonomous paper trading" / "$100K paper capital" /
"30-day paper gate". These are sales/copy decisions; updating
them is a content refresh, not a technical change. Flag if you
want them updated.


## Latest Update — 2026-06-16 (Drop Alpaca + Public.com autonomous routing)

### 🔁 Equity broker swap: Alpaca → Public.com

**Operator directive**: "Drop Alpaca. Not use it for trading anything.
[Wire Public.com autonomous routing] B of course."

#### Part 1 — Alpaca trading kill switch

- New env knob `BROKER_ALPACA_TRADING_ENABLED` (default **OFF**).
- Gate placed at `services/ml_alpaca_broker.maybe_execute_live` —
  short-circuits BEFORE the existing `_LIVE_OPT_IN` check. Even if
  someone re-arms `RISEDUAL_LIVE_EXECUTION=1`, the operator's
  drop-Alpaca directive holds.
- Alpaca **quote provider** untouched (`services/alpaca_equity_quotes.py`
  still feeds equity quotes to the pipeline). Only order placement
  is gated.
- Position closer (`services/alpaca_position_closer.py`) already had
  its own `ALPACA_POSITION_CLOSER_ENABLED=true` opt-in (default OFF),
  so it's already inert.

#### Part 2 — Public.com autonomous routing

- New module `services/public_equity_live_executor.py` mirrors the
  `crypto_live_executor` pattern for equity:
  - Master switch: `RISEDUAL_PUBLIC_LIVE_EXEC=1` (default **OFF**)
  - Fixed notional: `PUBLIC_LIVE_NOTIONAL_USD=25` (clamped to [1, 1000])
  - Optional allowlist: `PUBLIC_LIVE_SYMBOLS=AAPL,MSFT,NVDA` for
    operator-bounded first-connect
  - LONG-only (no short-margin scaffold)
  - Market orders at this phase; SL/TP brackets can layer once we
    know Public's order-type behaviour in prod
  - Connect-state gate: refuses to fire without an active
    `broker_connections` row for `broker_id="public"`
  - Idempotency: refuses 2nd live row for same open symbol
  - Quote-probe with graceful skip when unavailable
  - Doctrine pin: even if `place_order` raises, NO Mongo write occurs
    — we never persist a fill we don't have

- Wired into `services/ml_orchestrator.py` immediately after the
  legacy Alpaca call (which is now a no-op by default). When Alpha's
  consensus fires a BUY equity intent, the orchestrator calls
  `public_equity_live_executor.maybe_route_live(db, intent=...)`.
  All skip cases return `None` silently — Alpha's loop is never
  blocked by Public.com being unreachable.

- New Mongo collection: `equity_live_trades` (parallel to
  `crypto_live_trades`). Live equity fills never pollute the
  `paper_trades` ML training set.

#### Activation steps (prod)

1. Redeploy preview → prod.
2. Connect Public.com via the broker-connect panel
   (uses the auth-exchange fix from 2026-06-09 — secret → JWT).
3. Add to prod's `backend/.env`:
   - `BROKER_ALPACA_TRADING_ENABLED=false` (or just leave unset)
   - `RISEDUAL_PUBLIC_LIVE_EXEC=1`
   - Optional: `PUBLIC_LIVE_SYMBOLS=AAPL,...` for a starter allowlist
   - Optional: `PUBLIC_LIVE_NOTIONAL_USD=25` (default already 25)
4. Restart backend.
5. On next equity consensus tick (BUY direction), Alpha autonomously
   places a $25 market BUY on Public.com.

#### Test coverage

- 15 tests in `tests/test_alpaca_trading_disable.py` — pin the
  gate behavior + source-level ordering (gate BEFORE opt-in).
- 23 tests in `tests/test_public_equity_live_executor.py` — pin
  every doctrine: env contract, sizing bounds, allowlist, connect
  gate, idempotency, quote-fail, place_order returning None,
  place_order raising, happy-path with full provenance.
- Allowlist entry added to `test_no_local_direction_tuples.py`
  with codebase-canonical justification.

**Tests**: 4,306 → 4,344 passing (+38, zero regressions).
Backend healthy on preview, 632 routes.


## Latest Update — 2026-06-12 (MC2 Phase A — phantom-tick severance)

### 🎯 Operator caught a phantom tick still hitting Original MC

After Phase A's initial wire-in (intent_bridge + opinion_bridge +
outcome mirror + mc_checkin), FIVE OTHER outbound paths were still
pointed at `mission.risedual.ai`. The smoking gun was
`services/mc_sidecar._heartbeat_loop` — POSTing to
`{MC_BASE_URL}/api/heartbeat-ping/alpha` **every 30 seconds**.

**All five severance points patched this session**:

1. **`services/mc_sidecar._heartbeat_loop`** — THE phantom tick.
   When standalone, the loop now touches the local liveness file
   (watchdog stays green) and stamps `last_heartbeat_destination=
   "standalone_local"` on the state collection — but issues ZERO
   outbound HTTP. Loops continue ticking, just locally.

2. **`services/mc_inbox_poller.run_forever`** — three polling loops
   (opinions every 60s, roles every 300s, scorecard every 600s).
   Now returns immediately when standalone — entire async.gather
   never spawns.

3. **`services/mc_keys_proxy.fetch_market_data_keys`** — boot-time
   key sync from MC. Now short-circuits before the httpx client
   is constructed. Pod runs on local `.env` keys only.

4. **`services/risedual_monorepo_client._enabled`** — single source
   of truth gate for the opinion/scorecard/roles client. When
   standalone, returns `False` → every `_post` / `_get` short-
   circuits to `{"error": "sidecar_disabled"}`.

5. **`sovereign/inprocess_sidecar._is_enabled`** — in-process
   contribution loop. Forced OFF when standalone even if
   `ALPHA_INPROCESS_SIDECAR_ENABLED=1` is set.

6. **`services/crypto_mc_intent_emitter.emit_crypto_intent`** —
   skips MCClient construction entirely, builds the kwargs locally
   and writes straight to `mc2_intents`.

**Doctrine pin**: every severance point reads `is_standalone()`
from `services/mc2/standalone.py` — single source of truth. If
the operator flips the env var live, every loop notices on its
next iteration (no restart needed for heartbeat / inbox).

**Test coverage**: 10 new tripwire tests in
`tests/test_mc2_phantom_tick_severance.py`:
- mc_inbox_poller skipped when standalone
- mc_keys_proxy short-circuits before httpx
- mc_keys_proxy NOT short-circuited when not standalone (symmetric)
- monorepo_client `_enabled` False when standalone (with all env vars set)
- monorepo_client `_enabled` True when not standalone
- inprocess_sidecar disabled when standalone
- inprocess_sidecar enabled when not standalone
- crypto_mc_intent_emitter routes to MC2 (refuses MCClient construction)
- **mc_sidecar heartbeat refuses _async_client when standalone**
- mc_sidecar heartbeat uses wire when not standalone (symmetric)

**Tests**: 4,296 → 4,306 passing (+10, zero regressions).
Backend healthy on preview, 632 routes.

**To activate on prod**:
1. Redeploy preview → prod.
2. Set `RISEDUAL_STANDALONE_MODE=1` in prod's `backend/.env`.
3. Restart backend.
4. Watch the logs — within 30s of startup you should see:
   - `[mc_sidecar] RISEDUAL_STANDALONE_MODE=1 — heartbeat PING SKIPPED`
   - `[mc_inbox] STANDALONE_MODE=1 — Original MC inbox polling SKIPPED`
   - `[mc_keys_proxy] STANDALONE_MODE=1 — Original MC keys-proxy SKIPPED`
5. Run `tcpdump`-equivalent or check MC operator dashboard — NO
   inbound traffic from Alpha's pod IP.


## Latest Update — 2026-06-09 (MC2 Phase A — in-process Mission Control)

### 🏗 Severance from Original MC — RISEDUAL is now self-sufficient

**Architectural pivot** (operator directive from this session): Alpha
will no longer depend on `mission.risedual.ai`. The remote MC has
been the source of every major outage:
- 8h Alpha silence (Cloudflare 502 → hung sockets → scheduler death)
- MC identity 401 (`X-Runtime-Token` rejected, doctrine drift)
- MC Scorecard `total_resolved=0` (outcome ingest path not wired)

**Phase A scope (shipped this session — backend only, no frontend yet)**:

1. **New module `services/mc2/`** — in-process Mission Control surface
   with five files: `standalone.py` (env toggle), `state.py` (db handle
   + state snapshot), `intents.py` (`mc2_intents` writer), `opinions.py`
   (`mc2_opinions` writer), `outcomes.py` (`mc2_outcomes` writer),
   `scorecard.py` (local rollup).

2. **Master kill switch**: `RISEDUAL_STANDALONE_MODE=1` (default OFF).
   Flipping it on a deployed pod cuts the Original MC wire on the
   next request — no code change needed for safe rollback.

3. **Wire-in points** (3 modules touched):
   - `sovereign/intent_bridge.emit_intent_from_consensus` — writes to
     `mc2_intents` instead of POSTing to Original MC.
   - `sovereign/intent_bridge.emit_opinion_from_consensus` — writes to
     `mc2_opinions` instead of POSTing to Original MC.
   - `services/sovereign_outcome_bridge.enqueue_outcome` — MIRRORS to
     `mc2_outcomes` (additive — legacy inbox still written so flipping
     standalone off doesn't lose outcomes).
   - `services/mc_checkin.checkin_now` — short-circuits to synthetic
     `standalone_local` verdict, no outbound HTTP POST.

4. **New diagnostic surface**:
   - `GET /api/admin/mc2/state` (owner-only) — collection volumes + last
     10 rows per stream.
   - `GET /api/admin/mc2/scorecard?brain=alpha` (owner-only) — local
     win/loss/flat rollup. **Permanent fix for `total_resolved=0`.**

5. **Doctrine pins**:
   - Phase A has NO 12-gate chain. Every intent lands with
     `gate_state="accepted_no_gates"`. Phase B ports the gates.
   - `may_execute=False` is FORCED on every MC2 intent. RISEDUAL is
     doctrinally headless in V3; Phase A does not change that.
   - All MC2 writers no-op cleanly when `set_db` hasn't been called.
   - Outcomes mirror with full provenance (`sovereign_decision_id`,
     `prediction_id`, `source_signal`).

6. **Test coverage** — 26 new tripwire tests across:
   - `tests/test_mc2_phase_a.py` (24 tests) — env toggle, writers,
     scorecard, state snapshot, intent_bridge routing, checkin
     severance.
   - `tests/test_mc2_outcome_mirror.py` (2 tests) — outcome bridge
     mirror on/off.

**Tests**: 4,270 → 4,296 (+26, zero regressions). Backend healthy on
preview, 632 routes (+2 for MC2 endpoints).

**To activate on prod**:
1. Redeploy preview → prod.
2. Add `RISEDUAL_STANDALONE_MODE=1` to prod's `backend/.env`.
3. Restart backend (or wait for next deploy if env was set pre-deploy).
4. Hit `GET /api/admin/mc2/state` to confirm collections start filling.
5. After first paper close, `GET /api/admin/mc2/scorecard?brain=alpha`
   shows real `total_resolved > 0`.

**Phase B (next session, when ready)**:
- Port the 12-gate chain into `services/mc2/gates/`.
- Move `gate_state` from `accepted_no_gates` to per-gate outcomes.
- Time-windowed scorecard (1d / 7d / 30d) + per-lane breakdowns.
- Optional `/admin/mc2` frontend mirroring Original MC's dashboard.

**Phase C (beta ramp)**:
- Camaro / Chevelle / REDEYE personalities as in-process council
  members under `services/brains/{camaro,chevelle,redeye}/`.


## Latest Update — 2026-06-09 (Public.com broker auth fix)

### 🔧 Public.com connect: 400 → working

**Operator report (Jun 9)**: "Connection failed (400)" at risedual.ai's broker-connect panel with valid Public.com credentials (API Token + Account ID).

**Root cause**: `services/broker_service.PublicTradingService` used the operator's **secret key** directly as a Bearer token. Public.com's API rejects this with 401 — their flow REQUIRES an exchange step first:

```
POST https://api.public.com/userapiauthservice/personal/access-tokens
Body: {"validityInMinutes": N, "secret": <user-secret>}
→  Response: {"accessToken": "<short-lived JWT>"}
```

Only the returned JWT is accepted by `/userapigateway/trading/*`. Our route saw the upstream 401 and surfaced it as a 400 "Could not authenticate with broker" — hence the operator-visible failure.

**Fix shipped**:
- `PublicTradingService.AUTH_URL` + `_exchange_secret_for_access_token()` — performs the documented exchange and caches the JWT with a 60s refresh slack.
- `_auth_headers()` — lazy refresh on every request. First call exchanges; subsequent calls reuse until ~60s before declared expiry.
- All 5 trading methods (`get_account`, `get_positions`, `place_order`, `get_orders`, `cancel_order`) now call `_auth_headers()` instead of the deleted `self.headers`.
- 6 tripwire tests (`tests/test_public_broker_auth_exchange.py`) pin: AUTH_URL constant, no stale `self.headers`, exchange-on-first-call, 401-from-Public surfaces as None (not raise), cache reuse, expiry refresh.

**Tests**: 4,264 → 4,270 (+6, zero regressions). Backend healthy, 630 routes.

**Operator action**: Redeploy preview → prod. Public.com connect should succeed on the next attempt with the same credentials you tried before.


## Latest Update — 2026-06-03 (Alpha 8h-silence RCA — scheduler hang fix)

### 🚨 RCA: Alpha emitter went silent for 8 hours

**Operator screenshot (Jun 3, 09:12 PT)**: MC's Intents page shows Alpha as Strategist on both equity + crypto lanes, but **last opinion 8h ago, last sovereign 8h ago, total Alpha intents = 0**. Other brains (Camaro 43, REDEYE 57) emitting normally.

**Smoking gun in preview pod logs**:
- `06:11:02 UTC` — last apscheduler "skipped: maximum number of running instances reached (1)" warning for `_check_smart_orders`.
- `06:11:02 → 14:14:31 UTC` — **8 hours of total log silence** (no scheduler ticks, no intent emissions, no heartbeats from periodic loops).
- `14:14:31 UTC` — pod auto-restarted, scheduler came back, but only 1 minute of new emissions before the user's screenshot.

**Root cause**: `services/smart_order_service.check_smart_orders` (runs every 30s) was fetching quotes for every active smart-order symbol **serially with no per-call timeout**. Combined with `AsyncIOScheduler()` default `max_instances=1` and NO `misfire_grace_time`, stalled httpx sockets (Cloudflare 502/520 from MC saturation seen in logs at 05:57 and 06:07) accumulated until the event loop starved. apscheduler became unable to fire ANY job — including the in-process Sovereign sidecar and Alpha's consensus-tick path.

**Three-piece fix shipped**:

1. **`services/smart_order_service.check_smart_orders`** — quotes now fetched **concurrently via `asyncio.gather`**, each wrapped in `asyncio.wait_for(timeout=6.0)`. A single stalled provider can no longer extend the tick past its 30s window.

2. **`server.py` scheduler init** — `AsyncIOScheduler` now constructed with explicit `job_defaults`:
   - `coalesce=True` — collapse N queued misfires into 1 catch-up run
   - `misfire_grace_time=60` — drop ticks more than 60s late (don't defer them)
   - `max_instances=2` — bounded concurrency (one hung tick can't permanently block the next, but we don't fan out unbounded either)

3. **`tests/test_smart_order_scheduler_hang_fix.py`** (NEW, 3 tests) — static pin: `asyncio.gather` + `asyncio.wait_for` MUST appear in `check_smart_orders`. The regression would silently re-introduce the serial loop; the tripwire fails loud.

**Tests**: 4,261 → 4,264 (+3, zero regressions).

**Operator action**: Redeploy to prod. The scheduler-hang RCA applies symmetrically — prod was likely starving on the same Cloudflare-502 pattern that took the preview pod down.

### Pending — MC Scorecard verification (from prev session)
- Crypto outcome bridge wiring shipped 2026-06-02. After this redeploy, MC's Scorecard for Alpha should start accumulating crypto `total_resolved` counts on the next paper-close cycle.


## Latest Update — 2026-06-02 (Outcome push fix + Time-based exit + MC identity probe)

### Three batched changes — backend test suite 4,250 → 4,261 (+11, 0 regressions)

**1. MC Scorecard `total_resolved=0` fix — crypto closes now flow to MC**

`services/paper_trade_closer.py` was already enqueueing equity closes
into `sovereign_outcomes_inbox` since 2026-05-22, but `services/crypto_closer.py`
(paper) and `services/crypto_live_closer.py` (live) were not. MC's
recent_outcomes snapshot for Alpha was therefore equity-only, which
explains the 0/N resolved counter on the crypto side of the
Scorecard.

Files touched:
- `services/crypto_closer.py` — `enqueue_outcome(brain="alpha", ...)` call right after
  `write_crypto_trade_memory`. Provenance fields forwarded.
- `services/crypto_live_closer.py::_close_row` — symmetric
  `enqueue_outcome` call on every live close (SL/TP/manual/time-based).
- Lane discriminator `extras.lane = "crypto"` so MC can filter the
  per-lane outcome stream.

**2. Time-based stale-exit for live crypto (env-armed, default OFF)**

`services/crypto_live_closer.py` now scans every open live row's
`opened_at` before the SL/TP classifier runs. Rows older than
`CRYPTO_LIVE_MAX_HOLD_HOURS` (env knob; unset/0 = disabled) are
force-flatted: cancel both SL+TP legs, place market SELL for
`row.size`, stamp the row `closed_reason="time_based_exit"`. Exit
price uses Kraken's last-traded probe with fallback to `entry_price`
(honest "no mark, pnl=0" instead of a phantom extreme).

Doctrine pins:
- LONG-only (mirrors executor's no-shorts doctrine; defensive guard
  refuses synthetic covering BUYs).
- Zero-qty rows refused — never SELL the wrong size.
- Helper `_force_close_stale_row` placed-order failure → row left
  for the normal orphan classifier (no double-action).
- 5 new tripwire tests cover: default-OFF behaviour, fires-when-stale,
  skips-short-holds, falls-back-to-entry on quote fail, refuses zero qty.

Context: PDT rule change June 4, 2026 ($25k → $2.5k) reframes
overnight equity holds as discipline failures. Crypto is exempt
from PDT (24/7 market) but the operator wants the same time
discipline armed for live BTC/ETH positions.

**3. MC identity probe diagnostic — triage the 401 without one-off curls**

New owner-only endpoint
`GET /api/admin/runtime/mc-identity-probe` hits MC's
`/api/admin/runtime/{brain}/status` with `X-Brain-Id` + `X-Runtime-Token`
(per `MC_BRAIN_API_QUICKSTART_v1.md` section 10) and returns
status_code + first 400 chars of response + an operator-facing
hint string. Hint matrix pinned by 6 unit tests, covering:
- transport error (unreachable MC)
- 200 (accepted)
- 401 with three named possible causes (token rotated, JWT-only path,
  preview/prod missing brain token)
- 403 (JWT-only)
- 404 (route drift)
- default (other status codes)

The 401 next step is unblocked: operator can `curl` the probe,
share the JSON with the MC operator, and get a definitive
answer in one round-trip.

### Pending — MC Scorecard verification
- After this redeploy, watch MC's Scorecard for Alpha crypto outcome
  flow. The bridge is wired symmetrically with the equity path that
  already works.


## Latest Update — 2026-06 (Opinion side-channel wired)

### Wire: Alpha now publishes opinions to MC on every consensus tick

**Per operator override**: all brains (Alpha included) can occupy the executor seat. `post_opinion()` in `services/risedual_monorepo_client.py` had been a dormant wire (defined, never called). It is now wired into the consensus pathway so MC's cross-brain discussion layer surfaces Alpha's reasoning regardless of which brain holds the executor seat.

**Files touched**:
- `backend/sovereign/intent_bridge.py` — added `_build_opinion_payload()` + `emit_opinion_from_consensus()`. Modified `emit_intent_from_consensus()` to fire opinion alongside intent (BUY/SELL/SHORT/COVER) AND on non-directional verdicts (HOLD), since opinions are valid for all verdicts.
- `backend/tests/test_opinion_emission.py` (NEW) — 18 tripwire tests.

**Behavior**:
- Directional verdicts → intent + opinion (both wires hot, shared trace_id in evidence)
- HOLD/NEUTRAL → opinion only (intent path short-circuits per existing doctrine)
- `emit_opinion=False` kwarg lets legacy callers opt out
- Opinion sidecar failures are swallowed; intent return value is unaffected
- `post_opinion()` forces `may_execute=False` on the wire (opinions ≠ executions)

**Tests**: 4,094 passing (was 4,076).


## Latest Update — 2026-06 (Strategic dissent scorer wired)

### Wire: research shadow now scores BOTH tactical and strategic dissents

**Gap closed**: `compute_strategic_score()` existed at `services/research_shadow_scorer.py:112` but was never invoked by `run_scorer_pass`. Of 2,666 dissents in MongoDB, 99.7% had tactical scores but **0%** had strategic scores. The "shadow refused to enter" decisions (2,017 SHORT-vs-HOLD cases) had no strategic accountability.

**Files touched**:
- `backend/services/research_shadow.py` — added `STRATEGIC_LOOKAHEAD_S` dict (30min default for stock/crypto, 4h for options).
- `backend/services/research_shadow_scorer.py` — added `_fetch_strategic_pending()`, `_find_matching_closed_trade()`, `_score_one_strategic()`, and wired both passes into `run_scorer_pass()`. Counter now reports `scored_tactical` + `scored_strategic` separately.
- `backend/tests/test_strategic_dissent_scorer.py` (NEW) — 19 tripwire tests.

**Behavior**:
- Strategic eligibility: `is_dissent=True AND shadow_action="HOLD" AND active_action in {LONG,SHORT,BUY,SELL}`.
- Join: symbol + opened_at within ±15min of shadow.ts + direction axis match (handles both equity `up`/`down` and crypto `LONG`/`SHORT` vocabularies).
- Lookahead: 30min PAST active's close timestamp (not past shadow.ts).
- Skips (not failures): no matching closed trade, window not elapsed, quote provider down → re-tried on subsequent ticks.
- SHORT-active strategic scores pass `direction="SHORT"` to `compute_strategic_score`, not the LONG default. Bare default would mis-score SHORT-side dissents.
- Tier-3 firewall preserved: scorer READS from `paper_trades`/`crypto_paper_trades`, WRITES only to `research_shadow_decisions`.

**Live signal observed within 90 seconds of deploy**:
- `active=SHORT, shadow=HOLD` (n=348 scored): **81.3% shadow-right**, avg delta **+$17.82**. Adversarial brain correctly more patient.
- `active=LONG, shadow=HOLD` (n=45 scored): **0% shadow-right**, avg delta **−$82**. Adversarial brain wrongly patient — actives correctly close longs at the right time.
- 1,958 strategic dissents still pending — scoring at ~100/tick.

**Tests**: 4,142 passing (was 4,123).




## Latest Update — 2026-05-30 (Dupe-Pod fix + Process Identity payload)

### 🚨 Duplicate-checkin bug found and fixed

**Symptom**: MC's stored stamp for Alpha alternated between `verdict=prod` (matching what `/api/admin/runtime/stamp` self-reported on prod) and `verdict=preview` (matching the preview pod's `RISEDUAL_ENV=preview`). Same `brain_id=alpha`, same `ALPHA_MC_INGEST_TOKEN` — MC could not disambiguate.

**Smoking gun** (caught by MC operator via cross-stamp comparison):
- Self-diagnose on prod showed `pip_fingerprint.package_count=5` (full prod deps)
- MC's stored stamp at the same minute showed `pip_fingerprint.package_count=4` (lighter preview deps)
- Two different installed-package sets = two different Python processes

**Root cause**: The preview pod in `/app/backend/.env` had identical `ALPHA_MC_INGEST_TOKEN` and `RISEDUAL_MC_URL=https://mission.risedual.ai` as production. Both pods' periodic `mc_checkin` loops were POSTing to prod MC every 5 min as the same brain.

**Fixes shipped (Alpha side)**:

1. **Preview-pod skip guard** (`backend/server.py`):
   ```
   if stamp.env_name != "prod" and not RISEDUAL_MC_CHECKIN_ENABLE_ON_PREVIEW:
       skip checkin (boot + periodic)
   ```
   Verified live: preview pod boot log emits `[mc_checkin] SKIPPED — env_name='preview' is not 'prod'`.

2. **`process_identity` payload field** (`services/mc_checkin/__init__.py`):
   Every checkin POST now includes:
   ```json
   "process_identity": {
     "pid": <int>,
     "hostname": "<gethostname>",
     "process_boot_at": "<iso utc, captured once at module load>"
   }
   ```
   Stable for the lifetime of each Python interpreter. MC parses and indexes it.

3. **`last_posted_to_mc` field on the runtime stamp diagnostic**
   (`routes/admin_runtime_stamp.py`): surfaces the literal stamp the
   periodic loop last POSTed (env_name, db_name, mc_url, broker_mode,
   git_sha, policy_hash, posted_at). Closes the "is our pod posting
   what we think?" loop without log digging.

4. **`[stamp-debug]` log line** on every checkin — `env_name`, `db_name`,
   `broker_mode`, `git_sha`, `mc_url` printed at INFO level so
   operators can grep without re-deploying.

### MC-side defense-in-depth (their team)
- `sidecar_checkin_audit` append-only collection with full payload + source_ip
- `GET /api/admin/runtime/sidecar-checkin/{brain}/audit`
- `GET /api/admin/runtime/sidecar-checkin/{brain}/imposter-scan` flags
  `imposter_suspected=true` when >1 distinct identity sustains ≥3 checkins
- 610/610 MC tripwires green (+5 new)

### Tests added (Alpha side)
- `tests/test_mc_checkin_process_identity.py` (+2 tests)
  - payload schema correctness
  - identity stable within a single process
- `tests/test_admin_runtime_stamp.py` (5 tests, includes
  validator-self-check + gate-state + token-mask invariants)
- `tests/test_mc_keys_proxy.py` (14 tests, full fetch/apply
  contract)

**Backend regression**: **4,032 tests passing** (was 4,011 at session
start; +21 new this session, zero regressions).

### Diagnostic endpoints owned by the trading app
- `GET /api/admin/runtime/stamp` — owner-only, returns:
  - `runtime_stamp` (RuntimeStamp.current())
  - `last_posted_to_mc` (what the periodic loop last POSTed)
  - `validator_self_check` (re-runs MC's prod validator locally)
  - `operator_trading_gate` (blocked/open + reason)
  - `intent_emission` (RISEDUAL_EMIT_INTENTS_TO_MC state)
  - `mc_keys_proxy` (enabled + Polygon/Finnhub presence)
  - `tokens_present` (booleans, NEVER token values)

### Operator gate flipped
- `RISEDUAL_LOCAL_TRADES_BLOCKED=false` set on both preview & prod
- All four local paper-trade chokepoints unblocked
- Paper trader will fire real `paper_trades` rows on next BUY/SELL verdict

### Status going into next session
- ✅ Alpha env validated locally (`validator_self_check.ok = true`)
- ✅ Operator gate open
- ✅ Polygon + Finnhub keys via MC proxy
- ✅ ALPHA_INGEST_TOKEN + ALPHA_MC_INGEST_TOKEN present on prod
- 🔴 `risedual.ai` redeploy PENDING — pushes dupe-pod fix to prod
- 🔴 After redeploy, MC verdict should flip to `prod` within one 5-min cycle
- 🟡 Equity lane toggle still OFF — flip Monday 9:30 AM ET to see first SPY trade
- 🔴 Camaro brain-loop dead (external team)
- 🔴 RedEye 0 intents in 24h (external team)



## Latest Update — 2026-02-27 (Phase 4: Federation Outcome Loop + Consensus)

### 🧠 Phase 4 — 5-Shelly Federation closes the learning loop

The federation moves from "scribe-only" to **closed-loop learning**.
Pre-Phase 4, both `LocalShelly.reason()` and
`MCShelly.reason_across_shellys()` filtered on
`outcome.pnl_pct exists` — but nothing wrote that field, so every
reasoning call permanently reported "Not enough Shelly memory yet"
regardless of how many decisions accumulated.

**Outcome backfill** (`shelly/outcome_backfill.py`):
`backfill_outcome_for_trade(...)` matches on
`(symbol_upper, canonical_direction, opened_at ± 600s)` and stamps
`outcome.pnl_pct` + `outcome.outcome_label` + `outcome.trade_id`
back onto every matching unresolved memory across all 5 LocalShelly
collections + the MC shared aggregator. Idempotent (only writes
when `outcome.pnl_pct` is missing). Per-node `try/except` —
fail-soft. Top-level `authority: memory_reasoning_only` stamp
never modified.

**Wiring**: `services/paper_trade_closer.close_due_paper_trades`
calls the backfill after every successful close, right after the
sovereign outcome bridge. Best-effort, never blocks the close path.

**Alpha-as-paper-trader emission**
(`shelly/brain_emitter.emit_alpha_paper_trade`):
Distinct from the hypothesis-stage emit. Captures Alpha's
EXECUTION-stage decision (post-Kelly, post-RoadGuard,
post-modulator) in the Alpha LocalShelly with
`decision="PAPER_TRADE_OPEN"` and full lineage features
(trade_id, prediction_id, sovereign_decision_id, entry_price,
position_usd, regime). Wired into
`services/ml_paper_trader.maybe_paper_trade` right after the
`paper_trades.insert_one` success branch.

**Consensus endpoint**:
`GET /api/admin/shelly-federation/consensus?symbol=AAPL&direction=LONG`.
Runs `LocalShelly.reason()` on all 5 nodes + the MC cross-brain
aggregator, applies the conservative-priority rollup rule
(`warn > neutral > support` — mirrors the memory modulator's
"losers downweight beats winners upweight" doctrine), and returns
a single rolled-up recommendation alongside the per-node detail.
Read-only. Safe to poll.

**Frontend** (`components/admin/ShellyFederationPanel.jsx`):
- New "Federation" tab in AdminPanel → Insights, between "Shelly"
  and "Shelly Quarantine".
- 5 colored node cards (Alpha/Camaro/Chevelle/RedEye/MC) +
  MC-shared aggregator card showing memories_total /
  memories_pending_rollup / reasoning_receipts_total.
- Consensus dry-run form: symbol + direction → rolled-up verdict
  card (WARN/NEUTRAL/SUPPORT badge + Δ confidence) + per-node
  reasoning breakdown + MC cross-brain by-brain tally chips.
- All elements stamped with `data-testid` per doctrine.

**Tests**: 12 new (`test_shelly_phase4_outcome_backfill.py`).
Coverage: per-node backfill across all 5 + MC shared,
idempotency on already-resolved rows, time-window enforcement,
direction normalisation (up → LONG), per-node fail-soft,
outcome sub-doc field schema, Alpha emit no-op when pipeline
missing, Alpha emit feature population, Alpha emit error
swallowing, consensus rollup priority rule (3 cases).

**Backend regression**: **4,011 passing** (was 3,999; +12 new,
zero regressions). All lint clean. Live consensus endpoint
verified end-to-end on the preview pod.

**Brain coordination memo**
(`/app/memory/RESPONSE_TO_BRAIN_AUTHORS_FEDERATION_PHASE_4.md`):
Sidecar authors notified that the trading-app's outcome loop is
live, with the symmetric pattern documented if they want their
brain-side mirror to do the same. No coordination required —
their existing opinion / contribution surfaces are unchanged.

### Pending — Phase 5+ candidates
- **Council brain-paths emit at execution-stage** (currently only
  hypothesis-stage). Adds Camaro / Chevelle / RedEye receipts at
  trade emission so the federation's recommendation surface
  matches Alpha symmetrically.
- **Federation similarity-search UI** — surface
  `/api/admin/shelly-federation/similarity` (Chroma vector top-K)
  in the panel as an expandable per-node section.
- **Federation reasoning timeline** — per-symbol historical view
  of how the rolled-up recommendation has evolved.



## Latest Update — 2026-02-26 (Phase 1: Shelly-MC wired)

### 🧠 Phase 1 — MC Shelly receiving its first real verdicts

Per the operator's phased rollout plan
(Phase 1 = MC Shelly → Phase 2 = brain Shellys → Phase 3 = cross-Shelly federation),
this batch lights up Phase 1 end-to-end.

**Singleton pipeline** wired at `server.py` startup
(`app.state.shelly_pipeline`). Boot log line:
`Shelly Federation wired: 5 nodes (Alpha, Camaro, Chevelle, RedEye, MC)`.

**MC emitter helper** (`shelly/mc_emitter.py`):
- `set_pipeline()` / `get_pipeline()` — fail-soft singleton.
- `emit_mc_event(verdict_type, symbol, direction, decision, features, ...)` —
  shapes any MC verifier/notary verdict into a
  `ShellyMemoryEvent` and routes it through
  `ShellyPipeline.record_brain_event("MC", ...)`. Doctrine
  stamp applied by the pipeline. Swallows all exceptions
  (logs DEBUG) so MC code never blocks on Shelly.

**First MC verifier site wired**: `sovereign_promotion_gate.compute_sovereign_promotion_status`
emits one `verdict_type=sovereign_promotion_gate` event per
gate run, capturing the full verdict (rows_resolved,
win_rate, calibration_avg, rolling_win_rate, blocker)
into MC's LocalShelly. **Live preview verified**: 4
gate runs produced 4 MC-Shelly memories + 8 reasoning
receipts (local + cross-Shelly).

**Nightly rollup scheduled** in `services/scheduling/jobs.py`
at 02:15 UTC — drains all 5 LocalShellys (brains + MC)
into the shared aggregator. Job id:
`shelly_federation_rollup_nightly`.

**Tests**: 4 new in `test_shelly_mc_emitter.py` (no-pipeline
skip, doctrine routing, exception swallowing, full
promotion-gate integration). Full backend suite:
**3,990 passing** (was 3,986; +4 new, zero regressions).

### Next — Phase 2 wiring sites
The 4 brain receipt-emission paths the operator needs to
nominate:
- **Alpha** — `services/ml_paper_trader.maybe_paper_trade`
  (after the signal + sovereign decision are built).
- **Camaro / Chevelle / RedEye** — adversarial council
  vote emission (need operator confirmation of exact
  file path).



## Latest Update — 2026-02-26 (Fork G, 5-Shelly Federation)

### 🧠 5-Shelly memory + reasoning federation (drop-in, doctrine-locked)

Built the operator-specified 5-Shelly architecture: one LocalShelly
per execution-authority brain (Alpha / Camaro / Chevelle / RedEye)
plus an MCShelly head that ingests rollups and reasons across the
federation. Strict authority discipline — every persisted doc and
every returned dict carries `authority: "memory_reasoning_only"`.
Shelly may *recommend* (support / warn / neutral / conflict);
Shelly may NOT execute, block, override, or promote.

**Drop-in module** (`/app/backend/shelly/`):
- `contracts.py` — `ShellyMemoryEvent`, `ShellyReasoningReceipt`
  dataclasses + `stable_hash()` + `utc_now()`.
- `config.py` — `BRAIN_NAMES`, sample-size floors, warn/support
  thresholds (`LOCAL_LOSS_RATE_WARN=0.60`,
  `MC_LOSS_RATE_SUPPORT=0.35`, etc.).
- `local_shelly.py` — `LocalShelly` (per brain): `remember()`,
  `reason()`, `rollup_for_mc()`, `mark_rolled_to_mc()`. Async
  (motor) all the way, `_id` stripped at query time.
- `mc_shelly.py` — `MCShelly` head: `ingest_rollup()` (dedup on
  event_hash), `reason_across_shellys()` (cross-brain tally +
  explicit brain-conflict detection — if Alpha/Camaro polarize
  win-side while Chevelle/RedEye polarize loss-side, the
  receipt names them).
- `pipeline.py` — `ShellyPipeline` orchestrator:
  `record_brain_event(brain, receipt)` → local memory + local
  reasoning + MC reasoning; `rollup_all_to_mc()` for the
  scheduled drain.

**Admin route** (`routes/admin_shelly_federation.py`, owner-only):
- `GET /api/admin/shelly-federation/state` — per-brain memory +
  receipt counts + MC shared totals.
- `GET /api/admin/shelly-federation/recent-receipts?limit=N` —
  newest MC reasoning receipts.
- `POST /api/admin/shelly-federation/reason` — stateless
  dry-run reasoning against any `{symbol, direction}` so the
  operator can spot-check "what does the federation know about
  this setup right now?"

**Tests**: 17 new (`test_shelly_federation.py`). Coverage:
- Contract invariants (stable_hash order-insensitive, doctrine
  stamp present, receipt_hash excludes timestamp).
- LocalShelly idempotency, warn-on-loss-streak, neutral on no
  history, rollup marking.
- MC ingest dedup, support / warn / neutral / sample-floor
  paths.
- Pipeline four-brain instantiation, three-layer write,
  unknown-brain rejection, end-to-end rollup drain.
- **Doctrine invariant** — every persisted Shelly doc across
  every collection carries `authority: memory_reasoning_only`.

Full backend suite: **3,985 passing** (was 3,968; +17 new, zero
regressions). Live preview endpoints all return the doctrine stamp;
state endpoint reports 0 memories across all 4 brains (expected —
brains haven't been wired to call `pipeline.record_brain_event()`
yet, that's the operator's next integration step).

**How to teach Shelly** (one-line integration per brain receipt):
```python
shelly_result = await pipeline.record_brain_event(brain, receipt)
receipt["shelly"] = {
    "local_reasoning": shelly_result["local_reasoning"],
    "mc_reasoning":    shelly_result["mc_reasoning"],
    "authority":       "memory_reasoning_only",
}
```
Plus one scheduled `await pipeline.rollup_all_to_mc()` (nightly).
No execution-path changes anywhere.



## Latest Update — 2026-02-26 (Fork G, P2 follow-up: profiles + UI)

### 🟠 More named profiles + frontend Discipline picker

**Three new profiles registered** (`services/kill_switch_profiles.py`):
- `intraday_momentum` — 5% daily-loss floor, 2-loss cut, 15%
  profit cap (give-back guard for hot tape).
- `swing_trader` — 15% daily-loss tolerance (one-day noise
  shouldn't kill a multi-day thesis), 5-loss cut, no profit cap.
- `conservative_ira` — 3% daily-loss floor + hard $500 USD
  floor, 2-loss cut, no profit cap.
- Source labels make it explicit that the three new profiles are
  "RISEDUAL platform default — …" not direct citations. Profile
  changes still go through PR review — registry stays
  git-auditable.

**Frontend Discipline tab**
(`components/admin/DisciplineProfilePicker.jsx`):
- New AdminPanel tab between Trading Gate and Terminal
  ("Discipline").
- Owner-only — uses `authFetch` against the three runtime
  endpoints.
- Registry overview (4 cards): per-profile rules + source.
- Per-asset_type gate cards (equity + crypto) with three states:
  - **Inactive** — profile dropdown + starting-equity input +
    Activate button.
  - **Armed** — live config + today's P&L + consecutive losses +
    closes-today + Deactivate button.
  - **Halt** — rose-tinted card with the per-rule trigger
    messages spelled out.
- Toast feedback on activate / deactivate / errors.
- All interactive elements carry `data-testid` per doctrine.

**Tests**: 8 new profile-evaluator tests in
`test_kill_switch_profiles_extended.py`. Full backend suite:
**3,968 passing** (was 3,960; +8 new, zero regressions). Frontend
lint clean; live page renders without compile errors.

**Live smoke (preview, owner JWT)**: `/api/admin/kill-switch/profiles`
returns count=4 with correct rule shapes for all four profiles.



## Latest Update — 2026-02-26 (Fork G, P2 Warrior Live Gate)

### 🟠 P2 — Warrior Small Account profile wired into the live emission gate

The named discipline profile from the previous fork was a definition
+ inspection-only surface. This batch adds the live gate so an
operator can actually run the discipline overlay against the equity
or crypto paper-trader cores.

**Runtime helpers** (`services/kill_switch_profile_runtime.py`):
- `get_active_profile_config(db, asset_type)` — singleton config
  read from `kill_switch_profile_global` collection.
- `set_active_profile_config(...)` / `clear_active_profile_config(...)` —
  upsert / delete writes.
- `compute_session_stats(...)` — tallies today's realised P&L
  (`pnl_usd` sum) and consecutive-loss streak (scan most-recent
  closes desc, count contiguous `outcome=="loss"`). Session
  boundary = UTC midnight.
- `check_session_halt(db, asset_type)` — runs the active profile's
  evaluator against live session stats; returns the
  `ProfileEvaluation` when any halt rule fires, else `None`.
- **Fail-soft**: any config or Mongo failure degrades to no-halt
  so the discipline overlay can't ground the brain on its own bug.

**Admin routes** (`routes/admin_kill_switch_profile_runtime.py`,
owner-only):
- `POST /api/admin/kill-switch/runtime/{asset_type}/activate` —
  enable a profile with starting equity + optional note.
- `DELETE /api/admin/kill-switch/runtime/{asset_type}/active` —
  clear the override.
- `GET /api/admin/kill-switch/runtime/{asset_type}/status` —
  live readout: config + session stats + halt verdict + trigger
  list. The full operator dashboard signal in one call.

**Live gate hook** (`services/ml_paper_trader.maybe_paper_trade`):
- Inserted as the FIRST gate after the directional-confidence read.
- When `check_session_halt(db, "equity")` returns a halt verdict,
  the trade emission short-circuits to `None`, logs
  `[ml_paper] Kill-Switch Profile HALT <ticker> profile=<key>
  triggers=<rules>`, and writes an `activity_logger`
  `log_paper_trade_skipped` row with the per-rule messages.
- Default behaviour: no profile configured → silent no-op, brain
  runs exactly as before.

**Tests**: 14 new (`test_kill_switch_profile_runtime.py` — 7,
`test_admin_kill_switch_profile_runtime_route.py` — 7).
Full backend suite: **3,960 passing** (was 3,946; +14 new,
zero regressions).

**Live smoke (preview)**: activated Warrior on equity at $1,000
starting equity → status returns clean `{active: true, halt: false,
realized_pnl_usd_today: 0.0, consecutive_losses_today: 0}`;
deactivation cleared cleanly.



## Latest Update — 2026-02-26 (Fork G, P0 Learning Pipeline Unblock)

### 🔴 P0 — Sovereign learning pipeline can finally resolve decisions

The operator correctly identified that the promotion gate was stuck
at `0/500 resolved` not because of data volume but because of a
**structural break**: HOLD decisions never resolved (no fired trade
to compare against) and non-HOLD decisions only resolved when the
paper trader actually fired — both gated by code, not by data.

**Three-piece fix landed in one batch**:

1. **`/api/admin/sovereign/learning-health`** diagnostic endpoint
   (`routes/admin_sovereign_learning_health.py`, owner-only) —
   surfaces total decisions, action distribution, last-24h
   throughput, count carrying `entry_price`, per-horizon
   resolution counts (aged / resolved / was_right / unresolved
   aged), and the promotion-gate snapshot inline. The operator
   can see the whole pipeline in one call.
   - **Live preview reading** confirms 3,105 crypto decisions
     accumulated but **0 carry entry_price** (legacy), 0
     resolved at every horizon, 3,105 unresolved-aged.

2. **`feature_snapshot.entry_price`** added to `SovereignFeatures`
   dataclass (`services/sovereign_ai_core.py`). Wired through
   from both paper-trader call sites (`ml_paper_trader.py`,
   `crypto_paper_trader.py`) to capture the entry price at
   decide-time. Every new sovereign decision going forward will
   carry the field, opening the door to drift-based resolution.

3. **Drift-resolution branch** in
   `services/sovereign_drift_resolver.py` + integrated into
   `sovereign_resolution_loop._resolve_one_horizon`. For
   decisions aged past a horizon with NO linked closed
   paper_trade:
   - **HOLD** is "right" when `|drift_pct| <= tolerance`
     (default 0.5% equity, 1% crypto, env-tunable).
   - **LONG** is "right" when `drift_pct > 0`.
   - **SHORT** is "right" when `drift_pct < 0`.
   - Legacy rows without `entry_price` are skipped with a
     stable reason (`no_entry_price`) so the diagnostic
     endpoint surfaces what's blocking.
   - Fetches current price via `MarketDataService.get_quote()` /
     `.get_crypto_quote()`.

**Tests**: 22 new (`test_sovereign_drift_resolver.py` — 12 cases;
`test_admin_sovereign_learning_health.py` — 5; `test_sovereign_resolution_drift_fallback.py` — 3). Full backend
suite: **3,946 passing** (was 3,924; +22 new, zero regressions).

**Impact on Stage 4 readiness**: The promotion gate is now
structurally reachable. Once production redeploys this batch,
every new sovereign decision will be scoreable — including the
~29% HOLD share that was permanently muted before. The
`0/500 resolved` counter will start climbing on its own.



## Latest Update — 2026-02-26 (Fork G, P2 Small Account)

### 🟠 P2 — Small Account Mode (Named Kill-Switch Profiles)

Translated the public Warrior Trading "2025 Small Account Toolkit"
discipline rules into a code-versioned, named profile so a small-
account trader can opt into the documented discipline overlay
without re-implementing it manually each session.

**Profile** (`services/kill_switch_profiles.py`):
- `small_account_warrior` profile encodes the toolkit's three
  written rules:
  - **Rule 2 — Daily max loss**: -10% of starting equity OR a hard
    -$100 USD floor, whichever trips first (belt-and-suspenders
    for sub-$1,000 accounts where 10% rounds tiny).
  - **Rule 3 — Three consecutive losers**: session over after 3
    consecutive losing trades.
  - Toolkit explicitly says "don't stop until momentum cools" →
    NO profit-give-back cap baked in.
- Pure-function evaluator (`evaluate_profile`) — sync, no DB, no
  async. Can be called from the paper-trade emission path, the
  admin endpoint, or unit tests with the same code.
- Frozen dataclass profile registry — admin can't redefine a
  profile via HTTP; profile changes go through PR review.

**Routes** (`routes/admin_kill_switch_profiles.py`, owner-only):
- `GET /api/admin/kill-switch/profiles` — list registry
- `GET /api/admin/kill-switch/profiles/{key}` — single lookup
- `POST /api/admin/kill-switch/profiles/{key}/evaluate` — dry-run
  evaluator against `{starting_equity_usd, realized_pnl_usd_today,
  consecutive_losses_today}`; returns full per-rule trigger
  breakdown with the operator-friendly message ("daily loss
  -120.00 ≤ -100.00 (10% of starting equity)").

**Tests**: 19 new (`test_kill_switch_profiles.py` evaluator +
`test_admin_kill_switch_profiles_route.py` route). Coverage
includes quiet day, percent floor, USD floor, 3-loss halt,
2-loss no-halt, no-profit-cap behaviour, unknown profile, and
owner-role enforcement. Full backend suite: **3,924 passing**
(was 3,905; +19 new, zero regressions).

**Doctrine note** — the profile is intentionally NOT auto-wired
into the paper-trade emission path yet. This is the *definition +
inspection* step. Wiring the live gate (admin opt-in, per-account
state tracker) is the natural follow-up.



## Latest Update — 2026-02-26 (Fork G)

### 🔵 P3 — Options Education Layer

Plain-language options glossary so every chart/screener in the
Options hub can render inline definition tooltips without leaving
the page, and so a dedicated "Learn" tab gives newcomers the full
vocabulary in one searchable place.

**Backend** (`routes/learn_options.py`):
- Static, curated 29-term glossary covering **Basics, Greeks,
  Mechanics, Strategies** (call/put/strike/premium/IV/Greeks/
  covered call/CSP/credit & debit spreads/iron condor/straddle/
  strangle/…). Definitions follow ClearValue Investing's
  beginner-friendly style — one tooltip sentence + one paragraph.
- `GET /api/learn/options` — full grouped payload (count,
  categories, terms map).
- `GET /api/learn/options/{term_key}` — single-term lookup
  (case-insensitive, 404 on unknown).
- Mounted via `route_registry.py` under `/api/learn`. No DB
  schema, no LLM calls — git-auditable glossary updates.

**Frontend** (`components/OptionsTermTooltip.jsx`,
`components/OptionsLearn.jsx`):
- `<OptionsTermTooltip termKey="delta">Δ</OptionsTermTooltip>` —
  inline tooltip wrapper backed by a session-cached glossary
  (`window.__optionsGlossary`). Unknown terms render plain — no
  dead trigger.
- New "Learn" tab added to `hubs/OptionsHub.jsx` alongside Radar
  / Flow / Dark Pool. Renders a searchable card grid grouped by
  category, with live count + per-card data-testids.

**Tests**: `tests/test_learn_options.py` (7 cases) — shape
validation, core-term presence, list endpoint, case-insensitive
lookup, 404 path. Full backend suite: **3,905 passing** (was
3,898; +7 new, zero regressions).


## Latest Update — 2026-02-23 (Fork F)

### 🚨 PROD-DEPLOY FIX — In-Process Sovereign Sidecar

Operator screenshot showed Alpha at `HEARTBEAT ONLY · 54987s ago`
(15h stale) on MC's dashboard. MC team responded with a source-
cited diagnosis: the classifier is age-based on
``sovereign_state.updated_at`` — every accepted 200-OK contribution
refreshes that age. So 54987s of staleness implies **no
contributions reaching MC for 15h**.

**Root cause:** ``alpha-sidecar.conf`` lives at
``/etc/supervisor/conf.d/`` on the **preview pod** but is NOT
inside ``/app``. Emergent's deploy image
(``fastapi_react_mongo_shadcn_base_image_cloud_arm``) doesn't ship
custom supervisor configs. So **production has been running with
NO Sovereign sidecar process at all** since the deploy — the
brain's HTTP layer in preview was healthy and 200-OK-ing, but prod
had no sender. This is consistent with everything: empty
scorecard, 0 wins/0 losses, frozen weights, observation_fills
never resolving — none of those signals were arriving at MC.

**Fix:** New ``sovereign/inprocess_sidecar.py`` module spawns the
SAME ``SovereignSidecar`` class as the supervisor process — but
as a FastAPI lifespan asyncio task that ships automatically with
``/app/backend``. Wired into ``server.py`` startup + shutdown
hooks.

**Doctrine pins:**
* Default OFF via ``ALPHA_INPROCESS_SIDECAR_ENABLED`` — preview
  (where the supervisor sidecar IS running) stays exactly as it
  was.
* Lockfile guard at ``/tmp/alpha_alive`` — if the supervisor
  sidecar is alive (preview), the in-process loop no-ops on
  startup. No race, no double-POST.
* Fail-soft: missing ``MC_BASE_URL`` / ``ALPHA_INGEST_TOKEN``
  logs a warning and the loop exits cleanly. Backend still
  serves API.
* Identical payload shape to the supervisor sidecar — same
  ``SovereignSidecar`` class, same lineage stamp, same outcome-
  inbox drain, same 422-substantive empty-payload refusal.

**Operator runbook for prod:**
1. Push to GitHub + redeploy. Backend ships with the new module
   but inert (master switch OFF).
2. Set ``ALPHA_INPROCESS_SIDECAR_ENABLED=1`` in prod env.
3. Bounce the pod. Within 60s, MC's `sv_iso` age should drop
   from 54987s → <60s and the dashboard badge flips off
   `HEARTBEAT ONLY`.
4. Within ~24h (after one closed observation_fill cycle),
   weights start moving, scorecard fills, promotion gate
   re-evaluates.

**Live-verified in preview:**
- Default-OFF: backend startup logs `[alpha_inprocess_sidecar] startup: skipped (disabled)` ✅
- Lockfile guard with stale lockfile: ``supervisor_winning() == False`` ✅
- Enabled + no env: fail-soft (loop exits, no crash) ✅
- Enabled + lockfile fresh: `[alpha_inprocess_sidecar] startup: skipped (supervisor_present)` ✅

**11 new tests + 3,877 / 0 skipped / 0 failures backend suite.**

### 🧠 Memory Modulator v1 — Brain-Symmetric Confidence Modulator

Shipped operator's drop-in memory modulator at
``shared/memory_modulator.py`` with all four P0 safety adds:

* **P0-1 Quarantine exclusion** — every candidate memory passes
  through ``chevelle_memory_labeler.label_memory`` BEFORE
  similarity is computed. Rows with ``trust_weight == 0.0`` or
  any ``rejection_reason`` are dropped. Defensive: a raise in
  the labeler itself is treated as a quarantine signal — a
  noisy upstream row can't slip past the firewall.
* **P0-2 Bound enforcement** — modulator value clamped to
  ``[-0.25, +0.10]`` at THREE points: at compute time, at
  application time (``apply_modulator_to_confidence``), and (by
  MC tripwire on receipt, per operator). Even a buggy upstream
  ``99.0`` can't move confidence by more than ``+0.10``.
* **P0-3 Receipt persisted** — stamped on BOTH the intent
  payload (``emit_intent_from_consensus(payload)``) AND every
  paper_trade row (``trade_doc["memory_modulator"]``) so the
  audit lineage (modulator → conf → Kelly → fill) is
  reconstructable post-hoc, not just on the MC-side intent.
* **P0-4 Feature normalization** — per-feature clip to
  ``[-3.0, +3.0]`` + stable whitelist of 13 features. A single
  rogue ``volume_zscore=9999`` can never dominate the cosine; an
  attacker / regression adding a never-seen feature can't slip
  into the similarity vector.

**Doctrine pinned in code + tests:**
* HOLD never modulated.
* Direction never created (receipt has no
  ``recommended_direction`` / ``flip`` / ``override`` field).
* Confidence-only mutation; gates / ladder / RoadGuard untouched.
* Symmetric across Alpha/Camaro/Chevelle/REDEYE — same code
  path, no per-brain branches; the scoreboard decides.
* Losers downweight takes priority over winners upweight (the
  conservative move when both thresholds met).

**Wiring (this repo = Alpha):**
``services/ml_paper_trader.maybe_paper_trade`` calls the
modulator AFTER the Sovereign shadow and BEFORE Kelly sizing,
so the confidence delta actually reaches the position-sizer.
Honest-hold emits stamp the receipt onto MC's intent payload.

**Env knobs (operator-tunable):**
- ``MEMORY_MODULATOR_ENABLED`` (default ON; ``false`` quarantines the modulator itself at runtime)
- ``MEMORY_MODULATOR_LOOKBACK_DAYS`` (90)
- ``MEMORY_MODULATOR_SIM_THRESHOLD`` (0.85)
- ``MEMORY_MODULATOR_MAX_UP`` / ``_MAX_DOWN`` (0.10 / -0.25; clamped at the doctrine bound even if env tries to widen)
- ``MEMORY_MODULATOR_MIN_MATCHES_FOR_UP`` (5)
- ``MEMORY_MODULATOR_MIN_MATCHES_FOR_DOWN`` (2)

**24 new tests** pinning every doctrine + safety property.
``test_no_local_direction_tuples`` allowlist extended with a
justified entry for ``shared/memory_modulator.py`` (it must
accept the common verdict tokens directly since importing
``services.prediction_tracker`` would break the drop-in
contract for the other 3 brains).

**Test suite: 3,866 passed / 0 skipped / 0 failures**

### 🎯 Alpaca Position Closer — Live Broker Exit Engine

**Background:** Operator forensics (screenshot Feb-23) surfaced
that brain-opened Alpaca positions (NVDA 90sh, AMZN 78sh,
GOOGL 53sh, MSFT 50sh, META 1.3sh, plus SPY option spreads) had
been accumulating for weeks because the brain had a BUY path
(``ml_alpaca_broker._submit_market_order(side="buy")``) but
**no matching close path**. The ``paper_trade_closer`` was
marking the bookkeeping rows ``closed`` and computing PnL from
quotes, but never telling Alpaca to actually sell. PDT cliff
June 4 made this urgent.

**Shipped:** ``services/alpaca_position_closer.py`` — a real
exit engine that:
1. Polls Alpaca's ``GET /v2/positions`` (broker = source of
   truth; avoids the ``paper_trades`` drift documented in the
   NVDA-23-vs-168 forensic).
2. Resolves entry/peak/opened_at from the matching
   ``live_orders`` row.
3. Applies the **same** equity exit cascade as
   ``tier3_paper_closer`` (SL 1% → TP off-default → trail
   2%/50%-giveback → max-hold 36h) so paper + live grade
   identically.
4. For options, layers a hard close N days before expiry
   (default 5, ``OPTIONS_PRE_EXPIRY_DAYS``) on top of the
   standard envelope — assignment hygiene for the June-4 PDT
   transition.
5. Submits ``sell`` / ``buy_to_close`` / ``sell_to_close`` via
   the existing ``_submit_market_order`` helper.
6. Mirrors the SELL into ``paper_trades`` so the existing
   outcome-bridge → Sovereign learning pipeline picks up the
   live exit.

**Safety rails:**
- Master switch ``ALPACA_POSITION_CLOSER_ENABLED`` default
  **OFF** so a routine backend restart can't surprise-fire.
- Dry-run mode ``ALPACA_POSITION_CLOSER_DRY_RUN`` default
  **ON** when enabled — logs decisions and writes mirror rows
  with ``dry_run=True`` for operator audit before going hot.
- Idempotency window (default 600s) — no double-fire while
  Alpaca's position list catches up.
- Per-position try/except — one bad symbol never poisons the
  sweep.

**Schedule:** every 5 minutes (matches ``position_reconciler``).
Confirmed in startup log:
``..., position reconciler (30m), alpaca position closer (5m), drift alert watcher (5m), ...``

**Tests:** 14 new pinning master/dry-run switches, OCC option
parsing, pre-expiry priority, equity-cascade fallback, anchor
resolution, idempotency, per-position error isolation, and the
mirror-row stamps.

**Test suite: 3,842 passed / 0 skipped / 0 failures.**

**Operator playbook for June-4 PDT window:**
1. After redeploy, `alpaca position closer (5m)` will be live
   in the scheduler but inert (master switch OFF).
2. Set ``ALPACA_POSITION_CLOSER_ENABLED=true`` in prod env.
   Dry-run still ON — audit the decision log for one trading
   day.
3. When tape looks right, set
   ``ALPACA_POSITION_CLOSER_DRY_RUN=false``. Closer goes hot.
4. Monitor: ``grep "alpaca-closer\|Alpaca position closer" /var/log/...``

### 🐛 Outcome-Label Math Fixed for Observation Rungs

End-to-end smoke-testing the auto-resolver caught a fourth silent
prod bug: `paper_trade_closer._compute_close` derived the
``outcome`` label from $ PnL — which is always **$0** for
``observation_fill`` rows (shares=0). That meant every
observation rung, regardless of whether Alpha's directional bet
was right, was labelled ``flat`` on the wire.

**Impact:** Even after the NameError fixes from earlier this
session land in prod, the Sovereign learning signal from
observation rungs would have been **100% flat** — Tier 3
counters would tick but MC's outcome stream would carry zero
learning value.

**Fixed:** outcome now derived from ``pnl_pct`` with the same
±0.5% threshold the ``backfill_outcome_pairer`` uses. Sign
agreement with $ PnL is preserved for real trades, so the rule
is backward-compatible. 6 new regression pins (including a
cross-module pin that the threshold MUST match
``_WIN_THRESHOLD`` to prevent silent drift).

**Live-verified end-to-end on preview:**
- Seeded 48h-old observation_fill row (NVDA, +48% pct since
  entry) → closer marked `observation_closed`, `outcome=win`,
  inbox row carries `outcome_label="win"`, `outcome=1`, plus
  all 3 provenance fields (`sovereign_decision_id`,
  `prediction_id`, `source_signal`).
- Mirror loss scenario (short observation, market up 48%) →
  `outcome=loss`, `outcome=-1`. Bidirectional grading confirmed.

**Test suite: 3,828 passed / 0 skipped / 0 failures.**

### 🐛 Three Silent Prod Bugs Found via Skipped-Test Audit

Investigating the lone `1 skipped` in the test suite surfaced
three real production bugs in `services/ml_paper_trader.py`'s
Kelly-zero branch (added 2026-05-22) — all hidden by the broad
``try/except`` meant to protect against MC outages:

1. **`direction_val` UnboundLocalError** — used at line 570 etc.
   but only assigned post-Kelly (line 797). Every Kelly-zero
   tick raised NameError silently, **dropping every honest-hold
   receipt to MC since 2026-05-22**.
2. **Wrong literal comparison** — `direction_val == "long"`
   would never be true (the field carries `"up"`/`"down"`). Had
   the NameError NOT fired first, every emit would have stamped
   `SELL` regardless of true brain direction.
3. **`price_at_signal` was never assigned anywhere** — pure
   NameError on the observation_fill insert. **Every Kelly-zero
   tick was failing to write its observation_fill row**, which
   means the Tier 3 observation ladder's `days_active` /
   `total_trades` counters HAVE NOT BEEN ticking in prod since
   the rung shipped.

**Fixed:**
- Compute `direction_val` + `_is_long` at the top of the Kelly-zero
  branch.
- Replace `price_at_signal` with `snapshot.close_price` (with
  safe fallback).
- Default `sovereign_decision_id` via `locals().get(...)` when
  the upstream sovereign-shadow branch is skipped.

**Mock-drifted test resurrected:**
- `test_kelly_zero_calls_emit_intent_from_consensus` was passing
  args in the legacy `(db, ticker, signal, snapshot, regime, ...)`
  order — but the current signature is
  `(ticker, signal, snapshot, regime, db, ...)`. Fixed call
  order; added regime token correction (`trending_up`); stubbed
  `get_dynamic_confidence_threshold` so the realistic `0.65`
  confidence reaches the Kelly check; replaced defensive
  `pytest.skip` with hard `pytest.fail` so future drift surfaces
  loudly.
- Added 4 new regression pins that catch each NameError /
  literal-comparison bug by static authority — they cannot
  silently come back.

**Test suite:** **3,822 passed / 0 skipped / 0 failures** (up
from 3,817 with 1 skipped — and the skipped one was masking the
above prod bugs).

### 🛡️ MC Empty-Payload Alignment — `_contribution_loop` Deleted

MC shipped a 422-on-empty enforcement (substantive-rule: any of
`notes`/`weights`/`recent_outcomes`/`delta_reason`/`confidence_delta`
non-default). The in-process `services/mc_sidecar.py::_contribution_loop`
was the source of Alpha's `(empty payload)` audit rows — it posted a
hardcoded `weights+notes` body every interval and raced the
supervisor sidecar on the outcome inbox drain.

**Deleted:** `_contribution_loop` (+ `_contrib_task` global). The
supervisor-run `sovereign.sidecar` (PID 44, `alpha-sidecar.conf`) is
now the canonical contribution producer — single source of truth,
deterministic inbox drain, real LocalState-backed content, the
2026-05-22 empty-payload refusal still active.

**Heartbeat survives** — the in-process `_heartbeat_loop` is
untouched AND the supervisor sidecar has its own heartbeat thread.
Two independent paths to `/api/heartbeat-ping/alpha` means MC sees
liveness even if one path dies (the right belt-and-suspenders
surface, at the heartbeat layer).

**Lineage stamp** — per MC operator's ask, every contribution from
the supervisor sidecar now carries lineage in `notes`:
`sidecar v<X> · supervisor · contribution_id=<uuid12> · tick @ <ts>`
so audit-log skimming can distinguish supervisor contributions from
any future noise sources.

**Tests:** 7 new (3 mc_sidecar.py deletion pins + 4 lineage stamp
pins), all passing. Full suite **3,817 passed / 1 skipped / 0
failures**.

**Live-verified:** Supervisor sidecar restarted cleanly post-change;
heartbeat + contribution both returning 200 from MC; outcome inbox
drain finding 0 races (was 0/20 vs the old in-process-loop pattern).

### 🎯 P0 + P1 — MC Visibility Gaps + LLM Budget Mitigations
Closed the two MC visibility gaps that were causing Mission Control to
see empty/unattributed outcome contributions, and shipped a 2-layer
LLM budget-exhaustion guard for the Hypothesis tab.

**P0 — MC Visibility Gaps:**
- `scripts/reconcile_alpaca_orders.py` now accepts `--enqueue-outcomes`.
  When set, mirrored Alpaca BUY/SELL legs are FIFO-paired into
  round-trip outcomes (win/loss/flat by ±0.5% threshold) and pushed
  to the Sovereign outcome inbox, carrying the BUY lot's
  provenance through to MC.
- `services/backfill_outcome_pairer.py` — new FIFO pairer.
- `sovereign/local_state.py::add_outcome` — accepts `sovereign_decision_id`,
  `prediction_id`, `source_signal` (all optional). New `RECENT_OUTCOME_FIELDS`
  constant lists the canonical schema. Missing values are omitted
  (not stored as null keys) so MC's schema stays additive.
- `sovereign/mc_client.py::build_contribution_body` — emits provenance
  fields on the wire when present.
- `sovereign/sidecar.py::tick` — forwards provenance from drained
  rows to `state.add_outcome`.
- `services/paper_trade_closer.py` — forwards provenance to
  `enqueue_outcome` so live paper closures populate the lineage.
- `services/sovereign_outcome_bridge.py::enqueue_outcome` — promotes
  provenance from kwargs to top-level Mongo columns (not nested in
  `extras`) so the drainer + MC client can forward without parsing.

**P1 — LLM Budget Mitigations:**
- `services/hypothesis_cache.py` — new Mongo TTL cache. Default
  10-min TTL, clamped to `[60, 3600]s` via `HYPOTHESIS_CACHE_TTL_SECONDS`.
  Cache key = `symbol:model_key:data_fingerprint`. Best-effort:
  any DB error returns None / no-op so cache health never blocks
  the hypothesis path.
- `services/llm_fallback.py` — BYO direct API key fallback. When
  the Emergent LLM key returns a budget/quota error AND the
  operator has set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` /
  `GEMINI_API_KEY`, the call retries via the official provider
  SDK. Non-budget errors bubble up unchanged so the consensus
  filter still drops bad votes.
- `services/multi_model_hypothesis_service.py::_run_single_model`
  now wraps the LlmChat call in
  `call_with_emergent_then_fallback` and consults the cache
  before spending tokens. `served_from_cache=True` is stamped on
  cache hits for operator audit.

**Tests (32 new, all passing):**
- `tests/test_mc_visibility_gaps_2026_05_22.py` — 14 tests.
- `tests/test_llm_budget_mitigations_2026_05_22.py` — 18 tests.
- `tests/test_alpha_empty_contribution_refused.py` — hardened
  against a latent flake (paper_trade_closer enqueueing during
  the test would defeat the monkeypatch); both `outcome_inbox_client`
  module instances are now patched.

**Test suite total:** **3,810 passed, 1 skipped, 0 failures** (up
from 3,778 at fork start).

## Architecture
- **FastAPI** backend on `:8001` with `/api` prefix
- **React** frontend on `:3000`
- **MongoDB** for persistence + ChromaDB for vector memory (Shelly)
- **8-ML neuro-symbolic stack** (`RisedualMLPipeline`) sitting alongside the LLM Council
- **Closed-loop RoadGuard pair**: dedicated capital governor per executor lane
- All risk-modulating layers operate in shadow mode until calibration delta logs prove safe

### Canonical pipeline flow
```
market data
  -> Shelly recall (memory consult; observation only)
  -> perception ML (6 sklearn models + 5-rule symbolic engine)
  -> strategist ML
  -> auditor ML
  -> fast_veto ML (veto-only)
  -> shadow ML (observe only)
  -> executor ML  (lane-routed: EquityExecutorML | CryptoExecutorML)
  -> Shelly remember (organic episode)
  -> RoadGuard pair (closed loop):
        equity intent -> EquityRoadGuard  -> roadguard_equity_decisions
        crypto intent -> CryptoRoadGuard  -> roadguard_crypto_decisions
  -> [broker — disabled]
```

## What's Implemented (this fork — 2026-05-08 / 2026-05-09 / 2026-05-10 / 2026-05-13 / 2026-02 Feb fork)

### 🏆 Stage 3 — Sovereign vs Council Evidence Pipeline (2026-02-19)

Closed the Sovereign AI Promotion Plan Stage 3 — paired-verdict ledger
with backfilled realised PnL so the operator can read both AIs
side-by-side and judge promotion-readiness.

**New modules:**
- `services/decision_outcome_writer.py` — `_score_voice` (routes through
  `prediction_tracker.canonical_ai_dir`), `_decide_winner`,
  `write_outcome_for_trade`, `attach_council_verdict`, `aggregate_stats`.
- `routes/admin_decision_pairs.py` — owner-only `GET /api/admin/decision-pairs`
  and `/stats`. Registered in `route_registry.py`.
- `components/admin/Stage3DecisionPairs.jsx` — operator dashboard (4-tile
  scoreboard + head-to-head winners + expandable pair ledger).
  Wired into AdminPanel → Insights → "Sov vs Council".

**Wiring (live flow):**
- `ml_paper_trader.maybe_paper_trade` files a pair immediately after
  `paper_trades.insert_one` (council=ABSENT placeholder, attached later).
- `paper_trade_closer.close_due_paper_trades` calls `write_outcome_for_trade`
  after every successful close — best-effort.
- `multi_model_hypothesis_service.generate_hypothesis` (consensus branch)
  calls `attach_council_verdict` to backfill the council voice onto any
  open pair for the same symbol within 10 min.

**Doctrine pinned:**
- `sovereign_voice` remains pure templates — no LLM imports (CI test).
- `file_decision_pair` idempotent on `decision_id`.
- `_score_voice` routes through `canonical_ai_dir` (no private direction
  alias tables; passes `test_no_local_direction_tuples`).

**Test count: 3,688 / 3,688 passing** (was 3,660+; +30 new Stage 3 tests, 0
regressions). Frontend lint clean. Live smoke against demo-seeded pairs:
sovereign 50% accuracy, council 100%, agreement 33%, winners
{sovereign:0, council:1, tie:1, neither:0}.

**Testing agent verdict:** 100% backend (26/26 pytest) and 100% frontend.
No critical or minor issues. All 16 required data-testids present + unique.

**Outstanding (Stage 4+):**
- `/admin/council-policy` endpoint for dynamic confidence floors (P1).
- Alpaca equity `volume_24h_usd` enrichment for 7/7 snapshot completeness (P2).
- Stage 3.5 per-context promotion (lane/regime matrix) (P2).
- Stage 4: Sovereign becomes primary execution authority (P2).
- Stage 5: LLM emergency override / "Auditor of Last Resort" (P2).
- Small Account Mode (Warrior Trading kill-switch profile) (P2).
- Options Education Layer (`/learn/options` tooltips) (P3).

## What's Implemented (this fork — 2026-05-08 / 2026-05-09 / 2026-05-10 / 2026-05-13 / 2026-02 Feb fork)

### 🟢 Alpha Sidecar Freeze Hardening (2026-05-14)

After the 18:31:35 silent-freeze incident (Alpha's sidecar wedged ~11
minutes, supervisor never noticed because the process was technically
RUNNING — blocked in a TLS syscall), three architectural fixes per
MC's hardening note. Camaro got the same patch earlier today.

**Fix 1 — `httpx.Client` phase-bound timeouts + no keep-alive**
(`sovereign/mc_client.py`):
- Old: `httpx.Client(timeout=5.0)` — single-number, kept connection
  alive across LB rotation, half-open socket trap.
- New: `httpx.Client(timeout=httpx.Timeout(connect=3, read=5, write=5,
  pool=2), limits=httpx.Limits(max_keepalive_connections=0,
  max_connections=4))` — every phase bounded, fresh TLS each tick.
  Cost negligible at ~1 req/min.

**Fix 2 — heartbeat decoupled from `tick()` into its own thread**
(`sovereign/sidecar.py`):
- Independent `_heartbeat_loop` daemon, 30s cadence, owns its own
  `MCClient` (separate connection pool). A hung contribution can no
  longer starve the heartbeat path — MC sees "alive but quiet"
  instead of "dead."
- `tick()` no longer calls `client.heartbeat()`.

**Fix 3 — dual watchdog** (in-process + external):
- In-process `_watchdog_loop` daemon: stamps `_last_tick_at` on
  successful tick; if stale > 120s, calls `os._exit(2)` → supervisor
  respawns.
- External `liveness_watcher.sh` (new supervisor program
  `alpha-liveness-watcher`): touches `/tmp/alpha_alive` each tick,
  bash loop `pkill -9`s the sidecar if file mtime > 120s. Catches
  GIL-deadlock / fork-in-thread classes the in-process watchdog
  can't observe.

**Worst-case time-to-respawn: ~150s (down from ~11 min / operator-paged).**

**Sidecar URL migration:** `MC_BASE_URL` flipped from
`multi-brain-backbone.preview.emergentagent.com` →
`mission.risedual.ai` in `/etc/supervisor/conf.d/alpha-sidecar.conf`.
The old preview URL had been 404-ing since 2026-05-14 11:54, dropping
contribution + heartbeat on the floor for 4+ hours.

**Files touched:**
- `sovereign/mc_client.py` — httpx config
- `sovereign/sidecar.py` — threading, watchdog, liveness file
- `sovereign/liveness_watcher.sh` (new) — external pkill watcher
- `/etc/supervisor/conf.d/alpha-sidecar.conf` — MC URL flip
- `/etc/supervisor/conf.d/alpha-liveness-watcher.conf` (new)
- `tests/test_alpha_sovereign_sidecar.py` — updated tick test, added
  `test_heartbeat_thread_publishes_independently` and
  `test_watchdog_exits_on_stale_tick`.

**Tests:** 68/68 sidecar tests green. Full suite 3,457/3,457 green.

### 🟢 SSE Hypothesis Stream — Pro Gate Awaitable Fix (Feb 2026)

Closed out the last P0 blocker from the previous fork: 2 failing SSE
consensus tests in `tests/test_hypothesis_stream.py`.

- **Root cause**: `routes.ai.get_optional_user` is async, but tests
  monkey-patch it to a sync lambda. The route did `await
  get_optional_user(request)` which raised `TypeError: object is not
  awaitable` on the sync stub, fell into the bare `except`, and
  defaulted `is_pro = (model == "alpha")`. For `model=consensus` that
  collapsed to False → the gate emitted a `premium_required` error
  event instead of running the 4 brains.
- **Fix**: detect coroutine returns via `inspect.isawaitable()` and
  only `await` when needed. Production callers stay async; tests can
  monkey-patch with either form.
- **Tests**: full `tests/test_hypothesis_stream.py` (10/10) and the
  wider 3,457-test suite are green.
- **Files touched**: `routes/hypothesis_stream.py` (pro-gate block
  only).

## What's Implemented (this fork — 2026-05-08 / 2026-05-09 / 2026-05-10 / 2026-05-13)

### 🔓 Doctrine V3 — Local Trade Authorization Removed (2026-05-13)

RISEDUAL is now a **headless brain**. Mission Control's Executor seat
owns all trade authorization. Broker keys live exclusively on the
Executor's host. Local gates have been retired:

| Lock | Status | Notes |
|---|---|---|
| `operator_trading_gate.py` | **Permanently OPEN** | File preserved for back-compat; `is_authorized()` always True |
| `BROKER_LIVE_ORDER_ENABLED` env flag | **Removed** | Stripped from `.env`, no longer read in `broker_wire.py`, removed from `ast_invariants` |
| Sovereign kit `assert_doctrine()` | **No-op** | Kept callable for back-compat |
| Sovereign kit `assert_safe_action()` | **Vocabulary-only** | Still validates action ∈ {BUY,SELL,HOLD}; live-flag check removed |
| Sovereign wire field `live_trading_enabled` | **Still serialized `False`** | Required by MC's API schema; hard-coded literal in body builder |

**Rationale:** with broker keys removed from RISEDUAL hosts and trade
execution confined to the MC Executor seat, every local block became
safety theater. The headless-brain pattern is the post-2026-05-13 doctrine.

**Files touched:**
- `services/operator_trading_gate.py` — rewritten to always-open
- `services/ml/broker_wire.py` — `_broker_live_order_flag()` returns True
- `services/code_evolution/ast_invariants.py` — `BROKER_LIVE_ORDER_ENABLED` pattern removed
- `sovereign/sidecar.py` — `_assert_doctrine()` + `assert_safe_action()` calls removed
- `sovereign/wild_adaptive_core_v2.py` — both functions converted to no-ops
- `tests/test_operator_trading_gate.py` — rewritten to verify V3 invariants
- `tests/test_broker_wire.py`, `tests/test_code_evolution_v0.py`, `tests/test_alpha_sovereign_sidecar.py` — updated assertions
- `.env` — `BROKER_LIVE_ORDER_ENABLED` line removed

**Verified:** full pytest suite **3382 passed, 0 failed**; alpha-sidecar
still ticking 200 OK every 60s.

### ⚠️ Open item: Kraken/Alpaca keys still in `/app/backend/.env`

User stated keys will be deleted "in the next few days". Until then,
removing local blocks creates a window of exposure. Recommend rotation
or deletion of `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` / `ALPACA_API_KEY`
before any code path that might submit broker orders is re-enabled.

---

### 🛰️ Alpha Sovereign Sidecar — Mission Control wiring (2026-05-13)

RISEDUAL acts as the **"alpha" brain** (trend-follower) reporting to a
separate Mission Control runtime (`multi-brain-backbone.preview.emergentagent.com`).

**Doctrine (three locks for one door — all enforced):**
1. `LIVE_TRADING_ENABLED = False` is hard-coded in `backend/sovereign/wild_adaptive_core_v2.py`.
2. Sidecar boot calls `assert_doctrine()` and refuses to start if violated.
3. Client serializes `live_trading_enabled: false` always — there's no parameter to flip it.

**Files:**
- `backend/sovereign/wild_adaptive_core_v2.py` — doctrine constants + `assert_doctrine()`
- `backend/sovereign/local_state.py` — atomic JSON state (weights, lr, mode, outcomes)
- `backend/sovereign/mc_client.py` — verified MC HTTP contract:
    - Auth: `X-Runtime-Token: <ALPHA_INGEST_TOKEN>` (NOT Bearer)
    - `POST /api/heartbeat-ping/alpha`
    - `POST /api/runtime-discussion/sovereign/contribution?runtime=alpha`
- `backend/sovereign/sidecar.py` — 60s tick loop, contribution + heartbeat
- `backend/sovereign/smoke_test.py` — 8/8 offline doctrine checks
- `backend/sovereign/bootstrap_alpha.py` — one-shot weight seeder
- `data/sovereign/alpha/state.json` — brain-private state (chmod 600 env beside it)
- `/etc/supervisor/conf.d/alpha-sidecar.conf` — supervisor program registration
- `backend/tests/test_alpha_sovereign_sidecar.py` — 44 pytest cases, green

**Initial weights (only seeded if state.json absent):**
`trend +0.85, macd +0.65, rsi -0.25, learning_rate 0.06, mode=DTD`

**Verified first run:** contribution → 200 OK (`posted_as=executor`, `seat_epoch=91`);
heartbeat → 200 OK. No 422s, no 401s. Sidecar runs under supervisor with
`autorestart=true`. Stance endpoint (v2) intentionally skipped for Phase 1.

**Operator-only — stays separate from `risedual_monorepo_client.py`** (which is a
different sidecar to the RISEDUAL monorepo, not Mission Control). Two independent
sidecars, two independent kill-switches.

---



### 🧠 Shelly Memory — Single Source of Truth for Durable Memory (2026-05-10)

Per operator audit (2026-05-10): "the data you present should fall under Shelly's purview."

**Doctrine** (CI-pinned by 28 tests):
1. **Mongo durable, Chroma disposable** — Mongo write happens FIRST; Chroma upsert is wrapped in `try/except` and a Chroma failure logs a warning but never blocks the durable record.
2. Every memory unit, at write time, automatically receives:
   - `id` (UUID4)
   - `event_date` (`YYYY-MM-DD`, normalized via `_normalize_event_date`)
   - `event_date_ordinal` (int days since epoch — for Chroma `$gte` range filters)
   - `regime_status` (`"legacy"` if past, `"active"` if today)
   - `regime_label` (= `event_date`)
   - `created_at` (full ISO UTC matching audit_trail format)
   - `embedding_version` (`"minilm-l6-v2-default"`)
3. **`_normalize_event_date` is the only path** to set `event_date`. Three guarantees:
   - **Format uniformity** — every output is exactly 10 chars `YYYY-MM-DD`
   - **Timezone uniformity** — everything coerces to UTC
   - **Fail loud** — bad input → `ValueError` → HTTP 422 (cannot silently persist malformed dates)
4. **Toxic-spike landmines closed** at three independent layers:
   - Boundary normalize (every input shape collapses to YYYY-MM-DD UTC)
   - Fail-loud-on-malformed (no silent acceptance of "2022-05-09T14:30:00+00:00" mixing with date-only filters)
   - Numeric `event_date_ordinal` for Chroma range filters (Chroma v1.x silently rejects `$gte` on string fields)

**Module surface** (`services/shelly_memory.py`):
- `_normalize_event_date(raw)` — boundary normalizer (date / datetime / naive / aware / "Z" / full-ISO / `None` / `""`).
- `_stamp_regime(metadata)` — applies all 4 mandatory metadata fields.
- `remember(db, *, text, metadata, memory_id)` — Mongo-durable + Chroma-best-effort write. Returns the persisted document.
- `recall(db, *, min_event_date, max_event_date, include_legacy, limit)` — Mongo-only durable read. Filters on `event_date_ordinal` (NOT the string field). Boundary-normalizes filter inputs.
- `count_by_regime(db)` — operator audit: active vs legacy split.

**API** (`services/shelly_memory_api.py`, owner-only):
- `POST /api/admin/shelly-memory/remember` — write a memory (422 on bad date)
- `GET /api/admin/shelly-memory/recall?min_event_date=…&include_legacy=…&limit=…` — durable recall
- `GET /api/admin/shelly-memory/status` — collection + embedding_version + active/legacy/total counts

**Live validation (2026-05-10)**:
- 595 routes (was 592, +3 new admin endpoints)
- Every smoke-test case from the audit reproduces exactly:
  - `AAPL event_date=2024-03-15` → `regime=legacy`, `ordinal=738960`
  - `TSLA event_date=2022-05-09T14:30:00+00:00` → stored as `2022-05-09 regime=legacy` (full-ISO collapsed at boundary)
  - `NVDA` today → `regime=active`
  - `"not-a-date"` → **HTTP 422 "unparseable event_date"** (fail loud)
  - `recall(min_event_date=2023-01-01)` → exactly 2 results (NVDA + AAPL), TSLA correctly excluded
- Test count: **3270 / 3270 passing** (3242 → 3270, +28 new shelly_memory tests)

**Authority-boundary invariants preserved**:
- Owner-only at the API layer.
- The `remember()` write path raises `ValueError` for malformed input — never silently persists.
- Chroma failure path logs but doesn't block durable Mongo write (defense in depth).
- Smoke memories from audit reproduction were cleaned before commit.

### 🧠 Shelly Doctrine v2 — Perception + Malformed Quarantine (2026-05-12)

Per operator directive (2026-05-12): *"Shelly is the scribe and MongoDB is the source of truth. Perception is also Shelly. Any information sourced must be labeled according to MongoDB standards. If malformed it still must be labeled legacy, date, time and ID. Place malformed in a file of its own, numbered by the number of documents in file. ChromaDB if used is temporary and can be wiped if necessary."*

**Doctrine** (CI-pinned by 29 new tests, total 150 in shelly perimeter):
1. **Shelly is the scribe + perception layer**. Every piece of inbound information from any lane (chat, market feed, agent, scraper) must flow through `perceive()`. There is no other approved entry-point for sourced info.
2. **MongoDB is canonical**. Chroma is disposable.
3. **`perceive()` never raises**. Any failure routes the payload to the malformed-quarantine bin.
4. **Malformed docs still get MongoDB-standard labels** — `legacy_id` (UUID4), `legacy_date` (YYYY-MM-DD UTC, salvaged from payload `event_date`/`date`/`timestamp`/`ts`/`created_at` if possible, else today), `legacy_time` (full ISO UTC), `created_at`, `embedding_version`, `source`, `error`, `doc_number` (sequential), `raw_payload` (preserved verbatim).
5. **`doc_number` is strictly sequential** within `shelly_legacy_malformed`, atomic across concurrent writes (Mongo `find_one_and_update` + `$inc` upsert on `shelly_counters` collection). Degraded fallback to `count_documents+1` if the counter mechanism itself errors.

**New module** (`services/shelly_perception.py`, ~280 lines):
- `perceive(db, *, payload, source, text=None, metadata=None)` — perception entry. Returns `{"ok": True, "lane": "memory"|"malformed", "doc": ...}`. Auto-inherits `event_date`/`symbol`/`lane` from a dict payload into metadata. Auto-injects `source` label. Coerces non-string payloads to JSON via `_coerce_payload_to_text` (precedence: payload["text"] → str(payload) → json.dumps → quarantine).
- `quarantine_malformed(db, *, raw, source, error)` — last-resort writer. Always succeeds (Mongo `_id` stripped on return).
- `list_malformed(db, *, limit=50, min_doc_number=None)` — operator audit, sorted by `doc_number` ascending (arrival order).
- `_next_doc_number(db, key)` — atomic counter via `shelly_counters` collection.
- `_stamp_malformed(raw, source, error, doc_number)` — pure label stamper.

**Module split**: Perception logic lives in `shelly_perception.py` (~280 lines) to keep `shelly_memory.py` under the 600-line core-governance ceiling. `shelly_memory.py` re-exports `perceive`, `quarantine_malformed`, `list_malformed` so the public API of the doctrine package is unchanged.

**API** (`services/shelly_memory_api.py`, owner-only, 2 new endpoints):
- `POST /api/admin/shelly-memory/perceive` — `{payload, source, text?, metadata?}` → envelope. Never 5xx's.
- `GET /api/admin/shelly-memory/malformed?limit=&min_doc_number=` — quarantine bin audit, arrival order.
- `GET /api/admin/shelly-memory/status` — now also includes `malformed_collection` name + `malformed` bucket count.

**Constants added to `shelly_memory.py`**:
- `MALFORMED_COLLECTION = "shelly_legacy_malformed"`
- `COUNTERS_COLLECTION = "shelly_counters"`
- `count_by_regime(db)` extended to surface `malformed` bucket count.

**Test count**: **3287 / 3287 passing** (was 3270; +17 net) — full backend regression clean.

**Live validation (2026-05-12)**:
- 597 routes (was 595, +2 new admin endpoints)
- Doctrine invariants pinned by tests:
  - Happy path: `{text, event_date}` payload → `lane="memory"`, full 6-stamp doctrine + source label.
  - Malformed `event_date="not-a-date"` → `lane="malformed"`, all 9 mandatory labels present, `doc_number=1`, `raw_payload` verbatim, `error="stamp_error: ..."`.
  - Empty/None payload → `lane="malformed"`, `error="empty_or_unscribable_payload"`.
  - Concurrent quarantines (`asyncio.gather` × 10) → strictly sequential `doc_number=[1..10]`, no collisions.
  - `legacy_date` salvaged from payload `timestamp` field when `event_date` is unparseable.
  - Mongo failure on durable write → routed to malformed (operator never loses the perception silently).
  - `count_by_regime` exposes `{active, legacy, total, malformed}`.
  - `list_malformed(min_doc_number=3)` returns rows in arrival order, properly filtered.

### 🔌 Doctrine v2 Wiring — chat_memory + market_memory through perceive() (2026-05-12)

Per operator directive: *"Wire chat_memory_service and market_memory_service ingest paths to flow through perceive() so all sourced information is doctrine-labeled."*

**chat_memory_service.save_memory** — now writes BOTH:
1. **Chat-projection** to `chat_memories` (existing UI contract preserved — toggle, list, delete keep working) with `apply_doctrine_stamps`.
2. **Canonical Shelly record** via `perceive(source="chat", metadata={user_id, category, source_session, chat_memory_id})` → lands in `shelly_memories` with full doctrine.

The Shelly tee is wrapped in try/except — a Shelly outage cannot break the user-facing chat write.

**market_memory_service.save_regime** — three changes:
1. **Date pipeline migration**: replaced `to_iso_date(regime.get("date"))` with `_normalize_event_date()` (Shelly's boundary normalizer). Now full-ISO inputs like `"2024-03-15T23:30:00-05:00"` correctly collapse to UTC date `"2024-03-16"`. Garbage dates raise ValueError, which is caught and falls back to today (live-feed contract preserved — `save_regime` never blocks on a malformed date).
2. **`market_memory_log` doctrine stamps**: log rows now carry `apply_doctrine_stamps` labels (`id` / `event_date` / `event_date_ordinal` / `regime_status` / `regime_label` / `created_at` / `embedding_version` / `metadata.source="market_feed"`). Closes the labeling parity gap — every collection Shelly touches speaks the same vocabulary.
3. **Canonical perception tee**: every regime save also produces a `perceive(source="market_feed")` record in `shelly_memories` with `metadata={event_date, symbol, outcome, regime_doc_id, prediction_id}`. Wrapped in try/except.

**`_make_id` fallback path** — still uses `to_iso_date` for hash-key stability (the legacy v1 pre-prediction-id key cannot change without invalidating the entire ChromaDB dedupe contract).

**Tests** (9 new, in `tests/test_shelly_doctrine_v2_wiring.py`):
- chat: dual-write to both collections; canonical record carries `source="chat"` + `chat_memory_id`; doctrine stamps on canonical record; resilient to perceive() failure.
- market: tz-aware ISO timestamps coerce to UTC date end-to-end; `market_memory_log` carries full doctrine stamps; perception tee creates canonical record; garbage `date` falls back to today (logged warning) with Shelly tee still recording the event; perceive() failure does NOT block the primary ChromaDB + log writes; naive datetimes assume UTC.

**Backend regression**: **3296 / 3296 passing** (was 3287; +9 wiring tests). 597 routes, no errors. Backend hot-reloaded successfully.

### 🌐 Doctrine v2 Full Perception Coverage — agent_activity + news ingest (2026-05-12)

Per operator directive: *"Wire remaining ingest paths through perceive() — agent_activity_service, news ingestion, scrapers — for full perception coverage."*

Three additional ingest paths now flow through Shelly perception:

1. **`agent_activity_service.log_event`** — every entry in the agent narrative feed tees through `perceive(source="agent_activity")` with `title — detail` as scribe text. Metadata carries `agent_event_id`, `agent_event_type`, `severity`, `symbol`. Wrapped — never blocks the activity write.

2. **`news_shock_feeder._persist_catalyst_events`** (Benzinga) — every persisted catalyst article also lands as a canonical perception record. `source="news.benzinga"`, scribe text = headline, metadata = `{event_id, event_date (UTC date from Shelly normalizer), symbol, url, headline}`. Bulk-teed inside a single try/except so per-article cost stays flat.

3. **`av_sentiment_feeder._persist_av_catalyst_events`** (Alpha Vantage) — same as Benzinga but with `source="news.alpha_vantage"` and `sentiment_score` (signed, clamped to [-1, 1]) carried in metadata so vector queries can later filter on signed sentiment.

**Key invariants pinned**:
- All three tees are best-effort: a Shelly outage NEVER breaks the primary catalyst/activity write.
- Articles rejected at the primary boundary (no URL, no parseable timestamp, garbage sentiment score) do NOT produce phantom perception records.
- Event timestamps coerce to UTC `YYYY-MM-DD` via `_normalize_event_date`, so a Benzinga `"2024-03-15T14:30:00Z"` and an AV `"20240315T150000"` on the same day collapse to the same `event_date` key in Shelly.

**Tests** (8 new, in `tests/test_shelly_perception_coverage.py`):
- agent_activity: dual-write to `agent_activity` + `shelly_memories`; title-only composition when detail missing; resilient to perceive() failure.
- Benzinga: 2-article batch → 2 catalyst rows + 2 Shelly rows; UTC event_date collapse; resilient.
- AV: signed sentiment carried in metadata; garbage-score article skipped end-to-end; resilient.

**Backend regression**: **3304 / 3304 passing** (was 3296; +8 coverage tests). 597 routes, no errors. Backend hot-reloaded.

**Perception coverage now spans 5 lanes** (`source` values):
- `"chat"` — chat memory writes
- `"market_feed"` — market regime saves
- `"agent_activity"` — agent narrative feed
- `"news.benzinga"` — Benzinga catalyst articles
- `"news.alpha_vantage"` — Alpha Vantage news + sentiment

### 🔓 Shelly Full Functionality Unlock (2026-05-11)

Per operator directive: *"Let me make sure Shelly is fully functional. Get rid of any other block that doesn't allow her to be fully there."*

**Audit found three remaining gates** — all env flags defaulting to `false` and absent from `backend/.env`. With operator approval (response `a` to the audit), all three flipped to `true`:

| Flag | Effect when ON | Loop Lane |
|------|---------------|-----------|
| `LEARNING_CORE_CONSUME_ENABLED` | Phase 3 consumer applies bounded confidence delta (±0.10) + risk dampening (×0.85 on pretell, floor 0.50) to prediction payloads | Influence |
| `LEARNING_CORE_PERSISTENCE_ENABLED` | Regime cluster state durably written to Mongo (no longer RAM-only) | Persistence |
| `LEARNING_CORE_REHYDRATE_ON_STARTUP` | Backend boots with replayed resolved memories from Mongo | Rehydrate |

**Closed-loop state** — Shelly perceives → scribes → ingests → persists → rehydrates → influences (all bounded by the doctrine's hard caps). Trading remains **hard-stopped**: `OPERATOR_TRADING_AUTHORIZATION_ENABLED=false`. Influence is real, action is not.

**Backend restart**: clean. 597 routes, no errors. Rehydrate ran (0 memories loaded — expected since persistence was OFF until this restart; Shelly starts accumulating from now).

**CI invariants pinned** (`tests/test_shelly_full_functionality_invariants.py`, 2 new):
- All six Shelly flags must be `true` in `backend/.env` — a future silent flip-off fails CI and pinpoints the broken lane.
- `OPERATOR_TRADING_AUTHORIZATION_ENABLED` must stay `false` — the golden rule is now an explicit CI guard.

**Backend regression**: **3304 / 3304 passing** with all flags ACTIVE. No mutations to thresholds, no execution authority granted. Operator Trading Gate still LOCKED.

### 🗂️ Malformed Quarantine Operator Panel (2026-05-11)

Per operator directive: *"Malformed documents, yes"* — closing the operator-side loop on the Shelly Doctrine v2 quarantine bin.

**Backend** — new endpoint:
- `POST /api/admin/shelly-memory/malformed/{doc_number}/promote` — accepts `{corrected_payload?, use_raw?, source?, metadata?}`. Reads the malformed row by doc_number, re-perceives the (corrected or original) payload, and on memory-lane success stamps the malformed row with `promoted_to_memory_id` + `promoted_at`.

**Doctrine compliance**:
- Original malformed rows are NEVER deleted. The numbered audit trail is permanent.
- A still-malformed re-perception creates a NEW malformed doc with its own doc_number (append-only).
- 404 on unknown doc_number; 422 on malformed source label (Pydantic).

**Frontend** — new admin panel `MalformedQuarantine.jsx`:
- Wired into `AdminPanel.jsx` under Insights → "Shelly Quarantine" tab.
- Live count badges: pending vs promoted.
- Filter: `min_doc_number` cursor for pagination of large bins.
- Expandable rows showing all 9 doctrine stamps (legacy_id / legacy_date / legacy_time / created_at / embedding_version / source / error / doc_number / raw_payload).
- "Promote to memory" modal: pre-fills source + JSON-formatted raw_payload in an editable textarea; submits to `/promote`; refreshes list on success. Falls back to plain-text submit if JSON parse fails.
- Visual states: amber border + "pending" tag for fresh quarantines; emerald border + "✓ promoted" badge + linked memory_id once rescued.

**Tests** (4 new in `test_shelly_malformed_promote.py`):
- Corrected payload → memory lane → stamps `promoted_to_memory_id` + `promoted_at`.
- Source label inherits from malformed row when not overridden.
- `use_raw=True` on still-bad payload → new malformed doc; original row untouched.
- Unknown `doc_number` → 404-equivalent error.

**Backend regression**: **3310 / 3310 passing** (was 3304; +6). Frontend lint clean. Doctrine intact — quarantine bin is now recoverable AND auditable.

### 📊 Counterfactual P&L Tracker (2026-05-10)

Read-only "what would have traded" view layered on top of the synthetic ADL stream the Operator Trading Gate writes.

**Module**: `services/counterfactual_pnl.py`
- `score_one(db, row)` — single-receipt scorer. Resolves direction from `extras.intended_action`, notional from `qty*price`/`notional_usd`/default $1000, fetches close-to-close move via `price_provider.get_daily_history`. LONG profits when price rises; SHORT profits when it falls.
- `score_window(db, start, end)` — aggregates across a window, returns `{total_receipts, scored_receipts, unscored_receipts, simulated_pnl_usd, by_symbol[]}`.
- `score_yesterday(db)` and `score_last_n_days(db, days)` — convenience wrappers with `window_label`.
- `persist_daily_summary(db)` — idempotent upsert into `counterfactual_pnl_daily` (cron-safe).

**Endpoints** (extension to existing trading-gate router):
- `GET /api/admin/trading-gate/counterfactual-pnl?days=1|7|30` — on-demand compute
- `POST /api/admin/trading-gate/counterfactual-pnl/persist` — idempotent daily upsert

**Frontend** (extends `TradingGate.jsx`): new "What would have traded" card with 1d/7d/30d toggle, big colored total ($+/-), per-symbol breakdown showing `Nlong · Mshort` and signed P&L. Empty state when no scoreable receipts.

**Tests** (`tests/test_counterfactual_pnl.py`, **10 tests**): direction resolution, notional fallback, LONG/SHORT scoring with mocked moves, unscoreable rows (HOLD/missing prices), per-symbol aggregation, idempotent persist.

**Live validation (2026-05-10)**:
- 592 routes (was 590, +2 new endpoints)
- All 3 P&L endpoints respond cleanly; empty windows return zeroed structure
- Test count: **3242 / 3242 passing**

### 🔒 Operator Trading Gate — THE ONLY RULE (2026-05-10)

Per operator order:
> "There is only one rule, no trades until I say so. No paper trade or live trades until I okay it. That's the only rule."

**Single source of truth**: `services/operator_trading_gate.py`. Default state: **DISABLED**. Owner-only flip via API.

**Module surface**:
- `services/operator_trading_gate.py` — `is_authorized(db)`, `set_authorized(db, ...)`, `record_paused_synthetic(...)`, `gate_or_synthetic(...)`. State persists in Mongo `operator_trading_gate_state` (singleton); every flip recorded to `operator_trading_gate_history` (audit trail).
- `services/operator_trading_gate_api.py` — owner-only `GET /status`, `POST /toggle`, `GET /history`, `GET /synthetic-summary`.
- Test-mode bypass: when `PYTEST_CURRENT_TEST` is set OR `_TEST_MODE_FORCE_AUTHORIZED=True`, gate returns True. The gate's own tests flip `_disable_test_mode_bypass(True)` in an autouse fixture so default-disabled behaviour can still be asserted.

**Wired into every trade-insert chokepoint**:
- `paper_trading_service.py::execute_signal` (equity paper)
- `crypto_paper_trader.py` (crypto paper, immediately before `crypto_paper_trades.insert_one`)
- `paper_options_service.py` (options paper)
- `ml_paper_trader.py` (ML/sovereign paper)
- `services/ml/broker_wire.py` — added as **Gate 0** (operator authorization), ANDed with the 4 existing gates. Now 5-gate live-broker authorization.

**Synthetic counterfactual receipts**: when blocked, every chokepoint writes an ADL row with `decision=NO_TRADE`, `reason=paused_by_operator`, `extras.synthetic=True`, `extras.intended_action=<original direction>`, `extras.blocker=operator_trading_gate`. **MLs keep learning from the counterfactual stream.**

**Frontend** (`components/admin/TradingGate.jsx`): new "Trading Gate" tab in Operations group (top-of-list, sibling to Health). Big visual lock card (red when paused, green when authorized), confirmation modal with optional audit-log note, toggle history with operator + timestamp, synthetic-receipt list with intended action + lane + symbol + confidence.

**Doctrine relaxations** (operator order): ML-isolation gates removed/relaxed:
1. **Tier-3 shadow firewall** — `tests/test_shadow_tier3_isolation.py` retired (skipped at module level). Shadow code may now read AND write live trade tables. The `tier3_firewall=True` row tag remains as an analytics provenance marker (no behavioural meaning).
2. **`adversarial_enforcer.py` doctrine** — comments updated: "HOLD is not auto-promoted" reframed as a sanity rail (not an inter-ML communication block). Behaviour unchanged.
3. **`council_risk_modulator.py` table** — "(cannot promote)" changed to "(advisory only)". Behaviour unchanged.
4. **`risedual_learning_core.py` invariants** — "HOLD/UNKNOWN cannot receive positive boost" reframed as a training rail. Behaviour unchanged.

**Production validation (2026-05-10)**:
- 590 routes (was 586, +4 new admin/trading-gate endpoints).
- Live API: `GET /status` returns `enabled: false` on first boot (bootstrapped from env default + history row).
- Live API: `POST /toggle` flips state and writes history; `GET /history` returns audit trail with operator email + timestamp.
- Live: `gate_or_synthetic` blocks the trade AND writes the synthetic ADL row (`decision=NO_TRADE`, `extras.intended_action=PAUSED_BY_OPERATOR:BUY`).
- Test count: **3232 / 3232 passing** (+11 new operator_trading_gate tests, +3 skipped tier3 tests).

**Authority-boundary invariants preserved**:
- Default OFF on bootstrap (env hint, DB authoritative thereafter).
- Owner-only at the API layer.
- Fail-closed on any DB error.
- BROKER_LIVE_ORDER_ENABLED stays as Gate 2 (defense in depth).
- The gate cannot be modified via the Code Evolution gate (still BLOCKED_OPERATOR_ONLY for any patch touching `services/operator_trading_gate*`).

### Alpha Python Knowledge Base v0 (2026-05-10)

**Read-only Python corpus the runtime consults via `/py` chat prefix.** Sourced live from `docs.python.org`. Firewalled from execution + the Code Evolution gate.

**Module layout** (`services/alpha_knowledge/`):
- `schemas.py` — Pydantic models. `KnowledgeChunk` carries `excluded_from_code_gate_inputs=True` (a future patch-risk classifier MUST honour this flag).
- `seed_manifest.py` — 82-URL static manifest covering full Python language reference (10), library reference essentials (57), tutorial chapters (8), and HOWTOs (7).
- `chunker.py` — pure HTML→text pipeline (`_TextExtractor` strips nav/script/style/footer, emits `\n\n` on block-level closings) → paragraph-aware chunker (target 1200 chars, 150-char overlap).
- `ingest.py` — async fetch+chunk+upsert. 4-way concurrency, 12s per-URL timeout. Idempotent on `chunk_id` (sha256 of `source_url::chunk_index`). Creates Mongo `$text` index + per-category index on first run. Single fetch failure never aborts the run.
- `retrieval.py` — `$text`-index search ordered by `textScore`. Caps at 20 results. Returns `{results, total_corpus_size}`. Optional category filter.
- `chat_hook.py` — `maybe_expand_with_python_kb(db, message, memory_context)` detects `/py <question>`, retrieves top-5 chunks, prepends a `Python knowledge (Alpha KB):` block to memory_context. Strips prefix even on retrieval failure so the LLM still gets the question.
- `api.py` — owner-only:
  - `GET /api/admin/alpha-knowledge/status` → corpus size, per-category breakdown, last_ingest_at
  - `GET /api/admin/alpha-knowledge/manifest` → URL list + per-category counts
  - `POST /api/admin/alpha-knowledge/ingest` → run full or filtered ingest
  - `GET /api/admin/alpha-knowledge/retrieve?q=…` → ranked chunks

**Chat wiring** (`routes/ai.py`): `chat_endpoint` calls `maybe_expand_with_python_kb` BEFORE the LLM. When triggered, the response carries an `alpha_knowledge` block: `{consulted, results_count, total_corpus_size, sources: [{title, url, category, score}]}`. Never raises — failures are absorbed and chat falls back to vanilla.

**Frontend** (`components/admin/AlphaKnowledgePanel.jsx`): new "Alpha KB" tab in AdminPanel Insights.
- 4 stat tiles (total chunks, sources, manifest URL count, schema version).
- Per-category coverage strip (color-coded pills).
- "Run full ingest" button with live summary (sources fetched / failed / chunks written).
- Search test panel — paste a query, see top-8 ranked chunks with `score · #idx` and category pills.
- Footer reminder: chat prefix is `/py <question>`.

**Production validation (2026-05-10)**:
- 82 URLs / 0 failures / **3,734 chunks** ingested live (language_ref 448, library_ref 2791, tutorial 212, howto 283).
- `GET /retrieve?q=asyncio+task+gather` → 4.16 textScore on the canonical asyncio page.
- `POST /chat` with `/py How do I use functools.lru_cache for memoization?` → GPT-5.2 returned a complete answer; response carried `alpha_knowledge.sources` listing 5 functools chunks (scores 4.0 → 2.66).
- 583 → **586 routes** (4 new admin endpoints + the chat hook).
- Test count: **3224 / 3224 passing** (3199 → 3224, +25 new alpha_knowledge_v0 tests).

**Doctrine firewalls** (`tests/test_alpha_knowledge_v0.py`, **25 tests**):
1. AST-based scan: every coach module fails CI if it imports `services.code_evolution`, broker, or execution paths.
2. AST-based scan: every module fails CI on `exec()`, `eval()`, builtin `compile()`, `os.system`, `subprocess.{run,Popen,call,check_call,check_output}`. (Substring-naive checks would false-positive on docs URLs and `re.compile` — AST chain resolution avoids both.)
3. Bidirectional isolation: `code_evolution` package fails CI if any file references "alpha_knowledge".
4. `KnowledgeChunk.excluded_from_code_gate_inputs` defaults `True` — pinned by schema-construction test.
5. Chat hook: prefix detection, prefix stripping, exception-swallowing on retrieval failure.
6. Retrieval: empty query short-circuits; results carry score from `$meta:"textScore"`.
7. Chunker: HTML→text strips nav/script, emits paragraph breaks; chunks stay above 200 chars; chunk_id deterministic.
8. All 4 endpoints invoke `_require_owner(request)` (static check).

**Authority-boundary invariants preserved**:
- KB is read-only at the data path (no API mutation surface beyond ingest).
- KB is firewalled from `code_evolution` (bidirectional disjoint).
- KB chunks tagged `excluded_from_code_gate_inputs: True`.
- Chat consult is opt-in (`/py` prefix) — not auto-on.
- Operator-only at the API layer.

### Python Coach v0 — Operator Learning Surface (2026-05-10)

Operator-facing Python learning module. Turns a plain-English goal into a structured lesson plan via the existing `AIService` (Emergent Universal Key) and statically reviews pasted code via Python's `ast`. **Never executes user code.**

**Module layout** (`services/python_coach/`):
- `schemas.py` — Pydantic models for the API (`LessonPlan`, `LessonStep`, `CodeReview`, `CodeFinding`).
- `static_review.py` — pure AST reviewer. Detects SYNTAX errors, NO_FUNCTIONS, PRINT_ONLY, MISSING_DOCSTRINGS, NO_MAIN_GUARD, MIXED_INDENT, BARE_EXCEPT_PASS. Goal-aware drill suggester.
- `lesson_planner.py` — async `AIService` caller. Strict-JSON prompt + tolerant parser (strips fences, falls back to a stub on garbage). `generate_lesson_plan(goal)` and `generate_deep_feedback(code, goal)` — both NEVER raise.
- `api.py` — owner-only FastAPI router: `POST /plan`, `POST /review` (with optional `deep=True`), `GET /example`.

**Frontend**: `components/admin/PythonCoach.jsx` — new "Python Coach" tab in AdminPanel Insights. Goal textarea, code textarea, Stat tiles (Parses / Lines / Functions / Docstrings), Findings list with severity pills, optional LLM Deep feedback panel, "Load example" pulls the artifact's "fetch stock prices with retry" starter.

**Doctrine firewalls** (`tests/test_python_coach_v0.py`, **19 tests**):
- AST static review (6 unit tests).
- Forbidden imports: each coach module fails CI if it imports `services.code_evolution`, `services.broker`, `services.execution`, `subprocess`, `shlex`.
- Forbidden calls: each module fails CI if it contains `exec(`, `eval(`, `compile(`, `os.system(`, `subprocess.`.
- Bidirectional isolation: code_evolution may not reference "python_coach".
- Stub plan is valid `LessonPlan` even when LLM unreachable.
- All 3 endpoints invoke `_require_owner(request)` (static check).

**Live validation (2026-05-10)**:
- `POST /plan` for "learn list comprehensions" returned `generated_by: alpha-python-coach-llm` with 5 real drills + 4 pitfalls.
- `POST /review` with broken code → `parses=False`, single SYNTAX finding.
- `POST /review` with well-formed code → 0 findings.
- `GET /example` → starter snippet.
- 3180 → **3199 passing** (+19 new tests, no regressions). 583 routes.

### Code Evolution v0 — Self-Review Layer (2026-05-10)

RISEDUAL's code-review gate. Operator pastes a patch; the gate audits, classifies risk, recommends tests, and writes a Mongo receipt. **AI may not promote code, ever.**

**Doctrine** (literally enforced by tests):
```
def may_auto_promote(*args, **kwargs) -> bool:
    return False
```
Static source-grep + runtime invariants forbid any flip to `return True`.

**Module layout** (`services/code_evolution/`):
- `schemas.py` — dataclasses (internal) + Pydantic API models. ``PatchStatus`` literal: PROPOSED / BLOCKED_FORBIDDEN_PATTERN / BLOCKED_OPERATOR_ONLY / REQUIRES_DUAL_OPERATOR_SIGNATURE / REQUIRES_OPERATOR_SIGNATURE / SIGNED_AWAITING_OPS / REJECTED.
- `ast_invariants.py` — protected-path blocker (the gate cannot mutate itself), execution-path categoriser (CRITICAL paths require dual sig), risk-path categoriser (HIGH paths require single sig), forbidden-pattern regex (BROKER_LIVE_ORDER_ENABLED flips, COUNCIL_RISK_MODULATOR_ENABLED flips, delete_many, drop_collection, HOLD→BUY/SELL/LONG/SHORT mutations), AST-walk for destructive Mongo calls + direct inserts into protected collections.
- `code_auditor.py` — risk classifier (LOW/MEDIUM/HIGH/CRITICAL) + required-test recommender. Tests are recommended, never auto-run.
- `promotion_policy.py` — pure module (no Mongo, no FastAPI, no service imports). `may_auto_promote()`, `required_signatures_for(risk_level)`, `evaluate(invariants, audit) → PromotionPolicyResult`.
- `api.py` — FastAPI router, owner-only:
  - `POST /api/admin/code-evolution/evaluate` — full pipeline + Mongo upsert.
  - `POST /api/admin/code-evolution/countersign` — operator approve/reject. Each unique operator may sign once. BLOCKED_OPERATOR_ONLY can never be promoted via API (409).
  - `GET /api/admin/code-evolution/receipts` — paginated list (≤100), sorted by updated_at desc.
  - `GET /api/admin/code-evolution/receipts/{patch_id}` — single receipt.

**Mongo collection**: `code_evolution_receipts` (idempotent upsert by `patch_id`). Diff text is hashed (SHA-256) and never echoed back — only `diff_sha256` and `diff_size_bytes` persist.

**Hard invariants enforced by `tests/test_code_evolution_v0.py` (22 tests)**:
1. `may_auto_promote()` literally returns False (runtime + source check).
2. Patch touching `services/code_evolution/` → `BLOCKED_OPERATOR_ONLY`.
3. Patch with `BROKER_LIVE_ORDER_ENABLED=true` / `COUNCIL_RISK_MODULATOR_ENABLED=true` / HOLD→BUY → `BLOCKED_FORBIDDEN_PATTERN`.
4. AST walk catches `delete_many`, `drop_collection`, direct `paper_trades.insert_one` regardless of regex.
5. CRITICAL → 2 sigs, HIGH → 1, MEDIUM/LOW → 0.
6. Every promotion result reports `auto_promote=False`.
7. v0 ships ZERO subprocess imports — the test runner is explicitly future work.
8. `promotion_policy.py` stays pure (no fastapi / motor / routes / alpha_decision_log imports).

**Production validation (2026-05-10)**:
- Wired into `route_registry.py` — backend went from 575 → **579 routes**.
- Live smoke against 3 patch shapes:
  - Protected path → status `BLOCKED_OPERATOR_ONLY`, countersign returned **409** "cannot be promoted via the API".
  - Forbidden pattern → status `BLOCKED_FORBIDDEN_PATTERN`, sigs 0/2.
  - HIGH risk → 1 owner countersign flipped status to `SIGNED_AWAITING_OPS`, double-sign by same operator returned **409**.
- Receipts list endpoint sorts correctly, strips `_id`, surfaces sigs/required ratio.
- Test count: **3180 / 3180 passing** (3158 → 3180, +22 new code_evolution_v0 tests).

**Authority-boundary invariants preserved**:
- `BROKER_LIVE_ORDER_ENABLED=false`.
- No retrain artifact writes triggered.
- No threshold lowering anywhere.
- `trading_bot_service.py` Step 4E remains paused per operator order.
- Code Evolution gate cannot rewrite itself.

### Alpha Monorepo Sidecar (2026-05-09)

Wired the runtime to the RISEDUAL monorepo as a fire-and-forget observation sidecar. **NEVER blocks, NEVER raises** — local writes remain authoritative.

**Client (`services/risedual_monorepo_client.py`)**:
- `_enabled()` gate: requires `MONOREPO_BASE_URL`, `MONOREPO_INGEST_TOKEN`, `RUNTIME_NAME`. Honoured kill-switch `MONOREPO_SIDECAR_ENABLED=false`.
- `_post(path, body)`: 5s timeout via `httpx.AsyncClient`. Adds `runtime` to body, `X-Runtime-Token` header. Returns `{"ok": False, "error": ...}` on any failure — never raises.
- `fire_and_forget(coro)`: schedules on the running loop, drops cleanly when no loop is active, never raises.
- Public emits: `emit_receipt`, `emit_memory_label`, `register_calibrator`, `register_artifact`, `heartbeat`.
- Higher-level helper: `mirror_calibration_artifact(...)` — sha-hashes the joblib + fires both calibrator + artifact registration as one task.
- `aclose()` for shutdown cleanup.

**Wiring (single chokepoints)**:
- `services/alpha_decision_log.py::record_decision` — after the `insert_one` succeeds, schedules `emit_receipt(action="alpha_decision_log", intent=..., executed=False)`. Covers ALL six ADL paths automatically (crypto / day-trade / options / blocked-gate / equity paper / approved).
- `routes/admin_bulk_replay.py` — after labeling, emits one `emit_memory_label(...)` per upload (label = `quarantine` if any, else `review` if toxic, else `safe`).
- `services/calibration_layer.py::fit_and_persist` — after a successful joblib write, calls `mirror_calibration_artifact(...)`.

**Server lifecycle (`server.py`)**:
- Startup: schedules `_monorepo_register_artifacts_at_startup()` (one-shot register of the active calibrator) and `_monorepo_heartbeat_loop()` (60s cadence). Both are `asyncio.create_task` — they NEVER block boot.
- Shutdown: cancels heartbeat task + `aclose()` of the httpx client.

**Test net (`tests/test_risedual_monorepo_client.py` — 12 tests)**:
- `_enabled()` gate: kill-switch / missing env / present-env.
- `_post()` returns `sidecar_disabled` when off; swallows network errors silently.
- `fire_and_forget` no-loop drops the coro; on-loop schedules and runs.
- Body shape per emit (`receipts` / `memory-labels` / `calibrators` / `artifacts` / `heartbeat`).
- ADL wiring: `record_decision` mirrors a fake `emit_receipt` exactly once with the correct intent payload.

**Production status (2026-05-09)**:
- Backend boots cleanly: `[monorepo] sidecar enabled — heartbeat loop spawned`.
- Full pytest suite: **3151 / 3151 passing** (was 3139 — 12 new sidecar tests added).
- No regressions in calibration / bulk-replay / ADL receipt tests.
- `MONOREPO_BASE_URL`, `MONOREPO_INGEST_TOKEN`, `RUNTIME_NAME` already present in `.env`.

### FRED Macro Pipeline — Live (verified 2026-05-09)

`FRED_API_KEYS` is set in `/app/backend/.env`. Live verification:
- All 15 curated macro series populated (GDP, CPI, Core CPI, PCE, UNRATE, PAYEMS, ICSA, FEDFUNDS, DGS10, DGS2, T10Y2Y, HOUST, UMCSENT, BOPGSTB, GDPC1).
- `GET /api/fred/indicators` returns real values across 7 categories.
- No mock fallback in the macro feature pipeline.
- Search War Room FRED adapter (`adapters/fred.py`) reads the same env var via `KeyRotator`.

**Authority-boundary invariants preserved**:
- `BROKER_LIVE_ORDER_ENABLED` untouched (false).
- No retrain artifact writes triggered.
- No threshold lowering anywhere.
- The sidecar runs on the same process — failures are scoped to httpx timeouts and never re-raise.

### Confidence Calibration Layer — Option A (IsotonicRegression) (2026-05-09)

**Operator-approved Option A** with safe defaults: isotonic post-hoc mapping, fit only on firewall-trainable rows, served-confidence scope (CDO keeps raw), 24h refit cadence + manual admin trigger. Patent J thresholds unchanged. Execution authority unchanged.

**Backend** (`services/calibration_layer.py`):
- `fit_and_persist(rows)` — pulls rows through `chevelle_memory_labeler.trainable_only(...)`, drops UNRESOLVED / NEUTRAL / out-of-range, fits `sklearn.isotonic.IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")`, persists to `models/calibrators/calibrator_<UTC-version>.joblib` + atomic `active.txt` pointer.
- `apply(raw)` — total: never raises. Returns `CalibrationApplyResult` with raw_confidence, calibrated_confidence, calibration_method=`"isotonic"`, calibration_model_version, calibration_sample_count, calibration_applied, fallback_reason. Falls back to raw with `calibration_applied=False` on: missing artifact / stale (>72h) / predict-exception / non-finite raw / out-of-range raw.
- `reliability_snapshot(rows)` — read-only Patent J payload: active_version, sample_count, ECE, Brier, 10-bin reliability table, apply_health flags.
- `MIN_SAMPLES=50` (env-tunable), `STALE_AFTER_HOURS=72` (env-tunable). Versioned artifacts kept on disk for audit.

**Wired into served confidence** (`services/ml_paper_trader.py:maybe_paper_trade`):
- `apply()` runs immediately before the ADL-6 receipt + `paper_trades.insert_one`.
- Calibration metadata appended to BOTH `trade_doc["calibration"]` AND the ADL receipt's signal payload — so retrain joins see both raw and calibrated values.
- `signal.confidence` (raw Platt-calibrated) UNCHANGED — execution gates and Patent J still fire against raw, exactly as the operator decreed.

**Admin endpoints** (`routes/governance_chevelle_calibration.py`, owner-only):
- `POST /api/governance/chevelle/calibration/refit` — pulls last 30d of paper_trades + crypto_paper_trades, runs the firewall, fits + persists.
- `GET  /api/governance/chevelle/calibration/status` — returns the Patent J card payload.
- `GET  /api/governance/chevelle/calibration/reliability` — convenience accessor.

**Daily scheduler** (`services/scheduling/jobs.py`):
- `chevelle_calibration_refit_daily` cron job at **04:15 UTC**. Failures swallowed and logged — next-day retry.

**Frontend** (`components/admin/PatentJCard.jsx`):
- New "Patent J" tab in the AdminPanel Insights group (sibling to Kanban, Bulk Replay).
- Health pills (Loaded / Fresh / version), 4 metric tiles (Samples / Rows seen / ECE / Brier), reliability bin table with drift colour-coding (green <5pp, amber <15pp, rose >15pp), "Refit now" button.
- Footer copy explicitly states observation-only nature and the fall-back contract.

**Production validation (2026-05-09 08:05 UTC)**:
- First refit on real data: **1376 firewall-trainable samples** out of 1474 rows pulled (98 rows quarantined by the labeler, exactly as designed).
- **ECE = 0.2461** — predictor is 24.6pp overconfident on average.
- Biggest bin (1363 samples at predicted 0.752) has realised win rate of 0.504 — isotonic compresses down to ~0.504 for that range. This is the kind of correction the user mentioned ("isotonic ... directly attacks the ECE/Brier issue without weakening J").
- Active calibrator persisted: `calibrator_2026-05-09T08-05-03Z.joblib`.

**Test net** (`tests/test_calibration_layer.py`, **24 tests**):
- Pure helpers: reliability_bins / ECE / Brier on synthetic perfectly-calibrated and inverted inputs.
- Firewall integration: quarantined rows excluded, UNRESOLVED outcomes excluded, out-of-range confidence dropped.
- Fit refused below MIN_SAMPLES (no artifact, no active pointer touched).
- Fit succeeds at 60 samples; metrics include pre/post ECE + Brier; post_ece ≤ pre_ece (isotonic Pareto invariant).
- Apply: fallback when no calibrator → applied=False, calibrated mirrors raw.
- Apply: returns calibrated after fit, version + sample_count populated.
- Apply: clips out-of-range and non-finite raw inputs with explicit fallback_reason tags.
- Apply: stale calibrator (>STALE_AFTER_HOURS) → fallback.
- Apply: predict() exception → fallback (`apply_exception`).
- Reliability snapshot: zero-state + populated state, JSON-serializable (no numpy leaks).
- Static authority firewall: NO Mongo writes, NO broker / executor / Strategist / Auditor / kill-switch imports, NO verdict emission.

**Verified**:
- `tests/test_calibration_layer.py`: **24 passed** in 0.98s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **3139 passed, 0 failed** (was 3115 + 24 new = 3139 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":575}` (572 → 575, +3 calibration endpoints).
- Live admin refit: 1376 samples, ECE 0.2461, calibrator artifact persisted.
- Frontend lint clean.

**What's NOT in this drop (per operator spec — held for later)**:
- Option B (temperature scaling) — not added until isotonic results are measured in production.
- `TRAIN_REAL_MIN` lowering — not touched.
- Patent J threshold lowering — not touched.
- v2 artifact promotion — not touched.

### ADL-6 — APPROVED success-path receipts in `ml_paper_trader` (2026-05-09)

**Closes Gap A surfaced by the 2026-05-09 diagnostic.** The day-of-deploy diagnostic showed `paper_trades` had 152 rows in 24h but `alpha_decision_log` had only 1 equity row, all `NO_TRADE` — meaning every APPROVED equity paper trade was orphaned from the ADL stream. v2 retrain join was structurally blind to APPROVED outcomes regardless of how long we waited.

**Root cause**: `services/ml_paper_trader.py:maybe_paper_trade` writes `paper_trades` rows but never called `schedule_shadow_receipt`. ADL-1 baseline at `trading_bot_service.execute_signal` only fires for the legacy webhook path; the ML orchestrator path (which produces ~all real equity paper trades) goes through `ml_paper_trader.maybe_paper_trade` directly.

**Patch site**: `services/ml_paper_trader.py:maybe_paper_trade` — single fire-and-forget `schedule_shadow_receipt` call IMMEDIATELY before `db["paper_trades"].insert_one(trade_doc)` so the receipt always lands first in the ADL stream (or fires even when the insert is a duplicate-key no-op).

**Call shape**:
```python
schedule_shadow_receipt(
    db,
    signal={
        "symbol": ticker,
        "direction": direction_val,
        "confidence": float(signal.confidence),
        "prediction_id": signal.prediction_id,
        "regime": regime,
        "trade_id": trade_id,
        "source_layer": "ml_paper_trader",
    },
    market_data=None,
    lane="equity",
    requested_notional_usd=float(position_usd or 0.0),
    source="ml_paper_trader",
)
```
Wrapped in defensive belt+braces try/except — debug log on failure, never blocks the trade write.

**Hard rails (pinned by tests)**:
- 6-test net `tests/test_ml_paper_trader_adl_receipts.py` covers: receipt call IMMEDIATELY precedes the `paper_trades.insert_one` (within 80 lines, same try block), `lane="equity"` and `source="ml_paper_trader"` static check, defensive try/except wrapper static check, no broker imports added, signal payload contains all required keys (symbol/direction/confidence/prediction_id/regime/trade_id/source_layer), helper failure does NOT propagate.
- All execution paths byte-equivalent: trade_doc shape, idempotency (duplicate-key handling), reasoning overlay, brake/sovereign/penalty meta — preserved.
- Adjacent suites still pass: `test_ml_paper_trader_idempotency` 6/6, all 5 prior ADL suites (63 tests total).

**Verified**:
- `tests/test_ml_paper_trader_adl_receipts.py`: **6 passed** in 0.11s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **3115 passed, 0 failed** (was 3109 + 6 new = 3115 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":572}`.

**Cumulative ADL coverage now spans SIX entry points**:
1. **Equity paper trades** (APPROVED success) — `ml_paper_trader.maybe_paper_trade` (**ADL-6 — closes the APPROVED gap**).
2. **Equity executor** (legacy webhook success) — `trading_bot_service.execute_signal` (ADL-0 baseline).
3. **Crypto paper bot** — `crypto_paper_trader.run_crypto_symbol` (ADL-2).
4. **Day-trade scanner** — `day_trade_scanner.run_scan` per-candidate (ADL-3).
5. **Options paper agent** — `trading_agents.options_paper.run` per-ticker (ADL-4).
6. **Upstream gate-blocked decisions** — six sites in `trading_bot_service` (ADL-5).

**Expected impact at May 13 checkpoint**:
- Per-lane equity coverage: 0.66% → ~100% (every paper_trade row gets a receipt).
- by_decision: NO_TRADE-only → mix of APPROVED + NO_TRADE.
- Criterion #4 (APPROVED + blocked both present): ❌ → ✅.
- Criterion #1 (receipt-vs-trade > 90%): equity will lift dramatically; crypto stays at ~63% naturally because `crypto_paper_trader.run_crypto_symbol` only fires receipts when there's a strategist signal (most loop iterations have no signal), so the gap is real-but-expected for the crypto lane.

**What ADL-6 does NOT fix** (still organic-window dependent):
- Gap B (day-trade + options 0 receipts) — ADL-3/4 hooks are correct; the agents just haven't produced signals yet in the 7h post-deploy window.
- Gap C (history pre-dating ADL persistence) — heals automatically over the next 30d.

### Bulk Replay — pre-ingest CSV firewall scanner (2026-05-09)

**Pre-ingest sanity check** for historical paper-trade CSVs. Operator drag-drops a file, every row passes through the existing Chevelle Memory Labeling Firewall, response includes per-row verdicts + aggregate summary. **NO** writes, **NO** training, **NO** promotion, **NO** broker calls — read-only by construction.

**Backend** (`routes/admin_bulk_replay.py`):
- `POST /api/admin/bulk-replay/scan` (owner-only) — multipart upload, returns:
  - `aggregate`: total_rows, trainable_count, quarantined_count, toxic_count, avg_trust_weight, by_source, by_lane, by_event_era, by_failure_mode, by_data_quality.
  - `rows`: per-row `{csv_row_index, symbol, opened_at, closed_at, source, lane, event_era, failure_mode, data_quality, trust_weight, trainable, rejection_reason, rule_trace, grade}`.
  - `parse_errors`: row-level CSV parse failures kept SEPARATE from labeler quarantines so the operator can distinguish "bad CSV" from "bad data semantics".
  - `warnings`: high-quarantine and high-toxic rate flags.
- **Caps**: `MAX_ROWS_PER_UPLOAD=500` (cap-overflow rows reported via `parse_errors`), `MAX_FILE_BYTES=2 MiB` (over-cap → 413).
- **Grade tags**: `GREEN` (trainable, no failure mode), `AMBER` (toxic — trainable at trust=0.10), `RED` (quarantined, trust=0.0).

**Frontend** (`components/admin/BulkReplayPanel.jsx`):
- Drag-drop file zone + "Choose a file" fallback, "Scan" button.
- Five aggregate stat tiles (Total / Trainable / Toxic / Quarantined / Avg Trust).
- Five breakdown tables (source / lane / event_era / failure_mode / data_quality).
- Filter chips (All / Green / Amber / Red).
- Per-row table with color-coded grade badges.
- Footer note explicitly states the read-only nature.
- Wired into `AdminPanel` → Insights group as "Bulk Replay" tab.

**Test net** (`tests/test_admin_bulk_replay.py`, **20 tests**):
- Auth: non-owner gets 403.
- Clean CSV → all GREEN / trainable.
- Toxic rows → AMBER, trust=0.10, still trainable (rule 7).
- Inferred toxic_high_confidence on 0.92-conf 60% loss with no explicit failure tag.
- Missing source → RED, rejection_reason=`missing_source`.
- Missing timestamps → RED, rejection_reason=`missing_timestamps`.
- Missing symbol → RED, rejection_reason=`missing_symbol`.
- Aggregate breakdowns present (all 5 dimensions).
- avg_trust_weight computed correctly.
- Empty CSV / header-only CSV → 200 with zero rows.
- Partial malformed row mixed with good rows → endpoint stays 200, bad row quarantines.
- Invalid UTF-8 bytes handled gracefully (decoder uses `errors="replace"`).
- Row cap enforced (600 rows uploaded → 500 scanned + cap-overflow error reported).
- File-size cap enforced (>2 MiB → 413).
- Response includes caps for operator visibility.
- Per-row payload has all required fields.
- Static authority firewall: NO DB writes, NO broker/executor/training imports, NO `import_into_memory`/`train_all`/`force_train` route definitions.

**Verified**:
- 20 new tests pass. Full pytest **3109 passed, 0 failed** (was 3089 + 20 new = 3109 ✓).
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86. Frontend lint clean.
- E2E live test: 4-row CSV with 1 clean / 1 toxic / 1 missing-source / 1 yfinance returned exact expected verdicts (GREEN 0.50 / AMBER 0.10 / RED 0.00 / GREEN 0.05) and breakdowns.
- Maintenance page → Owner sign-in → admin dashboard renders normally with the new "Bulk Replay" tab visible in the Insights group.

### Public-Access Lockout — "Technical Difficulties" mode (2026-05-09)

**Operator decree**: while the ML stack is in its organic data-collection
window (May 8 → ~May 22, 2026), the public site shows a "Technical
Difficulties" page. Owner / admin can still sign in and reach the
dashboard.

**Backend**:
- `routes/system_access.py` — public `GET /api/system/access` returns lockout state + whether caller is admin; owner-only `POST /api/system/access` flips at runtime (no redeploy).
- `services/public_access_middleware.py` — Starlette middleware that returns 503 with stable JSON `{"error":"service_unavailable","reason":"scheduled_maintenance","message":"..."}` for non-admin `/api/*` traffic when locked. **Always-reachable**: `/api/health`, `/api/auth/*`, `/api/system/access`, all non-`/api/*` paths (so the SPA can render its own lockout page).
- Admin bypass: JWT email matched against `ADMIN_EMAILS` env allowlist (default `admin@risedual.ai`). Only access tokens (`type=access`) count — refresh tokens do NOT bypass.
- **Fail-open** on internal errors (DB hiccup → middleware does NOT hard-lock the site).
- State precedence: Mongo `system_settings.public_access` runtime override → `PUBLIC_ACCESS_ENABLED` env flag (default `true`).

**Frontend**:
- `hooks/useSystemAccess.js` — polls `/api/system/access` on mount + every 30s. Exposes `{ ready, publicAccess, isAdmin, message, refresh }`.
- `components/MaintenancePage.jsx` — full-screen "Technical Difficulties" view with RISEDUAL branding, pulsing status dot, your operator copy, and "Owner sign-in" entry point that opens `AuthModal`.
- `App.js` gate — when locked AND not admin AND no signed-in user, renders `MaintenancePage` instead of the normal SPA. Re-polls access whenever auth state changes (admin login → lockout disappears without page reload).

**Tests** (27 new, `tests/test_public_access_middleware.py`):
- Bypass paths reachable while locked (`/api/health`, `/api/auth/*`, `/api/system/access`).
- Anonymous `/api/*` traffic gets 503 with stable JSON shape (`error`, `reason`, `message` keys).
- Frontend assets (non-`/api/*`) pass through under lockout.
- Admin via cookie OR `Authorization: Bearer` header bypasses.
- Non-admin email JWT does NOT bypass (even if perfectly valid).
- Refresh tokens do NOT bypass — only access tokens carry admin claim.
- Invalid / garbage JWT → no bypass.
- Resolver exception → fail open.
- Env-default truthy/falsy token table.
- ADMIN_EMAILS allowlist parses comma-separated, lowercases, strips whitespace.

**Verified**:
- 27 new tests pass. Full pytest **3089 passed, 0 failed** (was 3062 + 27 new = 3089 ✓).
- E2E smoke: anonymous `/api/dashboard` → 503; admin `/api/dashboard` → bypasses; `/api/health` always 200; `/api/auth/login` always reachable.
- Maintenance page screenshot renders cleanly with branding intact.

**Operator runbook**:
- **Engage lockout**: `POST /api/system/access {"enabled": false}` (auth as admin first).
- **Disengage lockout**: `POST /api/system/access {"enabled": true}`.
- **Inspect state** (no auth): `GET /api/system/access`.
- **Add additional admins**: set `ADMIN_EMAILS=admin@risedual.ai,other@example.com` in `backend/.env` and restart backend.

**Current state at session end**: lockout is **ON** per operator decree. Public sees the maintenance page; admin signs in via "Owner sign-in" link at the bottom of the page.

### Chevelle Memory Labeler — read-side firewall (2026-05-09)

**Pre-Chevelle infrastructure**: a pure label-resolution layer that
gates every memory before it can flow into Chevelle's training /
observation engine.

**Module layout** (3-file split, all under `core-governance` 600-line preferred ceiling):
| File | Lines | Authority |
|---|---:|---|
| `services/chevelle_memory_labels.py` | 111 | enums + trust-ladder constants + observation-policy tokens (data only) |
| `services/_chevelle_resolvers.py` | 324 | pure label-resolver helpers (era / lane / source / outcome / failure / quality / trust) |
| `services/chevelle_memory_labeler.py` | 376 | dataclass + public API (`label_memory`, `label_memories`, `trainable_only`, `quarantined_only`) |

**Observation policy** (added 2026-05-09 enhancement):
The `chevelle_can_observe` flag now responds to an optional
`observation_policy` parameter on `label_memory` / `label_memories`:
| Policy | Effect on `chevelle_can_observe` |
|---|---|
| `"all"` (default) | True for every well-formed row; False only for non-dict / None input |
| `"exclude_synthetic"` | False for synthetic-tier rows (`trust=0.05`) — useful during live drills |
| `"live_only"` | False for anything below `live_real_fill` (`trust=1.00`) — useful for ground-truth calibration passes |

Critical invariant pinned by tests: the policy gates **observation only**. `trust_weight` and `trainable` are invariant across policies — operator can flip policies between runs without re-labeling the corpus. Unknown policy strings fall back to `"all"` (never raise).

**The 10 operator-mandated hard rules** — pinned by tests:
1. Every memory must have `source` (or be quarantined).
2. Every memory must have `opened_at`/`closed_at` (or be quarantined).
3. Every memory must have a `lane` (`equity`/`crypto`/`options`/`macro`/`unknown`).
4. Every memory must have a normalized `symbol` (or be quarantined).
5. Old trades get an `event_era` (9-bucket: GFC_2008 / FLASH_CRASH_2010 / CHINA_DEVAL_2015 / VOL_SPIKE_2018 / COVID_2020 / RATE_HIKE_2022 / AI_BUBBLE_2024_2025 / CURRENT_REGIME / UNKNOWN_ERA).
6. Bad/ambiguous rows are `QUARANTINED` (trust=0.0, trainable=False), NEVER deleted.
7. Toxic/failure labels REDUCE trust (0.10 floor), never delete.
8. Chevelle MAY observe quarantined memories (`chevelle_can_observe=True`) but `trainable_only(...)` filters them out.
9. NO BUY/SELL verdicts emitted from labels (static check pinned).
10. NO broker/executor/Strategist/RoadGuard/FastVeto/kill-switch imports (static check pinned).

**Trust ladder** (operator-specified values, encoded as module-level constants):
| Source / state | Weight |
|---|---:|
| `live_real_fill` (alpaca / kraken / webull / live) | 1.00 |
| `recent_paper_trade` (≤30 days) | 0.50 |
| `historical_paper_trade` (>30 days) | 0.25 |
| `current_macro_proxy` (lane=macro / fred) | 0.15 |
| `toxic_memory` (BLOWUP / TOXIC_HIGH_CONFIDENCE / REGIME_MISMATCH / DATA_INTEGRITY) | 0.10 |
| `synthetic_backtest` (yfinance / pre-cutover) | 0.05 |
| `quarantined` (missing required fields) | 0.00 |

**Failure-mode taxonomy** (coarse-grained projection of the live
`services.failure_mode_classifier` taxonomy, suitable for training
gates): `NONE` / `BLOWUP` / `REGIME_MISMATCH` /
`TOXIC_HIGH_CONFIDENCE` / `DATA_INTEGRITY` / `UNKNOWN`.

**Test net** (`tests/test_chevelle_memory_labeler.py`, **56 tests**):
- All 10 hard rules covered (1-4 rejection paths, 5 era-mapping, 6 quarantine bucket, 7 toxic-not-deleted, 8 trainable filter, 9 no-verdict static check, 10 no-broker-import static check across all 3 split files).
- Trust ladder pinned per-tier.
- Symbol normalization (`BTC-USD` → `BTC`, `BTC/USDT` → `BTC`, etc.).
- Era mapping for all 9 buckets including pre-2008 → UNKNOWN.
- Idempotence + non-mutation of input dict.
- `non_dict_input` / `None` input quarantined (never raises).
- Recent vs. historical paper-trade boundary at 30 days.
- High-conviction loss → toxic_high_confidence inferred when no explicit failure marker.
- DB-write static check (no `.insert_*` / `.update_*` / `.delete_*` / `.bulk_write` / `.drop` anywhere in the 3 files).

**NOT integrated yet** — this is the labeler (the contract). Chevelle's training/observation engine itself still has to be built, and it MUST consume only `trainable_only(label_memories(...))`. No live call sites yet — by design, until Chevelle is ready.

**Verified**:
- `tests/test_chevelle_memory_labeler.py`: **56 passed** in 0.16s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **3054 passed, 0 failed** (was 2998 + 56 new = 3054 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

### ADL-5 — upstream blocked-decision receipts in `trading_bot_service` (2026-05-09)

**Final micro-phase of the operator-mandated ADL persistence-coverage fix.** Captures gate-blocked decisions BEFORE they short-circuit the executor — the original ADL-1 success-path receipt only fired AFTER all gates passed, leaving every kill-switch / drawdown / fast-veto / sizing / RoadGuard ENFORCE block invisible to retraining.

**New helper** `services/trading_bot_service.py:_schedule_blocked_receipt(...)` (50 lines):
- Module-level fire-and-forget wrapper around `schedule_shadow_receipt`.
- Tags signal payload with `blocked_at` (gate stage) + `block_reason` (verbatim skip reason) on a COPY of the caller's dict — never mutates.
- Source tag: `"execute_signal_blocked"` (vs `"execute_signal"` for the success-path receipt) so retrain joins can disaggregate at the source level.
- Defensive belt+braces try/except — a raising or False-returning helper can NEVER alter `execute_signal`'s return value.

**Hooked at six gate sites in `trading_bot_service.py`**:
| Gate stage tag | Site | Coverage |
|---|---|---|
| `kill_switch_or_drawdown` | `_check_kill_switch_and_drawdown` skip path | kill switch active, kill switch trip on drawdown |
| `fast_veto_enforce` | Fast Veto enforce-mode path | shadow-promoted veto enforcement |
| `size_chain` | `_compute_adjusted_size` skip path | sector_cap / max_concurrent_trades / max_portfolio_exposure (all map to "portfolio limits reached"), drawdown allocator ("risk control"), low-confidence floor |
| `resolve_qty` | `_resolve_qty` skip path | invalid price / quote unavailable |
| `roadguard_enforce` | RoadGuard enforce-mode path | shared cross-lane caps, broker health, daily loss limit |
| `max_trades_per_day` | `process_signal_for_bots` dispatcher cap (line ~885) AND `process_webhook` daily cap (line ~1260) | daily trade rate limit at both entry points |

**Hard rails (pinned by tests)**:
- 12-test net `tests/test_execute_signal_adl_blocked_receipts.py` covers: each gate produces ONE receipt with correct `blocked_at`/`block_reason`/`lane`/`source`, caller's signal dict is NEVER mutated (deep-copy assertion), helper failure preserves the return shape, success path still produces ONLY the existing single `source="execute_signal"` receipt with no `blocked_at` key, kill-switch trip prevents downstream gates from running (no double-log), broker is NEVER called on a blocked path (tripwire on `_execute_bot_trade`), helper unit tests pin source tag + non-mutation + exception swallowing, static check that the helper uses the shared dispatcher and never invokes `run_shadow_pipeline` directly.
- All execution paths byte-equivalent: every skip dict, every error dict, every order shape, every loop's `continue` semantics — preserved.
- Code-size: `trading_bot_service.py` 1336 → 1490 lines (+154, well under existing 1575 baseline; allowlisted with documented justification).

**Verified**:
- `tests/test_execute_signal_adl_blocked_receipts.py`: **12 passed** in 0.19s.
- Targeted superset (test_execute_signal_usd + test_trading_bot_broker_contract + test_trading_bot_adaptive_sizing + test_executor_lanes + test_portfolio_risk_engine + test_execute_signal_adl_blocked_receipts + test_receipt_dispatch): **114 passed** in 1.42s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2998 passed, 0 failed** (was 2986 + 12 new = 2998 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

**Cumulative ADL coverage now spans FIVE entry points across SIX gate stages**:
1. **Equity executor** — `trading_bot_service.execute_signal` success path (ADL-0 baseline).
2. **Crypto paper bot** — `crypto_paper_trader.run_crypto_symbol` (ADL-2).
3. **Day-trade scanner** — `day_trade_scanner.run_scan` per-candidate (ADL-3).
4. **Options paper agent** — `trading_agents.options_paper.run` per-ticker (ADL-4).
5. **Upstream gate-blocked decisions** — six sites in `trading_bot_service` (ADL-5): kill_switch_or_drawdown, fast_veto_enforce, size_chain (sector_cap / max_concurrent / max_portfolio / risk_control / low_confidence), resolve_qty, roadguard_enforce, max_trades_per_day (dispatcher + webhook).

**ADL persistence-coverage fix is COMPLETE.** Operator decree: let receipts accumulate organically for 24–48h, then re-run `python -m scripts.diagnose_alpha_decision_log_persistence` and `python -m scripts.diagnose_alpha_retrain_join` to confirm:
- Per-lane receipt-vs-trade ratios > 90%.
- Receipt distribution across `blocked_at` stages.
- Retrain-join coverage rises from 2.50% → ≥ 50% in the next 30-day window.

**ADL Coverage Tile** remains held until that organic window passes.

### ADL-4 — wire `options_paper` agent to the shared receipt helper (2026-05-09)

**Micro-phase 4 of 5** of the operator-mandated ADL persistence-coverage fix. Closes the options-lane gap.

**Survey findings**:
- Real options paper executor exists at `services/trading_agents/options_paper.py:run` (160 → 196 lines after wiring).
- Scheduled every 30 min via APScheduler job `agent_options_paper`.
- Trades execute via `record_paper_trade` → `learning_engine_trades` Mongo collection (tagged `strategy="options_paper"`).
- Per-ticker decision object `(direction, confidence)` is built by `_score_ticker(ticker, db)` reading from `features_snapshots`.
- Decision points: APPROVED (conf ≥ 0.72 → trade), NO_TRADE (conf < 0.72 → skip), post-signal skipped (price<=0, record_paper_trade returns falsy).
- Pre-signal early returns (deliberately NOT logged): db None, kill switch active, `_score_ticker` returns None.

**Patch site**: insertion point right AFTER `direction, conf = scored` unpack and BEFORE the conviction floor check. One call covers APPROVED + NO_TRADE uniformly.

**Call shape**:
```python
schedule_shadow_receipt(
    db,
    signal={
        "symbol": ticker,
        "direction": direction,
        "confidence": float(conf),
        "strategy": _STRATEGY,                # "options_paper"
        "min_confidence_threshold": _MIN_CONFIDENCE,  # 0.72
        "watchlist_position": considered,
        "source_layer": "options_paper_agent",
    },
    market_data=None,
    lane="options",
    requested_notional_usd=float(_POSITION_USD),  # 500.0
    source="options_paper_bot",
)
```
Wrapped in defensive belt+braces `try/except` that logs `[options_paper] ADL receipt schedule skipped` at DEBUG.

**Hard rails (pinned by tests)**:
- 13-test net `tests/test_options_paper_adl_receipts.py` covers: APPROVED (conf≥0.72) schedules receipt with options lane/source, NO_TRADE (conf<0.72) STILL schedules receipt, signal payload integrity (symbol/direction/confidence/strategy/source_layer), pass-through of `_POSITION_USD` as `requested_notional_usd`, three pre-signal early returns produce ZERO receipts (db None, kill switch ON, `_score_ticker` returns None), raising helper does NOT crash `run`, helper return=`False` does NOT branch caller, plus two static checks (no `run_shadow_pipeline` direct call, no broker/executor imports).
- Options paper agent execution path is byte-equivalent: kill-switch behaviour, conviction gate, MAX_OPENS limit, watchlist iteration, narrate_scan_skip behaviour — all unchanged.
- Code-size: `options_paper.py` 160 → 196 lines (+36, well under `core-governance` 600 preferred / 800 hard ceiling).

**Verified**:
- `tests/test_options_paper_adl_receipts.py`: **13 passed** in 0.15s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2986 passed, 0 failed** (was 2973 + 13 new = 2986 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

**Cumulative ADL coverage now spans FOUR lanes**: equity executor, crypto paper bot, day-trade scanner, options paper agent. All four hooked via the same `schedule_shadow_receipt` helper for uniform fire-and-forget semantics.

**Remaining ADL micro-phase**:
- **ADL-5**: Move/duplicate receipt earlier so kill_switch / risk_guard / sector_cap / max_trades_per_day / RoadGuard ENFORCE blocked decisions also produce receipts.

### ADL-3 — wire `day_trade_scanner.py` to the shared receipt helper (2026-05-09)

**Micro-phase 3 of 5** of the operator-mandated ADL persistence-coverage fix. Closes the day-trade scanner gap — every scanned candidate now produces an `alpha_decision_log` receipt regardless of whether the gates approve or block it.

**Patch site**: `services/day_trade_scanner.py:run_scan` per-candidate loop. Insertion point is INSIDE the iteration, AFTER `apply_gates` resolves the gate verdict (passed/blocker/inputs) and AFTER the `gate_passed` / `gate_blocker` / `chosen` book-keeping. The original `if not passed: ... continue` was restructured to an `if/else` so both branches fall through to a single shared receipt-scheduling block — APPROVED candidates AND blocked candidates both get one receipt per scan iteration.

**Call shape** (verbatim):
```python
schedule_shadow_receipt(
    db,
    signal={
        "symbol": c.symbol,
        "direction": c.direction,
        "confidence": float(c.score),
        "score": float(c.score),
        "rank": c.rank,
        "asset_class": c.asset_class,
        "gate_passed": c.gate_passed,
        "gate_blocker": c.gate_blocker,
        "prediction_id": c.prediction_id,
        "scan_id": scan_id,
        "source_layer": "day_trade_scanner",
    },
    market_data=None,
    lane="equity",
    requested_notional_usd=0.0,
    source="day_trade",
)
```
Wrapped in a defensive belt+braces `try/except` that logs `[day-trade-scan] ADL receipt schedule skipped` at DEBUG and continues. Lane is hard-coded `"equity"` per operator brief — the source tag `"day_trade"` is the primary discriminator from `crypto_paper_trader` and `trading_bot_service` ADL streams.

**Pre-signal early returns** (db missing → `scan_universe` returns `[]`, empty universe, db None) deliberately skip the loop entirely so no receipts are scheduled when no candidate exists.

**Hard rails (pinned by tests)**:
- 12-test net `tests/test_day_trade_scanner_adl_receipts.py` covers: APPROVED candidate produces receipt, blocked candidate (`below_min_score`, `non_directional_prediction`, `already_holding`) still produces receipt, signal payload contains `gate_passed`/`gate_blocker`, multi-candidate fan-out (3 candidates → 3 receipts), empty universe → 0 receipts, `db=None` → 0 receipts, raising helper does NOT alter `run_scan` result, helper return=`False` does NOT branch caller, plus two static checks (no `run_shadow_pipeline` direct call, no broker/executor imports anywhere in the file).
- Day-trade execution path is byte-equivalent: ScanResult shape, target writes, scan-log writes, gate iteration order — all unchanged.
- Code-size: `day_trade_scanner.py` 412 → 454 lines (+42, well under `core-governance` 600 preferred / 800 hard ceiling).

**Verified**:
- `tests/test_day_trade_scanner_adl_receipts.py`: **12 passed** in 0.15s.
- Targeted superset (test_day_trade_scanner + test_day_trade_scanner_adl_receipts + test_receipt_dispatch + test_crypto_paper_trader_adl_receipts): **50 passed** in 1.55s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2973 passed, 0 failed** (was 2961 + 12 new = 2973 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

**Cumulative ADL coverage now spans**: equity executor (ADL-0 baseline at `trading_bot_service.execute_signal`), crypto paper bot (ADL-2 at `crypto_paper_trader.run_crypto_symbol`), day-trade scanner (ADL-3 at `day_trade_scanner.run_scan`).

**Remaining ADL micro-phases**:
- **ADL-4**: Wire options paper bot if present (`lane="options"`).
- **ADL-5**: Move/duplicate receipt earlier so kill_switch / risk_guard / sector_cap / max_trades_per_day / RoadGuard ENFORCE blocked decisions also produce receipts.

### ADL-2 — wire `crypto_paper_trader.py` to the shared receipt helper (2026-05-09)

**Micro-phase 2 of 5** of the operator-mandated ADL persistence-coverage fix. Closes the diagnosed crypto-lane gap (was 1.40% receipt coverage — 3/214 trades). Single early-fire `schedule_shadow_receipt` call covers APPROVED, HOLD, and gate-blocked paths uniformly.

**Patch site**: `services/crypto_paper_trader.py:run_crypto_symbol`. Insertion point is right after `signal = {**raw_signal, "symbol": symbol, "regime": ..., "failure_context": ...}` and BEFORE the "Early HOLD evaluation" branch. Because the call sits BEFORE every downstream gate (failure-memory cooldown, dynamic-confidence gate, adversarial veto, integrity mitigation, Patent J/K/M/I guard, Patent I legacy fallback, size-zero clamp, quote check), one call covers every decision path that actually has a strategist signal.

**Call shape** (verbatim):
```python
schedule_shadow_receipt(
    db,
    signal=signal,
    market_data=None,            # let extract_live_features fill live values
    lane="crypto",
    requested_notional_usd=0.0,  # pre-sizing snapshot
    source="crypto_paper_trader",
)
```
Wrapped in a defensive belt+braces `try/except` that logs `[crypto_paper] ADL receipt schedule skipped` at DEBUG and continues — even though `schedule_shadow_receipt` already swallows its own failures.

**Pre-signal early returns** (db_missing / not_crypto_symbol / insufficient_bars / ticker abandonment) deliberately do NOT call the helper — those have no signal to log.

**Hard rails (pinned by tests)**:
- 11-test net `tests/test_crypto_paper_trader_adl_receipts.py` covers: helper called on normal uptrend, lane=`"crypto"`, source=`"crypto_paper_trader"`, signal-with-symbol forwarded, `market_data=None`, `requested_notional_usd=0.0`, helper-NOT-called for db_missing / non-crypto / insufficient-bars, raising helper does NOT crash `run_crypto_symbol`, helper return=`False` does NOT branch caller, and a static check that the bot uses `schedule_shadow_receipt` (never `run_shadow_pipeline` directly).
- Crypto execution path is byte-equivalent: signal dict, sizing math, trade-row schema, audit-log writes, return shape — all unchanged.
- Code-size: `crypto_paper_trader.py` 1232 → 1256 lines (+24, soft-baseline drift only — preferred-ceiling test does NOT fail on growth of existing baseline entries).

**Verified**:
- `tests/test_crypto_paper_trader_adl_receipts.py`: **11 passed** in 0.78s.
- Targeted superset (test_crypto_paper_bot + test_crypto_paper_trading + test_crypto_paper_trader_adl_receipts + test_receipt_dispatch): **66 passed** in 2.30s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2961 passed, 0 failed** (was 2950 + 11 new = 2961 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

**Remaining ADL micro-phases**:
- **ADL-3**: Wire day-trade scanner/executor (lane="equity").
- **ADL-4**: Wire options paper bot if present.
- **ADL-5**: Move/duplicate receipt earlier so kill_switch / risk_guard / sector_cap / max_trades_per_day / RoadGuard ENFORCE blocked decisions also produce receipts.

### ADL-1 — shared fire-and-forget receipt helper (2026-05-09)

**Micro-phase 1 of 5** of the operator-mandated ADL persistence-coverage fix. Pure helper extraction — zero behaviour change at the only existing call site, but makes ADL-2/3/4/5 trivial drop-in additions.

**New module** `services/ml/receipt_dispatch.py` (122 lines):
- `schedule_shadow_receipt(db, *, signal, market_data, lane, requested_notional_usd, ...)` — wraps the `asyncio.create_task(run_shadow_pipeline(...))` pattern with the surrounding try/except.
- Returns `True` if scheduled, `False` on ANY failure (no event loop, no Mongo, broken import, create_task raise). **Caller's execution path is identical either way**.
- Coroutines explicitly closed when `create_task` raises so failure paths don't leak unawaited-coroutine warnings.

**Refactored**: `services/trading_bot_service.py:execute_signal` Phase-5a block (24 inline lines → 11-line helper call). Verbatim args, verbatim fire-and-forget, verbatim warning-only failure.

**Hard rails (pinned by tests)**:
- 9-test net `tests/test_receipt_dispatch.py` covers: happy path (kwargs forwarded byte-for-byte), `db is None` short-circuit, no-running-loop short-circuit (sync caller / cron), `create_task` failure (caught + logged), broken `shadow_wiring` import, raising inner pipeline (caller still returns `True`), full byte-equivalence to legacy inline pattern.
- Static authority firewall: NO imports from broker / trading_bot_service / crypto_paper_trader / paper_trading_service / routes.broker / executors / RoadGuard / FastVeto / pipeline / broker_wire / kill_switch / alpha_decision_log.
- No Mongo write verbs (`insert_*`, `update_*`, `delete_*`, `drop`, `bulk_write`).
- No env reads (`os.environ` / `os.getenv` / `setenv`).

**Verified**:
- `tests/test_receipt_dispatch.py`: **9 passed** in 0.9s.
- `tests/test_trading_bot_broker_contract.py`: **14 passed** — broker contract net unchanged.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2950 passed, 0 failed** (was 2941 + 9 new = 2950 ✓).

**Remaining ADL micro-phases**:
- **ADL-2**: Wire `crypto_paper_trader.py` (lane="crypto").
- **ADL-3**: Wire day-trade scanner/executor (lane="equity").
- **ADL-4**: Wire options paper bot if present.
- **ADL-5**: Move/duplicate receipt earlier so kill_switch / risk_guard / sector_cap / max_trades_per_day / RoadGuard ENFORCE blocked decisions also produce receipts.

### `alpha_decision_log` persistence diagnostic + findings (2026-05-09)

**Operator-mandated upstream investigation** of the bottleneck the previous diagnostic surfaced. Read-only — no writes, no env mutation, no schema mutation, no retrain, no Phase 6 changes.

**New tool**: `python -m scripts.diagnose_alpha_decision_log_persistence --window-hours N`. Probes (read-only):
- Static call-site survey (which files invoke `record_decision` / `record_pipeline_decision` / `run_shadow_pipeline`?)
- ADL TTL + index inspection
- Write breakdown by lane / decision / blocked_at stage / symbol
- Per-lane gap analysis (paper trades vs ADL receipts)
- Recent supervisor-log scan for swallowed-exception warnings
- 8-hypothesis classifier + actionable recommendations

**Files**:
- `scripts/diagnose_alpha_decision_log_persistence.py` (267 lines, runnable CLI).
- `services/diagnostics/alpha_decision_log_persistence.py` (555 lines, probe/classifier library — under the 600 `core-governance` ceiling).
- `tests/test_diagnose_alpha_decision_log_persistence.py` (**16 tests passing in 0.43s**).

**Live findings (window=24h)**:
| Probe | Verdict |
|---|---|
| A. Persist hook never called? | NO — 2 call sites total (1× `run_shadow_pipeline`, 1× `record_pipeline_decision`) |
| **B. Single chokepoint?** | **YES** — only ONE caller (`trading_bot_service.execute_signal:428`, fire-and-forget). Every other executor (crypto_paper_trader, day_trade scanner/executor, options paper bot, signal-bot dispatcher tail) bypasses the hook. |
| **C. NO_TRADE silently dropped?** | **INVERTED** — every recorded receipt is NO_TRADE (perception-blocked). 0 APPROVED rows. The success path never produces receipts. |
| D. Lane imbalance? | NO — equity=4, crypto=3 (both lanes can produce rows when the hook is hit) |
| **E. Executors writing trades without receipts?** | **YES** — equity 4/185 (2.16%), crypto 3/214 (1.40%). ~98% of trades fire without producing any receipt. |
| F. TTL purge pressure? | NO — TTL 30.0d, well above any sensible window |
| G. Swallowed exceptions in logs? | NO observed (but absence of warnings is NOT proof — exceptions are silently caught in `record_decision` + the create_task callback) |
| **H. Narrow symbol coverage?** | **YES** — only 2 distinct symbols (AAPL, BTC-USD) ever produced a receipt |

**Root cause confirmed**:
1. `record_decision` is called from EXACTLY ONE place (`shadow_wiring.py:280`).
2. `run_shadow_pipeline` is called from EXACTLY ONE place (`trading_bot_service.execute_signal:428`).
3. That call site is `_asyncio_p5a.create_task(...)` — fire-and-forget, never awaited.
4. The legacy executor path is the only entry to the shadow wiring. Every other executor (crypto_paper_trader, day-trade pipeline, options paper bot, signal-bot tail paths) writes paper trades but produces ZERO ADL receipts.

**Recommendations surfaced (priority order)**:
1. **PRIMARY**: Add fire-and-forget `run_shadow_pipeline` calls at every executor entry point — crypto_paper_trader, day_trade scanner/executor, options paper bot, signal-bot dispatcher (where it bypasses execute_signal).
2. Crypto executor specifically — confirm by inspection then patch.
3. Equity day-trade executor — same.
4. (Auto-suppressed when not applicable): NO_TRADE-decisions-only inversion → move the persist call EARLIER in the pipeline so upstream gates (kill_switch, risk guard, sector cap) ALSO produce receipts.

**Verified**:
- `tests/test_diagnose_alpha_decision_log_persistence.py`: **16 passed** in 0.43s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2941 passed, 0 failed**.
- Live CLI reproduces every finding.

### `no_decision_log` skip-rate diagnostic + findings (2026-05-09)

**Operator-mandated diagnosis** of why retraining only matched 5/1445 paper trades. Built a read-only `python -m scripts.diagnose_alpha_retrain_join` tool that probes all 8 hypothesis questions verbatim. **No writes, no env mutation, no schema mutation, no retrain promotion** — strict observation.

**Live findings (window=30d)**:
| Probe | Verdict |
|---|---|
| 1. Trades pre-date ADL? | YES — 1544 unmatched trades closed before oldest ADL row |
| 2. Unstable join keys? | YES — `paper_trades` uses `'ticker'`, `crypto_paper_trades` uses `'symbol'`, `alpha_decision_log` uses `'symbol'` |
| 3. Wrong timestamp field? | N/A — join is keyed by `(symbol, lane)`, time only bounds the read window |
| 4. Symbol normalization mismatch? | YES — 194 crypto rows would match if `BTC` ↔ `BTC-USD` |
| 5. Lane mismatch? | YES — neither paper-trade collection has a `lane` field; both default to "equity" |
| 6. ADL only for Phase 5a+? | YES — ADL spans 0.40d but window is 30d |
| 7. ADL TTL too short? | LIKELY — only 0.40d of history; persistence layer brand new |
| 8. Crypto vs equity schema drift? | YES — symbol field `ticker` vs `symbol`; pnl `pnl_usd` vs `pnl` |

**Match outcome**: 50/1998 in-window paper trades match (2.50%). 1544 trades pre-date ADL; 210 have no ADL row at all; 194 are normalization-mismatches.

**Real bottleneck identified**: `alpha_decision_log` has only **7 rows total** across **2 distinct keys** (`AAPL/equity`, `BTC-USD/crypto`), span 0.40d. The ADL persistence layer (in `services/ml/shadow_wiring` or `pipeline.py`) is barely firing. NO BACKFILL is possible — upstream features can't be recreated.

**Files**:
- `scripts/diagnose_alpha_retrain_join.py` (279 lines) — runnable CLI + report formatter.
- `services/diagnostics/alpha_retrain_join.py` (478 lines) — probes/classifiers/recommendations library (split out + relocated under `services/diagnostics/` since it's a library, not a runnable script — fits the 600-line `core-governance` ceiling).
- `services/diagnostics/__init__.py` — package docstring with the read-only authority-boundary contract.
- `tests/test_diagnose_alpha_retrain_join.py` — **28 tests** pinning pure helpers, hypothesis classifiers, recommendations, and a static check that the diagnostic has no broker/executor/RoadGuard/FastVeto imports and zero Mongo write verbs (`insert_*`, `update_*`, `delete_*`, `drop`, `bulk_write`, ...).

**Recommendations surfaced (priority order)**:
1. **PRIMARY**: ADL persistence layer is the real blocker — investigate `services/ml/shadow_wiring`/`pipeline.py` to confirm it fires on every decision.
2. Patch `extract_rows()` to read `symbol or ticker or pair`.
3. Derive `lane='crypto'` for `crypto_paper_trades` / `asset_class='crypto'` rows.
4. Add symbol-normalization step (`BTC` → also probe `BTC-USD`).
5. Patch `label_from_outcome()` to accept `pnl_usd` / `pnl` / `r_multiple` (not just `realized_pnl_usd`).
6. **DO NOT** run v2 retrain `--write-artifact` yet — 2.5% coverage trains a non-representative slice.

**Verified**:
- `tests/test_diagnose_alpha_retrain_join.py`: **28 passed** in 0.12s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2925 passed, 0 failed**.
- Live CLI: produces the full hypothesis report + match-attempt + sample rows + recommendations on real data.

### v2 retraining schema — `alpha_v2_fundamentals_technicals` (2026-05-09)

**Operator-mandated next step** after the feature builders. Extends `scripts/retrain_alpha_models.py` to consume `frame.market.fundamentals.*` + `frame.market.technicals.*` while leaving live inference completely untouched.

**Schema layout**:
| Schema | Dim | Layout |
|---|---:|---|
| `alpha_v1` (DEFAULT, unchanged) | 10 | perception 6 sub-scores + avg_conf + recall ±ratios + intent_encoded |
| `alpha_v2_fundamentals_technicals` | **40** | v1 (10) + fundamentals (16, equity-only) + technicals (14, both lanes) |

**Lane semantics (pinned by tests)**:
- **Equity rows**: fundamentals + technicals both expected; missing → 0.0 fills + counter bumped.
- **Crypto rows**: fundamentals NOT required (no skip, no counter bump); technicals expected.
- **Pre-feature-builder historical rows** (no `frame.market` payload): trainable under v2 — slots fill 0.0, counters bump. Historical training data is NEVER abandoned.
- **v1 contract preserved**: `_FEATURE_DIM == 10` Strategist invariant still pinned. v1 default everywhere.

**Files**:
- `scripts/retrain_alpha_models.py`: 607 → 755 lines (under the 800 architectural ceiling).
- `scripts/_retrain_v2_schema.py`: new, 218 lines. Strangler-split holds the v2 schema constants + ordered feature names + `reconstruct_features_v2`. Re-exported from the main script — external imports unchanged.

**Report / manifest enrichment** (always present, zero on v1):
- `feature_schema_version`
- `feature_count` + `feature_count_v1_baseline`
- `feature_names` (full ordered list)
- `missing_fundamentals_count` (equity-only)
- `missing_technicals_count`
- Manifest filename includes the schema tag (`strategist_alpha_v2_fundamentals_technicals_<sha>_<ts>.joblib`) — operator can never confuse v1/v2 by inspection.
- Manifest field `promotion_blocked_reason="v2_schema_requires_matching_inference_adapter"` for v2; `null` for v1.

**Hard rails (pinned by tests)**:
- v2 retrain MUST NOT mutate `STRATEGIST_ARTIFACT` / `AUDITOR_ARTIFACT` env vars (sentinel test).
- No broker / executor / RoadGuard / FastVeto / pipeline / kill-switch imports in either script (static check).
- No `Verdict.X` / `set_active` / `promote_now` / `.place_order` markers (static check).
- v2 artifacts NEVER auto-promoted. Live inference stays on v1 until a v2 inference adapter is explicitly built.

**CLI**:
```
python -m scripts.retrain_alpha_models --feature-schema alpha_v2_fundamentals_technicals \
    [--window-days N] [--write-artifact] [--json out.json]
```
Default schema is `alpha_v1` so existing automation is byte-equivalent.

**Verified**:
- `tests/test_retrain_alpha_models.py`: **31 passed** (17 v1 baseline preserved + 14 new v2 tests).
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2897 passed, 0 failed**.
- Live CLI smoke (`--feature-schema alpha_v2_fundamentals_technicals --window-days 30`): runs cleanly, prints `feature count: 40 (v1 baseline = 10)`, prints the v2 promotion-block warning at the bottom of the report.

### Feature builders — fundamentals + technicals (2026-05-09)

**Operator directive**: P/E + SMA logic must be a **feature/input layer**, never a decision authority. Built two new modules under `services/ml/features/` that emit structured feature dicts for the ML perception/strategist layers to consume during retraining.

**New package** `services/ml/features/`:
| File | Lines | Authority |
|---|---:|---|
| `__init__.py` | 41 | Package docstring + authority-boundary contract |
| `fundamentals.py` | 168 | Equity-only AV-OVERVIEW shaper (P/E, EPS, PEG, dividend yield, ROE, profit margin, growth YOY, beta, book value, EV/EBITDA, …) + binary band flags (`pe_in_value_band`, `pe_in_growth_band`, `pe_in_speculative`, `negative_eps`, `dividend_payer`) |
| `technicals.py` | 123 | Universal (equity + crypto) wrapper around `services.universe_technicals.compute_technicals` — RSI14, MACD, SMA20/50/200 + binary flags (`price_above_sma20/50/200`, `sma20_above_sma50` golden-cross proxy, `rsi_oversold` / `rsi_overbought` / `rsi_neutral`, `macd_bullish` / `macd_bearish`) |

**Wiring**: nested into `extract_live_features` as observation-only keys (`frame.market.fundamentals`, `frame.market.technicals`). Empty results are omitted (no littering the frame with empty dicts).

**Hard rails (pinned by tests)**:
- **Authority firewall**: feature builders import NOTHING from `broker_service`, `trading_bot_service`, `crypto_paper_trader`, `paper_trading_service`, `routes.broker`, `services.ml.{executors, roadguard, fast_veto, shadow_wiring, pipeline, broker_wire}`, `services.alpha_decision_log`, `ai_core.kill_switch`. Pinned by source-level regex check.
- **No verdicts**: source contains no `BUY` / `SELL` / `NO_TRADE` / `Verdict.*` / `set_active` / `promote_now` / `place_order` / `broker.execute`. Pinned by static substring check.
- **Equity-only firewall on fundamentals**: crypto / unknown / blank lanes return `{}` (no garbage P/E for digital assets). Pinned in 5 tests.
- **Never raises**: provider exception, `None` return, empty dict, blank symbol — all yield `{}`. Pinned in 6 tests.
- **Strategist vector dim unchanged**: `_FEATURE_DIM == 10` invariant test pinned. Static check that `_build_features` source does NOT read `fundamentals` / `technicals`. Existing artifact compatibility preserved — these features are observation-only today; available for the next retrain.

**Verified**:
- `tests/test_ml_feature_builders.py`: **45 passed** in 1.45s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 72.
- Full pytest: **2883 passed, 0 failed** (was 2838 + 45 new tests).
- Live smoke: `extract_live_features("AAPL", "equity")` returns nested `fundamentals` (19 numerics + 5 band flags) and `technicals` (9 indicators + 10 flags) on top of the existing flat keys (`broker_uptime`, `intraday_progress`, …) — no shadowing.
- Backend boots clean.

**Aligns with**: dual-stack architecture, adversarial layering, RoadGuard separation, shadow-first rollout, artifact-based retraining flow. Avoids the "single hardcoded brain" failure mode the user spent weeks removing from Camaro.

### Pre-4E broker-call contract net (2026-05-09)

**Operator-mandated stabilization pass** before the riskiest slice. Step 4E is **PAUSED** until this safety harness exists. Goal: pin the observable contract of the broker call surface so any future move/refactor that drifts it fails loud and fast.

**New file** `tests/test_trading_bot_broker_contract.py` — **14 tests, 0.78s**, fully mocked at the boundary (zero real broker calls, zero Mongo, zero env mutation).

**Snapshot scope** (per operator brief):
1. **Broker call ORDER**: `db.broker_connections.find_one` → `_get_user_broker` → `_get_or_refresh_client` → `place_order`. Pinned by spy log assertion.
2. **place_order kwargs SHAPE**: `{symbol, qty, side=lower(), order_type="market", time_in_force="day"}`. Pinned by `call_args.kwargs` exact match.
3. **Sync-call discipline**: `place_order` is wrapped in `asyncio.to_thread`. Direct-await regression would fail the test.
4. **Error paths** (every short-circuit branch):
   * `_db is None` → `{"error": "DB unavailable"}` (no broker import attempted — tripwire monkeypatched).
   * No broker connection → `{"error": "No broker connected for live trading"}`.
   * Falsy `place_order` result → `{"error": "Broker rejected order"}`.
   * Any exception → `{"error": f"Live execution failed: {e}"}` (sanitized; raw exception never escapes).
   * Unknown mode → `{"error": "Unknown bot mode: <mode>"}`.
5. **Return shapes** (success): `{"status": "filled", "broker_order_id", "symbol", "side", "qty"}` exact match.
6. **Paper-mode firewall**: paper path passes `side.upper()`, forwards SL/TP, AND must NOT touch `broker_connections` or import `routes.broker`. Pinned by spy + tripwire.
7. **Risk-guard pre-flight**: runs strictly BEFORE any broker lookup; adjusted qty propagates to `place_order`. `_apply_bot_risk_guards` independently pinned: DB-None fail-open, halve-on-trip with floor 1.0, exception fail-open with original qty preserved.

**Synthetic regression smoke test** (verified): replacing `side=side.lower()` with `side.upper()` in the live branch failed 2 contract tests by name; restoring → all 14 green again.

**Wired into `make lint-fast`**: net now runs as part of the broader Phase 6 architectural-invariant bundle (was 119 tests, now **133** — +14, sub-second runtime preserved at 0.95s).

**Hard rails honoured**:
- No broker code moved.
- `BROKER_LIVE_ORDER_ENABLED` unchanged (stays `false`).
- Phase 6 promotion stays blocked.
- No env mutation, no schema change, no allowlist additions.

**Step 4E remains PAUSED** until operator command. The contract net is now the gate it must clear.

### Step 4D — `trading_bot_service.py` decision-rules extraction (2026-05-09)

**Micro-phase 4 of 5**. Pure decision rules only — zero behaviour change. No DB, no broker, no mutation, no kill-switch state changes. Skip/block reasons unchanged.

`services/trading_bot_service.py`: 1402 → **1338 lines** (-64).

**Moved** to `services/trading_bot/_decision_rules.py` (108 lines):
| Function | Authority |
|---|---|
| `get_total_exposure` | Sum USD-notional size across open positions. Pure. |
| `get_open_trade_count` | `len(open_positions)`. Pure. |
| `get_sector_exposure` | Sector-share ratio (0.0–1.0). Pure. |
| `apply_portfolio_constraints` | Eligibility/headroom check returning a non-negative trade size (0 = block, "portfolio limits reached" reason left in caller). Pure. |

**Skipped from 4D scope** (deferred — not pure):
- `_check_kill_switch_and_drawdown` — calls `kill_switch.activate(...)` (state mutation; hybrid decision+activation).
- `_compute_adjusted_size`, `_resolve_qty`, `_extract_trade_size` — sizing math (out of "decision rules" scope per operator constraint).
- `_bot_from_config` — execution-prep synthetic-bot builder.

**Constants**: `MAX_CONCURRENT_TRADES`, `MAX_PORTFOLIO_EXPOSURE`, `MAX_SECTOR_EXPOSURE_PCT` imported from `_constants` directly inside the moved module. No existing test monkeypatches `tbs.MAX_*`, so this is byte-equivalent for all current callers and tests.

**Compatibility shim**: re-imported under public names so `test_portfolio_risk_engine.py` (`tbs.get_total_exposure`, `tbs.apply_portfolio_constraints`, etc.) and all in-module bare-name calls work unchanged.

**Verified**:
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- `test_portfolio_risk_engine.py`: 35/35 passed.
- Targeted superset (execute_signal + executor_lanes + roadguard + fast_veto + trading_bot + portfolio_risk + adaptive_sizing + drawdown): **202 passed**.
- Full pytest: **2824 passed, 0 failed** (identical pre/post extraction).

**Cumulative trading_bot_service.py**: 1575 → 1338 lines (-237, -15%) across 4A + 4B + 4C + 4D.

**Remaining**: 4E (broker calls — LAST, riskiest slice). Operator-mandated PAUSE before 4E.

### Step 4C — `trading_bot_service.py` data-access extraction (2026-05-09)

**Micro-phase 3 of 5**. Bot-CRUD helpers only — zero behaviour change. No broker calls, no decision logic, no sizing, no Mongo query / update / response shape changes.

`services/trading_bot_service.py`: 1506 → **1402 lines** (-104).

**Moved** to `services/trading_bot/_data_access.py` (171 lines):
| Function | Authority |
|---|---|
| `create_bot` | Insert bot doc (defaults OFF). |
| `_build_config` | Per-bot-type config validator/builder (no DB). |
| `toggle_bot` | Flip ``enabled`` flag. |
| `update_bot_config` | Merge config patch. |
| `delete_bot` | Delete by `(user_id, bot_id)`. |
| `get_user_bots` | List user's bots. |

**Compatibility shim**: re-imported under the original public names so external callers (`routes/trading_bots.py` does `from services.trading_bot_service import create_bot` etc.) and tests are unchanged. The moved helpers fetch the live `_db` via the existing `_resolve_db_for_shadow` getter (deferred import to break the circular dep — same pattern as 4B). Mongo queries, projections, sort orders, and return shapes are byte-equivalent.

**Verified**:
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- Targeted suite (execute_signal + executor_lanes + roadguard + fast_veto + trading_bot + portfolio_risk + adaptive_sizing + drawdown + webhook + grid): **207 passed**.
- Full pytest: **2824 passed, 0 failed** (identical pre/post extraction).
- Backend boots clean.

**Cumulative trading_bot_service.py**: 1575 → 1402 lines (-173, -11%) across 4A + 4B + 4C.

**Remaining**: 4D (decision rules), 4E (broker calls — LAST).

### Step 4B — `trading_bot_service.py` telemetry/receipts extraction (2026-05-09)

**Micro-phase 2 of 5**. Telemetry/receipts only — zero behaviour change, no broker calls, no decision logic, no DB query change.

`services/trading_bot_service.py`: 1554 → **1506 lines** (-48).

**Moved** to `services/trading_bot/_telemetry.py` (93 lines):
| Function | Authority |
|---|---|
| `fire_equity_shadow` | Research Shadow Layer fire-and-forget logger (kicks off `services.research_shadow_engines.fire_shadow` as a background task). Tier-3 firewall enforced by callee. |
| `record_kill_switch_outcome` | Step 8 of `execute_signal` — feeds broker outcome into the `ai_core.kill_switch` error window. Counts `{"error": ...}` dicts as failures. |

**Compatibility shim**: re-imported under the original private names (`_fire_equity_shadow`, `_record_kill_switch_outcome`) so the in-module call sites at lines 503/530 are unchanged. Deferred import of `_resolve_db_for_shadow` inside `fire_equity_shadow` breaks the circular dependency cleanly — the helper still routes through tbs's DB resolver.

**Verified**:
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- Targeted suite (execute_signal + executor_lanes + roadguard + fast_veto + kill_switch + shadow + trading_bot + portfolio_risk + adaptive_sizing + drawdown): **393 passed**.
- Full pytest: **2824 passed, 0 failed** (identical pre/post extraction).
- No receipt/audit shape changes. No broker behaviour changes.

**Remaining**: 4C (data access), 4D (decision rules), 4E (broker calls — LAST).

### Step 4A — `trading_bot_service.py` constants extraction (2026-05-09)

**Micro-phase 1 of 5** (operator-mandated slice-then-test cadence). Pure constants/types only — zero behaviour change.

**Target file** (per operator decision): `services/trading_bot_service.py` (1575 → **1554 lines**, -21). The originally-named `day_trade_executor.py` doesn't exist; `trading_bot_service.py` is the real top-level bot orchestrator.

**New package** `services/trading_bot/`:
| File | Lines | Authority |
|---|---:|---|
| `__init__.py` | 15 | Step 4 plan + scope notes |
| `_constants.py` | 54 | 7 module-level constants — pure values, no behaviour |

**Constants moved**: `ADAPTIVE_SIZING_ENABLED`, `_MIN_SCALED_QTY`, `MAX_POSITION_USD`, `MAX_PORTFOLIO_EXPOSURE`, `MAX_CONCURRENT_TRADES`, `MAX_SECTOR_EXPOSURE_PCT`, `BOT_TYPES`.

**Compatibility shim**: `trading_bot_service.py` does `from services.trading_bot._constants import *names*` so:
- External attribute access `tbs.MAX_POSITION_USD` still resolves (the names live in `tbs.__dict__` after `from import`).
- The monkeypatch pattern in `test_trading_bot_adaptive_sizing.py` (`monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", True)`) still mutates the binding bare-name reads inside `tbs` resolve through.

**Verified**:
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- Targeted suite (`test_portfolio_risk_engine` + `test_trading_bot_adaptive_sizing` + `test_execute_signal_usd` + `test_executor_lanes` + `test_drawdown_allocator`): **120 passed**.
- Full pytest: **2824 passed, 0 failed** (identical pre/post extraction).
- Backend boots clean. External `tbs.X` attribute access programmatically asserted.

**Remaining micro-phases**: 4B (telemetry/receipts), 4C (data access), 4D (decision rules), 4E (broker calls — LAST).

### Step 3 — `_start_schedulers()` strangler split (2026-05-09)

**`server.py` shrinks from 2004 → 1537 lines (-467).** Job registration moved verbatim to `services/scheduling/jobs.py`. Zero behaviour change: every job ID, interval, cron expression, `replace_existing`, and `next_run_time` kwarg preserved. Live scheduler still registers **61 jobs** (57 static + 4 dynamic ETL).

**New package** `services/scheduling/`:
| File | Lines | Authority |
|---|---:|---|
| `__init__.py` | 15 | Re-exports `register_all` |
| `jobs.py` | 127 | Flat schedule manifest — pure `scheduler.add_job(...)` calls |
| `_callbacks.py` | 267 | 14 inline async callbacks + production-incident rationale |

**`server._start_schedulers` retains** (unchanged authority):
- AsyncIOScheduler instantiation
- ``scheduler.start()``
- self-test scheduler wiring (`set_scheduler(scheduler)`)
- Health-panel error pump (`set_scheduler_boot_error`)
- Auto-seed Tier3 universe block (startup data seed, NOT job registration)

**Design decision — `server_mod` injection + callback extraction**:
- `register_all(scheduler, db, server_mod)` accepts the live `server` module so it can reach module-level callbacks (`_check_smart_orders`, `_run_grid_bots`, `_run_etl_job`, ~40 others) without a circular import.
- 14 closures that previously lived inline (`_run_ticker_abandonment_snapshot`, `_run_kraken_shadow_compare`, `_write_scheduler_heartbeat`, etc.) were lifted out to `_callbacks.py` as top-level `async def fn(db)` functions and wired via APScheduler's `args=[db]` — same runtime behaviour, much tighter `jobs.py`.

**Verified**:
- Boot: Schedulers started — same 61 jobs (57 static + 4 dynamic ETL).
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- `test_code_size.py`: 5/5 passed (jobs.py 127, _callbacks.py 267 — both well below core-governance preferred 600).
- Full pytest: **2824 passed, 0 failed** (identical pre/post split).
- Live `/api/admin/self-test`: scheduler check PASS, 61 jobs registered.

**Architecture Split Steps 1, 2, 3 now complete. Step 4 (split `day_trade_executor.py` ~1100 lines) remains.**

### Step 2 — `admin_ml_v2.py` authority-boundary split (2026-05-08)

**543-line route file → 7 small modules, largest 206 lines.** Zero behavior change. Compatibility shim preserves every existing import.

**New package** `routes/admin_ml/`:
| File | Lines | Authority |
|---|---:|---|
| `_routers.py` | 35 | APIRouter + DB handle (no endpoints) |
| `_auth.py` | 25 | Late-bound `require_admin` (test-monkeypatch compatible) |
| `__init__.py` | 60 | Side-effect imports + re-exports |
| `v2_pipeline.py` | 84 | Synthetic pipeline dry-run |
| `kanban.py` | 104 | RoadGuard pair + calibration kanban |
| `receipts.py` | 189 | Boot receipts + decision log + Phase 5b + Camaro |
| `safety.py` | 206 | Heartbeat + pipeline receipts + promotion checklist + artifacts + wedge alerter |
| **`admin_ml_v2.py`** | **62** | **Compatibility shim — re-exports public surface** |

**Compatibility preserved**:
- `route_registry.py`'s `from routes.admin_ml_v2 import (router, ml_safety_router, set_db, ...)` still works.
- Test sites that `monkeypatch.setattr(admin_ml_v2, "_require_admin", ...)` still work — `_auth.require_admin` resolves through `sys.modules["routes.admin_ml_v2"]` at every call.
- Direct test imports (`admin_ml_v2.list_artifacts_endpoint`, `admin_ml_v2.promotion_checklist`) re-exported.

**Verified**:
- 15 endpoint routes live-curl tested: every one returns 200 with auth, 401 without (same paths, same shapes).
- `lint-arch` 9 passed · `lint-fast` 119 passed · `lint-safety` 72 passed.
- Targeted suites: `test_artifact_inventory` + `test_promotion_checklist` + `test_phase5d_safety` + `test_wedge_alerter` + `test_code_size` = **60 passed**.
- Full pytest: **2824 passed, 0 failed** (same count as before split).
- `admin_ml_v2.py` removed from `PREFERRED_BASELINE` (62 ≤ 500 ceiling — ratchet worked exactly as designed).

### Architecture-as-policy — module-type aware code-size lint (2026-05-08)

**Two-tier policy** encoded in `tests/test_code_size.py`:

  - **Hard ceiling** (failing): unchanged — Python 800, frontend 500. Existing `ALLOWLIST` still works; no allowlist expansion.
  - **Preferred ceiling** (drift indicator): module-type aware, snapshot-based. Catches new code that violates authority-boundary expectations without breaking the green baseline.

**Module classification** by path predicate:

| Class | Preferred | Path |
|---|---:|---|
| `api-route` | 500 | `backend/routes/` |
| `ui-tile` | 400 | `frontend/src/components/admin/*Tile.jsx` |
| `ui-admin-component` | 500 | `frontend/src/components/admin/` |
| `ui-component` | 500 | `frontend/src/components/` |
| `core-governance` | 600 | `backend/services/` |
| `script` | 400 | `backend/scripts/` |
| `test` | 800 | `backend/tests/` |
| `default` | 800 | _fallback_ |

**Snapshot baseline** (`PREFERRED_BASELINE`): 59 existing breaches grandfathered with their line count + label. New files in those paths must respect the preferred ceiling. As old files shrink below their preferred ceiling, the lint fails with a "Remove from PREFERRED_BASELINE" message — ratcheting the bar tighter over time.

**Exempt patterns**: `migrations/`, `*_schema.py`, `*.schema.json`, `.egg-info/` — bypass both lints (no allowlist needed).

**Two new tests** added (lift count from 3 → 5):
  - `test_no_new_preferred_ceiling_breaches` — fails on new breaches OR stale baseline entries (file shrunk below ceiling, file moved/deleted).
  - `test_preferred_baseline_labels_match_classifier` — catches path drift (e.g. directory moves).

**Verified**:
  - All 5 tests pass (was 3); baseline cleanly captures today's breaches.
  - Synthetic drift smoke: new oversized api-route → fails with exact class+ceiling message; new oversized ui-tile → same; shrinking a baseline entry → "remove from baseline" message. Restoring → green.
  - `make lint-arch` 9 passed, `make lint-fast` 128 passed, `make lint-safety` 72 passed.
  - Full pytest: **2824 passed, 0 failed**.

**Set the bar for the upcoming Phase 6 prep splits**: `admin_ml_v2.py` (next), `_start_schedulers()`, `day_trade_executor.py` (last) — each must land within or move toward their preferred ceiling.

### Phase 5d/6 governance — `make lint-safety` (2026-05-08)

**Single-command verification of the entire Phase 5d/6 read-only safety surface**.

**Three-tier separation now established**:
  - `make lint-arch`   — architecture drift (~1s, 7 tests)
  - `make lint-fast`   — runtime invariants (~1.5s, 126 tests, includes lint-arch)
  - `make lint-safety` — Phase 5d/6 governance surface (~3s, 72 tests)

**`lint-safety` bundle**:
  - `test_phase5d_safety.py` — stale-model + feature-health + executor heartbeat
  - `test_wedge_alerter.py` — notification-only paging + cooldown
  - `test_artifact_inventory.py` — file-stat + env-read endpoint
  - `test_promotion_checklist.py` — 8-check aggregator
  - `test_retrain_alpha_models.py` — artifact-only dry-run scaffold

**Constraints honoured**: read-only, no broker calls, no env mutation, no joblib loading, no backend restart, no promotion actions. Stops at first failing file via `pytest -x`. On failure, prints the failing test node AND the full list of bundled layers so the operator immediately knows which surface regressed.

**Hook installer extended** with `--safety`:
  - `./scripts/install-pre-push-hook.sh --safety` → `make lint-safety` mode
  - Hook embeds `target=lint-safety` marker.
  - Re-install across all three modes (default / `--fast` / `--safety`) is idempotent.

**Intended uses**:
  - Pre-promotion verification.
  - Post-refactor sanity check.
  - Pre-deploy safety review.

**Verified**:
  - `make lint-safety`: 72 passed in 2.72s.
  - Synthetic failure smoke test: correctly stops after first failure, names the test node, lists all 5 bundled layers, exits non-zero.
  - All three speeds (`lint-arch` / `lint-fast` / `lint-safety`) work standalone and as hook installers.

### Phase 6 prep — Promotion Checklist endpoint + tile (2026-05-08)

**Read-only Phase 6 readiness aggregator**. Pulls from existing surfaces (artifact_inventory + executor_heartbeat + alpha_decision_log) and emits an 8-check status report with GREEN/YELLOW/RED.

**Module** (`/app/backend/services/ml/promotion_checklist.py`):
  - `build_checklist(db)` returns `{as_of, overall_status, ready_for_review, message, checks[8], active_artifacts, latest_artifacts, any_frozen, thresholds}`.
  - 8 checks: `manifest_present`, `latest_newer_than_active`, `active_on_disk`, `env_type_matches`, `heartbeat_not_frozen`, `no_flood_1h`, `active_age`, `shadow_receipts`.
  - **Severity**: RED wins overall → `ready_for_review=false`. YELLOW only → `ready_for_review=false` ("Items need review."). All GREEN → `ready_for_review=true` ("Ready for review.").
  - **Hard-rule invariants**: response NEVER contains "Promote now" / "promote_now" / "promote_action" / "set_active". No env mutation. No joblib load. No broker / executor / pipeline imports.

**Endpoint** (`/api/admin/ml/promotion-checklist` on `ml_safety_router`): admin-gated, GET-only.

**Frontend tile** (`MLPromotionChecklistTile.jsx`): rendered between Heartbeat and Artifacts on the Calibration Kanban.
  - 60s auto-refresh + manual refresh.
  - Per-row icon + status pill matching the colour scheme. Banner shows the overall message.
  - Footer surfaces active vs latest artifact filenames per type.
  - **Hard rule verified**: zero "Promote now" occurrences, zero promote/set-active/activate buttons in the tile.

**Tests**: `tests/test_promotion_checklist.py` (17 tests covering all 6 acceptance criteria + edge cases). Frontend testing_agent_v3_fork: 100% pass.

**Verified**:
  - 17/17 backend pytest pass.
  - Live smoke: 8 rows render (RED on heartbeat_not_frozen, GREEN on env_type_matches/no_flood/shadow_receipts=7, YELLOW on the rest because models dir + env unset). Banner reads "Blockers present — review the RED checks." / `overall=RED · ready_for_review=false`.
  - Full pytest: **2822 passed, 0 failed**.

### Phase 6 prep — ML Artifacts tile on Calibration Kanban (2026-05-08)

**Read-only Artifacts tile** (`frontend/src/components/admin/MLArtifactsTile.jsx`). UI-only surface; backend endpoint unchanged.

  - Renders below `MLHeartbeatTile` on the Calibration Kanban admin page.
  - `GET /api/admin/ml/artifacts` → sortable table (8 columns: Type, Filename, SHA, Timestamp, Size, Age, Manifest, Active).
  - **Default sort**: newest first by mtime. Click any column header to toggle/switch sort key.
  - **Visual cues** (read-only, no promotion power):
    * Green dot when `currently_pointed_to_by_env=true`.
    * Red `STALE` badge when active artifact has `age_hours > 72`.
    * Yellow `NO MANIFEST` badge when `manifest_present=false`.
    * Header banner `STALE ACTIVE ARTIFACT` when any active+stale row exists.
    * Cyan badge for `model_type=strategist`, violet for `auditor`, slate for `unknown`.
  - **Empty state**: "No model artifacts found." with the resolved `models_dir` path.
  - **Manual refresh button** + 60s auto-refresh interval.

**Hard-rule invariants** (verified):
  - NO promote button, NO set-active button, NO env-editing controls, NO write endpoints called, NO joblib loading.
  - All controls are read-only — only GET `/api/admin/ml/artifacts` is fetched.

**Verified**:
  - Backend: 14/14 pytest tests pass (`test_artifact_inventory.py`).
  - Frontend: testing_agent_v3_fork 100% pass — all UI elements render with proper `data-testid` attributes; sorting, refresh, badges, and banners all working; tile is read-only.
  - Live smoke (main agent): 4 seeded artifacts exercising all 4 cases (active+fresh+manifest, active+96h stale, inactive+no-manifest, unknown type) rendered correctly with green dot, red STALE badge, yellow NO MANIFEST badge, and `STALE ACTIVE ARTIFACT` header banner.
  - Test pollution cleaned up: empty `data/models/` and pristine `.env` after smoke test.

### Phase 6 prep — `GET /api/admin/ml/artifacts` (2026-05-08)

**Read-only artifact inventory**. File-stat + env-read only. Surfaces every `.joblib` under `/app/backend/data/models/` (override via `ALPHA_MODELS_DIR`).

**Module** (`/app/backend/services/ml/artifact_inventory.py`):
  - `list_artifacts(models_dir=...)` returns one dict per `.joblib`:
    `{filename, full_path, size_bytes, mtime, age_hours, sha_from_filename, timestamp_from_filename, model_type, manifest_present, manifest_path, currently_pointed_to_by_env, env_var}`.
  - Newest-first by mtime.
  - Recognises canonical retrain pattern `<type>_<sha>_<UTCstamp>.joblib`; legacy / non-canonical names tagged `model_type='unknown'`.
  - Flags `currently_pointed_to_by_env=true` only when `STRATEGIST_ARTIFACT` / `AUDITOR_ARTIFACT` env value's resolved path matches the file AND the model_type lines up. Cross-type mismatches and unknown rows always stay `false`.
  - **Module-level invariant**: never imports `joblib`, broker, executor, or pipeline modules — verified by source-level test.
  - Missing / unreadable directory returns `[]`, no crash.

**Endpoint** (`/api/admin/ml/artifacts` on `ml_safety_router`):
  - Admin-gated via `_require_admin`. Returns `{models_dir, items, count}`.
  - **Strict invariants**: no `joblib.load`, no env mutation, no broker/executor/pipeline calls, no promotion, no restart.

**Tests** (`/app/backend/tests/test_artifact_inventory.py`): 14 tests covering all 8 acceptance criteria — unauth blocked, admin 200, empty dir, strategist + auditor recognition, env-pointed flagging, no joblib load, no env mutation — plus cross-type-mismatch + unknown-type + ALPHA_MODELS_DIR override + source-level forbidden-import audit.

**Verified**:
  - 14/14 unit + 9 HTTP integration = **23/23 passing** via testing_agent_v3_fork.
  - Live curl: unauth → 401, admin → 200, empty `/data/models/` → `count: 0`, seeded artifact → correctly tagged.
  - Pre-existing pydantic `regex=` → `pattern=` deprecation warnings cleaned up at the same time (admin_ml_v2.py 5 sites).
  - Full pytest: **2796 passed, 0 failed**.

### Phase 6 prep — `scripts/retrain_alpha_models.py` (2026-05-08)

**Artifact-only retraining scaffold**. NO live promotion, NO env mutation, NO broker calls, NO executor wiring changes.

**Script** (`/app/backend/scripts/retrain_alpha_models.py`):
  - Reads `paper_trades` + `crypto_paper_trades` (outcome labels) + `alpha_decision_log` (feature reconstruction) within a configurable window.
  - Reconstructs the same 10-dim Strategist feature vector used at inference time (perception 6 sub-scores, avg confidence, shelly recall pos/neg ratios, intent encoded).
  - Strategist labels: realised P&L > 0 → BUY, < 0 → SELL, ≈ 0 → NO_TRADE. Auditor labels: losing trades → BLOCK.
  - Trains two `RandomForestClassifier` instances; reports `accuracy / precision_weighted / recall_weighted / ECE_10bin` plus class balance, train/test split, skip reasons.
  - Versioned outputs: `data/models/strategist_<git_sha>_<timestamp>.joblib` + `auditor_<...>.joblib` + `manifest_<...>.json` (source-tagged `alpha_retrain`).
  - Default mode is dry-run; `--write-artifact` persists files.
  - `--window-days N` (default 14), `--seed`, `--out-dir`, `--json /path` for machine-readable report.
  - Returns clean status `NOT_ENOUGH_ROWS` when `rows_used < 50`.
  - Returns clean status `NO_DB` when server db unimportable (no crash).

**Tests** (`/app/backend/tests/test_retrain_alpha_models.py`): 17 unit tests covering all 6 acceptance criteria — no rows → NOT_ENOUGH_ROWS, missing-features classification, versioned paths, dry-run no-write, stable schema, no env mutation — plus pure-helper coverage (label_from_outcome, class_balance, ECE, fit_classifier).

**Verified**:
  - 17/17 unit tests pass.
  - CLI smoke run against preview Mongo correctly returned NOT_ENOUGH_ROWS (172 trades scanned, 0 paired with decision logs in 1-day window).
  - testing_agent_v3_fork: 100% pass, zero issues.
  - Full pytest: **2782 passed, 0 failed**.

### Phase 5d guard rails — `make lint-arch` / `make lint-fast` + opt-in pre-push hook (2026-05-08)

**Goal**: protect the green-CI baseline as Phase 6 prep touches more files.

**`Makefile` targets** (`/app/Makefile`):
  - `make lint-arch` — direction-tuple drift + oversized files. 7 tests, ~1s.
  - `make lint-fast` — superset: lint-arch PLUS the Phase 6 invariant bundle (dual-stack, kill-switch, authority/risk-budget, RoadGuard pair, RoadGuard, fast-veto, executor lanes). 126 tests, ~1.5s total. Read-only, no Mongo writes, no broker calls. Stops at first failure via Make dependency chain.
  - `make help` documents both targets.

**Opt-in pre-push hook** (`/app/scripts/install-pre-push-hook.sh`):
  - `./scripts/install-pre-push-hook.sh` → installs `lint-arch` hook (default; quick drift check).
  - `./scripts/install-pre-push-hook.sh --fast` → installs `lint-fast` hook (broader Phase 6 safety check).
  - Hook embeds `target=...` marker so the operator can see which mode is active.
  - Re-install in either mode swaps targets idempotently. `--uninstall` removes only managed hooks.
  - Bypass once: `git push --no-verify`. **Not installed automatically.**

**Verified**:
  - `make lint-arch`: 7 passed in ~1s.
  - `make lint-fast`: 7 + 119 = 126 passed in ~1.5s.
  - Synthetic violation: detected at `lint-arch` step, dependency chain stops `lint-fast` early, exit non-zero.
  - Hook lifecycle: install (default) → install (--fast) → re-install plain → uninstall → uninstall-when-absent (safe no-op).
  - No allowlist additions, no behavior code changes.

### Phase 5d Wedge alerter — heartbeat-driven paging (2026-05-08)

**Notification-only** wedge alerter (`services/wedge_alerter.py`). NO promotion, NO enforcement, NO broker calls, NO scheduler restart, NO automatic remediation.

**Trigger rules**:
  - **T1** `any_frozen=true` for >`WEDGE_FROZEN_THRESHOLD_MIN` min (default 30) — tracked per-lane via `frozen_started_at`.
  - **T2** `top_clamp_block_reason == STRATEGIST_FEATURE_HEALTH_LOW` with `top_clamp_block_count > WEDGE_FEATURE_HEALTH_THRESHOLD` (default 50) in last 1h.

**Behavior**:
  - Posts ONE alert to `OPS_ALERT_WEBHOOK_URL` (Slack/Discord/generic-compatible JSON).
  - Cooldown: `WEDGE_ALERT_COOLDOWN_HOURS` (default 4h) suppresses duplicate `(lane, rule)` alerts.
  - Audit row written to `wedge_alerter_history` regardless of webhook success.
  - Missing webhook logs `OPS_ALERT_WEBHOOK_URL_MISSING` once per tick — never crashes.
  - Lane recovery clears the frozen-timer in `wedge_alerter_state.lane_frozen_started_at`.
  - Tier-3 firewall: only writes to `wedge_alerter_state` + `wedge_alerter_history`, NEVER to `alpha_decision_log` / `roadguard_*` / `phase5b_intents` / `paper_trades`.

**New scheduler job**: `wedge_alerter_tick` interval=5min — safe to schedule even without webhook (logs and returns).

**New admin endpoints** (`ml_safety_router`, all read-only):
  - `GET /api/admin/ml/wedge-alerter/status` — config flag + persisted state shape.
  - `GET /api/admin/ml/wedge-alerter/history?limit&lane` — audit rows.
  - `POST /api/admin/ml/wedge-alerter/run-now` — manual one-tick trigger.

**Tests**: `tests/test_wedge_alerter.py` (10 unit) + `tests/slow/test_wedge_alerter_api.py` (26 API). Combined Phase 5d suite: **79 passed** end-to-end. Hard stop on Phase 5b promotion remains in effect.

### Phase 5d cleanup — Tech-debt batch (2026-05-08)

**Direction-tuple cleanup**: replaced 5 literal direction tuples with the canonical `Verdict` enum / `canonical_ai_dir` helper. No allowlist additions.
  - `services/alpha_decision_log.py:131` → `(Verdict.BUY.value, Verdict.SELL.value)`
  - `services/ml/shadow_wiring.py:199, 248, 278` → `(Verdict.BUY.value, Verdict.SELL.value)`
  - `services/fast_veto_layer.py:286` → `canonical_ai_dir(council_action) != "UNKNOWN"`

**RoadGuardTile.jsx split** (was 525 lines, now 412): extracted 3 sub-components without behavior change.
  - `RoadGuardLaneCard.jsx` — per-lane summary card
  - `RoadGuardStatsRow.jsx` — `Metric` + `ScopeButton` primitives
  - `RoadGuardReasonBadge.jsx` — `DecisionBadge` + `ChecklistRow` primitives

**Tests**: `test_no_local_direction_tuples.py` (4/4) + `test_code_size.py` (3/3) now passing. Full pytest: **2755 passed, 0 failed**. UI smoke screenshot confirms RoadGuardTile renders identically.

### Phase 5d — Stale-model / Feature-health / Heartbeat safety patch (2026-05-08)

**Stale-Model Protection** (`services/ml/model_age.py`):
  - `evaluate_artifact(path)` returns `(stale, age_hours, max_age)`. Threshold via `MAX_MODEL_AGE_HOURS` env (default 72).
  - `ModelBootReceipt` extended with `model_age_hours: Optional[float]` and `stale: bool`.
  - When stale, Strategist/Auditor/Executor boot flips `ready=False` with reason `MODEL_STALE_OBSERVE_ONLY:age=Xh>max=72h`. Layer.decide() then returns NO_TRADE — no override flag exists.

**Feature-Health Weighting** (`services/ml/feature_health.py`):
  - `compute_feature_health(frame)` returns `(score, diagnostics)` with components: perception confidence (50%), market completeness (30%), data freshness (20%).
  - Strategist clamps `effective_confidence = raw_confidence * feature_health_score`.
  - Below `FEATURE_HEALTH_HOLD_THRESHOLD` (default 0.3) → forces NO_TRADE with reason `STRATEGIST_FEATURE_HEALTH_LOW`.
  - Reason flips to `STRATEGIST_CONFIRM_CLAMPED` / `STRATEGIST_FLIP_CLAMPED` when clamping is active.

**Executor Heartbeat** (`services/ml/executor_heartbeat.py`):
  - Thread-safe in-process tracker. Records every `pipeline.decide()` per lane.
  - Surfaces `last_pipeline_run_at`, `last_signal_at`, `signals_1h` (total runs), `buy_sell_1h`, `holds_1h`, `feature_health_avg`, `model_age_hours`, `model_stale`, `top_clamp_block_reason`, `top_clamp_block_count`, `frozen`.
  - Frozen heuristic: no run in `EXECUTOR_HEARTBEAT_FREEZE_AFTER_MIN` minutes (default 15) OR (buy_sell_1h=0 AND avg health < 0.3).

**ML Heartbeat tile** (`frontend/src/components/admin/MLHeartbeatTile.jsx`):
  - Top row of Calibration Kanban admin page. 30s auto-refresh + manual refresh button.
  - Shows lane / frozen / last_run / signals_1h / buy_sell_1h / holds_1h / health_avg / model_age / stale / top reason.
  - Strict read-only: no promotion buttons, no flip-enforcement controls, no broker-write controls.

**New admin endpoints** (`/api/admin/ml` prefix, separate `ml_safety_router`):
  - `GET /api/admin/ml/heartbeat` — per-lane state with frozen flag, model_stale flag.
  - `GET /api/admin/ml/pipeline/receipts?limit&lane` — recent decision-log entries.

**Tests**: `tests/test_phase5d_safety.py` (14 unit) + `tests/slow/test_phase5d_safety_api.py` (29 API). All 43 passing. **Hard stop on Phase 5b promotion remains in effect** — no broker wiring changes, no BUY/SELL enablement.

### Phase 5c — sklearn-backed Strategist + Auditor, live features, Camaro bridge

**Real Strategist + Auditor ML** (`services/ml/strategist/base.py`, `services/ml/auditor/base.py`):
  - `StrategistML` — `RandomForestClassifier`, 10-feature vector (perception scores + Shelly recall ratios + intent hint), 3-class output (BUY/SELL/NO_TRADE)
  - `AuditorML` — `RandomForestClassifier`, 8-feature vector, binary output (CONFIRM/DOWNGRADE — never flips direction)
  - Both artifact-gated via `STRATEGIST_ARTIFACT` / `AUDITOR_ARTIFACT` env vars
  - Falls back to balanced synthetic-trained placeholders when no artifact set
  - Boot receipts now show `placeholder_classifier` (was `placeholder_deterministic`)

**Live feature extraction** (`services/ml/feature_extraction.py`):
  - Pulls from existing services: `alpaca_equity_quotes`, `kraken_crypto_quotes`, `news_shock_service`, FRED snapshot via `fred_snapshots` Mongo collection
  - Wired into `shadow_wiring.run_shadow_pipeline` after `_build_feature_frame`
  - NEVER raises — every fetch is try/except, missing data falls into the safe middle of each model's training distribution
  - Caller-supplied `base` market dict wins over live data (synthetic dry-runs stable)

**Camaro → Shelly bridge** (`services/ml/camaro_shelly_bridge.py`):
  - Reads closed day-trade rows from `paper_trades` + `crypto_paper_trades` where `source="day_trade_scanner"` and `status="closed"`
  - Transforms each into a regime payload with `source="camaro"` (the canonical user-requested label)
  - Idempotent `prediction_id`: `camaro::<trade_id>::<closed_at>`
  - Calls `market_memory_service.save_regime` (Shelly's ChromaDB)
  - Audit trail in `camaro_shelly_bridge_log` collection
  - Admin endpoints: `POST /api/admin/ml/v2/camaro/bridge/run?lookback_hours=24`, `GET /api/admin/ml/v2/camaro/bridge/status?limit=10`

**Real .joblib artifacts** (`backend/scripts/train_ml_artifacts.py`):
  - Produces 8 artifacts under `/app/artifacts/ml/` (6 perception + Strategist + Auditor)
  - Wire into `.env` via `PERCEPTION_*_ARTIFACT` / `STRATEGIST_ARTIFACT` / `AUDITOR_ARTIFACT` paths to swap from in-process placeholders to disk-loaded models
  - Re-runs of the script use synthetic-realistic data; replace the training data builder when receipts mature into labeled data

**OPS_ALERT_WEBHOOK_URL**: wiring already correct in `services/ops_alerter.py`. Operator action: set `OPS_ALERT_WEBHOOK_URL=<slack-or-discord-webhook>` in prod `.env` and the alerter activates on next tick.

### Tests
**Total: 252 backend pytest passing** (208 + 13 phase5c features+camaro + 31 verified by testing agent for endpoints)

This iteration adds: `tests/test_phase5c_features_and_camaro.py`

## What's Implemented (continued)

### Phase 5b — broker wire (4-gate defense in depth) + Promotion Diff UI

**Phase 5b broker wire** (`services/ml/broker_wire.py`):
  - Sits AFTER `RoadGuard.evaluate` in `shadow_wiring.py`
  - Computes `will_actually_fire` from FOUR gates ALL closed by default:
    - Gate 1 — `EQUITY_EXECUTOR_ENFORCE_ENABLED` / `CRYPTO_EXECUTOR_ENFORCE_ENABLED` (per lane)
    - Gate 2 — `BROKER_LIVE_ORDER_ENABLED` (global kill-switch)
    - Gate 3 — `RISEDUAL_LIVE_EXECUTION=1` (legacy opt-in)
    - Gate 4 — Calibration Kanban readiness (lane must be `Eligible`)
  - `_dispatch_to_broker` is a **NO-OP placeholder body** — even with all 4 gates open, no broker SDK is contacted (intentional safety)
  - Persists ONE `phase5b_intents` row per signal with classification `SHADOW_ONLY | GATE_BLOCK | WOULD_HAVE_FIRED | FIRED`
  - Admin endpoints: `/api/admin/ml/v2/phase5b/{summary, recent}`
  - 30-day TTL on `phase5b_intents`

**Promotion Diff UI**:
  - Inline expandable RG verdict log on each Kanban LaneCard
  - "Show last 50 RG verdicts" button → fetches from existing `/api/admin/ml/v2/roadguard/decisions/recent?lane=X&limit=50`
  - Sticky-header table with When / Symbol / Decision / Gate / Reason
  - Colour-coded decisions (PASS=emerald, REDUCE=amber, BLOCK=rose)
  - data-testids on every row for testability

### 8-ML stack (Phase 0–4.5) — rebuilt from scratch this session
[Previous architecture preserved — see git history for details]

### Closed-loop RoadGuard pair (Phase 4 — user spec)
  - `EquityRoadGuard` + `CryptoRoadGuard` — each pinned to its lane via G00 LANE_MISMATCH
  - Independent envs (`ROADGUARD_EQUITY_*` vs `ROADGUARD_CRYPTO_*`)
  - Lane-specific decision collections: `roadguard_equity_decisions`, `roadguard_crypto_decisions`
  - Zero cross-contamination — pinned by tests

### Phase 5a — receipt-only ML pipeline wiring
  - `services/ml/shadow_wiring.py` runs the full 8-ML pipeline + lane-RG + Phase 5b on every executor signal
  - Writes ONE `alpha_decision_log` receipt + ONE `roadguard_<lane>_decisions` row + ONE `phase5b_intents` row per signal
  - **NEVER calls a broker. NEVER places an order.**

### Calibration Kanban (read-only admin tile)
  - Per-lane Shadow → Calibrate → Enforce promotion ladder
  - 6-item checklist: min_receipts, zero_contamination, zero_false_blocks, broker_health_stable, correct_collection, enforce_off
  - States: Eligible / Ready for Review / Blocked
  - Buttons display state, NEVER flip enforcement

### Alpha helpers
  - `alpha_decision_log` (8-stage receipts, 30d TTL)
  - `alpha_weight_calibrator` (sizes from `min(equity, cash)`, NEVER buying_power; $500 cap / 5 slots)
  - `alpha_daily_mandate` (≥20 RT/day target, 0.70→0.60 pressure curve, 15:30 ET no-open, 15:55 force-flat)

### Tests
**Total: 218 backend pytest passing** (60 pipeline + 16 lane-pair + 11 kanban + 15 broker_wire + 13 alpha + 16 boundary + 11 perception + 76 existing)

Test files: `test_ml_boundary.py`, `test_ml_perception.py`, `test_ml_pipeline_v2.py`, `test_roadguard_pair.py`, `test_calibration_kanban.py`, `test_broker_wire.py`, `test_alpha_helpers.py`

## Critical Invariants (do not break)
1. Alpha must size from `min(equity, cash)`. **NEVER** buying_power.
2. Daily mandate: ≥20 round-trips, flat-by-15:55-ET. Equities only.
3. Receipts validate strictly: `decision=NO_TRADE` requires `blocked_at` ∈ 8-stage chain.
4. `FAST_VETO_ENFORCE_ENABLED`, `ROADGUARD_EQUITY_ENFORCE_ENABLED`, `ROADGUARD_CRYPTO_ENFORCE_ENABLED`, `*_EXECUTOR_ENFORCE_ENABLED` all stay `false` until calibration proven.
5. **`BROKER_LIVE_ORDER_ENABLED=false`** until Phase 5a receipts prove clean.
6. `ROADGUARD_CAN_APPROVE=False` — both lane RGs NEVER create trades or increase size.
7. Shadow ML cannot veto/size/sell/execute/place_order/request_order. Sealed at the type level.
8. **Shelly cannot expose `.veto()`, `.size()`, `.execute()`, `.place_order()`, `.request_order()`** — pinned by `test_shelly_client_has_no_authority_methods`.
9. Closed-loop RG pair: an equity intent that arrives at `CryptoRoadGuard` (or vice versa) MUST fail LANE_MISMATCH at G00. No cross-contamination between `roadguard_equity_decisions` and `roadguard_crypto_decisions` collections — pinned by tests.
10. Shadow wiring NEVER calls a broker — `will_hit_live_broker` is hard-coded `False`.
11. `yfinance` is rejected as a market data source.
12. Shelly recall/remember failures NEVER abort the pipeline.

## Pending / Backlog

### P0 — Next session
- **Phase 5b** — Broker wiring behind enforce flags (only after 5a receipts look clean):
  - `EXECUTOR_ENFORCE_ENABLED` per lane; `BROKER_LIVE_ORDER_ENABLED=true` only after operator approval
  - Wire `compute_pressure().adjusted_threshold` into the THRESHOLD gate
  - Wire `should_force_flat()` into 15:55 ET force-flat job

### P1
- Real `Strategist` and `Auditor` ML implementations (replace placeholders)
- Real perception feature extraction from live market data (replace dummy training)
- Camaro Terminal UI (`/terminal/:symbol`) — blocked on broker decision (Webull vs Kraken Equities) and user-provided WebSocket keys
- Camaro→Shelly bridge: ingest Camaro trades into ChromaDB tagged `source="camaro"`
- `OPS_ALERT_WEBHOOK_URL` config in production
- Admin Health-dashboard tiles: ML pipeline, RG pair, mandate, Shelly

### P2
- `_start_schedulers()` strangler split into `services/scheduler_jobs/*.py`
- `SwallowedExceptionMonitor` to catch silent background-job failures
- Train + persist real `.joblib` artifacts for the 6 perception sub-models + 2 executor MLs
- Strangler split of large IP files (e.g., `crypto_paper_trader.py`)
- **Fundamental + Technical Feature Enrichment Engine** (saved 2026-05-09, NOT wired). File: `services/ml/features/_backlog/fundamental_technical_engine.py`. Combined dataclass output (PE/EPS, SMA5/20, slopes, volatility, regime tag, feature health). Open questions before wiring: where does `net_income`/`shares_outstanding` come from at runtime, does `regime_tag` replace or supplement existing tagger, does its `feature_health` override `services/ml/feature_health.py`. Operator decree: review against existing `fundamentals.py`/`technicals.py` overlap before any wiring.

### Backlog
- Alpha rollout 4 & 5: shadow → enforce after delta-log review
- GitHub overlay flake (parked)
- 16:30 ET daily mandate digest email

## Key Endpoints (new)
- `GET  /api/admin/ml/v2/boot-receipts` — per-layer ready/disabled
- `GET  /api/admin/ml/v2/decisions/summary?days=7` — receipts by stage
- `GET  /api/admin/ml/v2/decisions/recent?limit=50&blocked_at=&symbol=`
- `POST /api/admin/ml/v2/pipeline/decide?record=` — synthetic dry-run
- `GET  /api/admin/ml/v2/roadguard/pair-status` — closed-loop RG pair counts + last verdict
- `GET  /api/admin/ml/v2/roadguard/decisions/recent?lane=equity|crypto&limit=50`


## Phase 1 — MC-Aware AI Assistant Workspace (2026-05-17)

Built the dedicated 3-pane `/ai` route that replaces the floating chat
widget. The chat is now MC-grounded: it asks, displays, and never executes.

### What shipped
- **New route**: `activeView === 'ai'` renders `AIAssistantHub`
  (`frontend/src/components/hubs/AIAssistantHub.jsx`). Wired into
  `AuthenticatedShell` + `Navbar` (teal "AI" pill, key `ai`) +
  `MobileBottomNav` (chat button → /ai instead of floating modal).
- **Floating chat retired**: `RiseDualGPTChat.jsx` import removed
  from `AuthenticatedShell`. File kept on disk for now in case the
  drag/dock pattern is wanted elsewhere.
- **MC-aware hook**: `useMCContext()` polls 3 read-only surfaces
  every 30s (`/api/admin/mc-sidecar/status`,
  `/api/sovereign/honesty-mirror`, `/api/chat/mc/intents/recent`).
  Owner-only endpoints return a graceful 403; the hook exposes
  `ownerScope` so the UI can render an operator-only placeholder.
- **Slash command parser**: `frontend/src/utils/mcCommands.js`
  detects `/mc <verb>` lines and dispatches them to a single
  backend endpoint instead of hitting the LLM path.
- **MC card renderer**: `components/chat/MCCard.jsx` renders five
  card kinds (`mc_status`, `mc_mirror`, `mc_intents`, `mc_opine`,
  `mc_help`) inline as compact assistant bubbles.
- **Backend dispatcher**: `routes/chat_mc.py` exposes
  `POST /api/chat/mc/dispatch` (parses + routes to the right
  command handler) and `GET /api/chat/mc/intents/recent` (right-pane
  hydrate without a slash). Owner-gated commands use the canonical
  `role == "owner"` check.
- **Side-effect bug fix**: `routes/admin_mc_sidecar.py` and
  `routes/sovereign_honesty.py` were checking `user.get("is_owner")`
  which is never set on the user document. Replaced with the correct
  `role == "owner"` check; tests updated to match.

### Doctrine guard rails
- Chat can **ask** (`/mc status`, `/mc mirror`, `/mc intents`).
- Chat can **display** (renders MC's reply as a card).
- Chat can **opine** (`/mc opine NVDA` runs a fresh council
  hypothesis and returns the receipt; does NOT post-intent).
- Chat **cannot execute**. Intent emission still flows through
  `intent_bridge` from the consensus tick — chat is read-through.

### Tests
- `backend/tests/test_chat_mc_route.py` — 9 new tests covering
  help / status / mirror / intents / opine / unknown verb.
- 93 tests pass across the MC + chat + intent surface.

### Backlog from this phase (Level 2-4 ladder)
- **Level 2 ext**: add `/mc post-intent <hypothesis_id>` once user
  asks for write-capable surface (kept out of Phase 1 by design).
- **Level 3**: full tool-using agent (LLM autonomously calls
  `get_council_opinion`, `get_honesty_mirror`, etc.). Powerful but
  blurs authority — deferred until Phase 1 proves the read-only
  shape works in production.
- **Level 4**: WebSocket / SSE bridge from MC → chat so the
  assistant proactively pings the operator when the council flips.
- **Intent audit mirror**: `intent_bridge.emit_intent_sync` should

## Phase A — Open Trading Override (2026-05-17)

**Operator command:** "open for trading" — remove all promotion-tier
gates that prevent stacks from emitting and sizing intents. MC remains
the execution authority; the local pod stops pre-throttling.

### Root cause of "blocking everyone but Camaro"
The promotion gate ladder (`check_all_gates`) and the local sizer
(`compute_position_multiplier`) BOTH gated on accumulated live
history. Camaro had enough equity history to clear Tier 3; Alpha,
Chevelle, RedEye sat at Tier 1/2; crypto was blocked everywhere
because no stack had a long-enough crypto live record. With the
position multiplier collapsing to ~0 on low readiness, even good
council signals fired with $0 notional.

### Surgical changes (4 files, ~80 LOC)

1. **`risedual_core/risedual_core/ml/calibration.py::check_all_gates`**
   — returns `tier1/2/3.unlocked = True` flat. Legacy threshold
   logic preserved in `_check_all_gates_legacy` for the readiness UI.

2. **`ai_core/sizing.py::compute_position_multiplier`** — returns
   `MAX_POSITION_MULTIPLIER` flat. Legacy three-throttle math
   preserved in `_compute_position_multiplier_legacy`.

3. **`sovereign/mc_client.py::build_contribution_body`** —
   `live_trading_enabled = True` (was hardcoded False).

4. **`sovereign/intent_bridge.py::_build_emission_kwargs`** —
   stamps `execution_decision="ALLOW"` (was `OBSERVE_ONLY`).

### Configuration changes

| Setting | Was | Now |
|---|---|---|
| `backend/.env::RISEDUAL_EMIT_INTENTS_TO_MC` | unset (=0) | `1` |
| Mongo `system_settings.public_access.enabled` | `False` | `True` |
| `ALPHA_INPROCESS_SIDECAR` | `1` | unchanged |

### Tests updated (no regressions)

- `test_alpha_sovereign_sidecar.py` — `live_trading_enabled` asserts flipped.
- `test_intent_bridge.py` — `execution_decision` asserts flipped.
- `test_tier3_sizing.py` — legacy throttle tests retargeted at
  `_compute_position_multiplier_legacy`; added override test.
- `test_trading_bot_adaptive_sizing.py` — throttled-readiness test
  retargeted to assert override behavior (~13.08 not ~7.98).
- `test_execute_signal_usd.py` — same retarget (~$1308 not ~$800).

**Final: 3,592 backend tests pass, zero regressions.**

### What was deliberately NOT touched (still on, doctrine intact)

- RoadGuard equity + crypto pair (capital governors on MC side).
- Global kill-switch / firewall.
- Council penalty math (`HARD_CONFLICT_PENALTY`, `HOLD_BIAS_PENALTY`).
- `DIRECTIONAL_FLOOR = 55`, `HIGH_CONVICTION_OVERRIDE = 80`.
- Spread/slippage rejection.
- Toxic-spike autopsy.
- Compliance footer + risk disclosures.
- Cross-stack references in council math (Camaro/Chevelle/RedEye are
  PEER BRAINS in the consensus — removing them would break the Honesty
  Patch).

### How to revert
Replace the body of `check_all_gates` with `return _check_all_gates_legacy(...)` and
`compute_position_multiplier` with `return _compute_position_multiplier_legacy(readiness)`.
Set `live_trading_enabled` back to `False`, `execution_decision` back to `OBSERVE_ONLY`,
`RISEDUAL_EMIT_INTENTS_TO_MC=0`, flip the Mongo `system_settings.public_access` to False.


  write to `mc_intents_audit` so `/mc intents` returns live receipts
  instead of the doctrine note. Trivial — deferred until the
  `RISEDUAL_EMIT_INTENTS_TO_MC` flag is flipped on.

## Phase A — Diagnostic Trace Instrumentation (2026-05-17)

Added a single 8-char trace_id stamped onto every emitted intent so
one signal can be followed end-to-end across the brain↔MC boundary.
Designed to surface the failure point per operator's directive:
"the first missing line is the failure boundary."

### Trace boundaries (logged with `[xxxxxxxx]` prefix)

| Layer | Log line | File |
|---|---|---|
| Brain decides to emit | `ALPHA_<lane>_INTENT_CREATED` | `sovereign/intent_bridge.py` |
| HTTP about to fire | `MC_<lane>_POST_SENT` | `sovereign/mc_client.py::post_intent` |
| MC accepts | `MC_<lane>_RESPONSE_OK verdict=… executable=…` | `sovereign/mc_client.py::post_intent` |
| MC rejects | `MC_<lane>_RESPONSE_FAIL err=…` | `sovereign/mc_client.py::post_intent` |
| MC accepts but won't fire | `MC_<lane>_NON_EXECUTABLE reason=…` | `sovereign/mc_client.py::post_intent` |
| Local crypto adapter | `CRYPTO_ADAPTER_REACHED` | `services/executors/crypto_executor.py` |
| Local equity adapter | `EQUITY_ADAPTER_REACHED` | `services/executors/equity_executor.py` |
| Broker outcome | `<lane>_BROKER_{SUBMITTED,SKIPPED} reason=…` | each lane executor |

### Lane classifier
`/USD`, `/USDT`, `/USDC`, `-USD`, and `BTC/ETH/SOL/...` symbols are
tagged `CRYPTO`; everything else `EQUITY`. Defined once in
`intent_bridge._classify_lane` and `mc_client._classify_lane_for_log`.

### Trace propagation
- `_build_emission_kwargs` mints a trace_id if caller doesn't pass one.
- `build_intent_body` accepts an optional `trace_id` and stamps it on
  the wire (MC server is additive-safe — extra fields ignored if
  schema doesn't know about them).
- `post_intent` extracts trace_id from the body and uses it for
  `MC_POST_SENT` / `MC_RESPONSE_*` log lines.
- Lane executors mint their own trace_id if the signal carries none,
  and write `trace_id` back onto the result dict so upstream chains.

### Operator grep recipes
```bash
# Has the brain decided to emit ANYTHING since boot?
grep "ALPHA_.*_INTENT_CREATED" /var/log/supervisor/backend.*.log

# Has any crypto intent reached MC?
grep "MC_CRYPTO_POST_SENT" /var/log/supervisor/backend.*.log

# Has MC ever returned non-executable on crypto?
grep "MC_CRYPTO_NON_EXECUTABLE" /var/log/supervisor/backend.*.log

# Follow ONE trace end to end:
grep "<8-char-id>" /var/log/supervisor/backend.*.log
```

### Tests
- `test_trace_pipeline.py` — 9 new tests covering trace mint,

## Phase A2 — Live-Flow Wiring into Survival Layer (2026-05-17)

The portable survival kernel from Phase A1 is now in the critical
path of every directional emission. Brain side runs a local mirror
of MC's gate before POSTing; broker side verifies the HMAC-signed
receipt before submitting any order.

### Brain side (`sovereign/intent_bridge.py`)
- `_build_emission_kwargs` now constructs an `IntentEnvelope` via
  `sidecar_build_intent` and runs `mc_canonical_gate()` as a local
  pre-flight mirror.
- Three log boundaries added:
  - `SURVIVAL_PREFLIGHT_OK` (approved, receipt attached)
  - `SURVIVAL_PREFLIGHT_SOFT_DENY` (failed, but soft mode lets it
    proceed — current default)
  - `SURVIVAL_PREFLIGHT_BLOCK` (failed under `RISEDUAL_SURVIVAL_ENFORCE=1`,
    returns None, no MC POST)
- The signed receipt rides on the outgoing payload via a new
  `mc_receipt` field in `build_intent_body`.

### Broker side (`services/executors/crypto_executor.py`,
`services/executors/equity_executor.py`)
- Before delegating to `_core_execute`, check `signal["mc_receipt"]`:
  - Present + valid signature → `*_RECEIPT_VERIFIED` log + proceed
  - Present + invalid → `*_RECEIPT_INVALID` log; if
    `RISEDUAL_REQUIRE_MC_RECEIPT=1`, skip with `RECEIPT_<reason>`
  - Absent + REQUIRE on → skip with `RECEIPT_MISSING`
  - Absent + REQUIRE off → graceful degrade, log, proceed

### Mode matrix

| `SURVIVAL_ENFORCE` | `REQUIRE_MC_RECEIPT` | Behavior |
|---|---|---|
| 0 (default) | 0 (default) | **Observe** — every boundary logs, nothing blocks. Use this for prod rollout. |
| 1 | 0 | Brain refuses to emit on failed pre-flight. Broker still graceful. |
| 0 | 1 | Brain emits everything. Broker refuses orders without valid receipt. |
| 1 | 1 | **Full hard-block** — both sides enforce. End state. |

### Promotion path
1. Ship with both flags off (current state — wiring is observable in
   logs but never blocks).
2. Watch logs for `SURVIVAL_PREFLIGHT_SOFT_DENY` — if Camaro's 60%
   dampener produces a flood of below-floor denies, that's exactly
   what we want to see *before* it ships to live.
3. Flip `RISEDUAL_SURVIVAL_ENFORCE=1` once the log story is clean.
4. Flip `RISEDUAL_REQUIRE_MC_RECEIPT=1` only after MC is actually
   signing receipts on its end (this requires changes on the MC
   side — until then, brain-emitted receipts are the only source).

### Tests
- `test_survival_live_wiring.py` — 9 new tests covering the bridge
  side (receipt attach / soft-deny / hard-block), broker side (valid
  receipt / tampered receipt / missing receipt / graceful degrade),
  and the end-to-end chain.
- **3,622 backend tests pass, zero regressions.**

### What's now possible
- Grep `SURVIVAL_PREFLIGHT` to see every kernel verdict in real time.
- Grep `*_RECEIPT_VERIFIED` to confirm broker is checking signatures.
- Any sidecar with `local_execution_authority = True` (via tampered
  envelope) hits `SIDECAR_LOCAL_AUTHORITY_FORBIDDEN` in the kernel.
- Stale code with a mismatched policy_hash hits `POLICY_HASH_MISMATCH`
  — surfaces deploy skew automatically.


  propagation, lane tagging, MC_POST_SENT / RESPONSE_OK /
  NON_EXECUTABLE / RESPONSE_FAIL boundaries.
- `test_executor_lanes.py` — 3 tests updated to tolerate the new
  `trace_id` field on result dicts.

**Final: 3,608 backend tests pass, zero regressions.**




## Test Credentials
See `/app/memory/test_credentials.md`.

## 2026-08-11 — Alpha Day Trader (Phase A+B) + Chasing/RTH fixes

**What shipped (verified):**
- **Chasing filter root-cause fix** (`_intraday_move_pct`): both legs now come from the same `market_daily` response — previously mixed providers produced impossible readings (PLTR reported +27.6%). Added ±50% sanity cap that fails open on suspected split/dividend desyncs. **72 skips → 0 in preview forensic before restart; watch after next 5-min tick.**
- **RTH-only session gate** in `maybe_route_live`: outside 9:30–16:00 ET or weekends → clean `market_closed` skip (Public.com API rejects fractional MARKET orders in extended hours). Handles DST/EST switch, weekday-in-ET calculation, boundary minutes. Override via `PUBLIC_LIVE_RTH_ONLY=0`.
- **Alpha Day Trader** (`services/alpha_day_trader.py`, ~700 LOC) — new intent-producing intraday strategy:
  - Ranker (relvol + pct_change + vol_accel + spread + above-VWAP)
  - Pattern engine: VWAP_RECLAIM, HOD_BREAK, BREAKOUT, PULLBACK, MOMENTUM_REACCELERATION
  - Trigger watcher freezes `confirmation_price` on crossover
  - Level 2 confirmation as MODIFIER only — `None → 0.50`, **never blocks**
  - Setup dedup: same market move → one `setup_id` (5% price band per symbol×setup_type×day)
  - Hands off to existing `maybe_route_live` — Seat/Risk/RoadGuard/EntryTiming path preserved
  - Lifecycle counters (`candidates_seen → setups_created → triggers → intents_created → broker_submitted → filled`)
  - Independent env switches: `RISEDUAL_ALPHA_DAYTRADER_SCAN`, `RISEDUAL_ALPHA_DAYTRADER_EXECUTE` (both OFF by default)
- Admin routes: `GET/POST /api/admin/alpha-daytrader/{counters,setups,outcomes,tick}`
- Scheduler: 5-min interval job `alpha_day_trader` (no-ops when SCAN switch off)
- Existing `day_trade_scanner` **left running** during rollout — no interruption to current trade activity
- 17/17 unit tests passing

**How to activate on prod:**
1. Set `RISEDUAL_ALPHA_DAYTRADER_SCAN=1` — observation only (creates setups/counters, no trades)
2. Confirm setups/triggers appear at `/api/admin/alpha-daytrader/counters`
3. Set `RISEDUAL_ALPHA_DAYTRADER_EXECUTE=1` — intents flow into the existing execution pipeline
4. Watch `trigger → intent` conversion; anything != 1:1 means a hard-safety condition triggered

**Not yet built (Phase C):**
- Break-even auto-stop bump after +1R
- Full outcome journal with latency instrumentation (signal→trigger→intent→broker→fill ms)
- `AlphaDayTraderPanel.jsx` Mission Control panel

**Files:**
- NEW `backend/services/alpha_day_trader.py`
- NEW `backend/routes/admin_alpha_daytrader.py`
- NEW `backend/tests/test_alpha_day_trader.py`
- MOD `backend/services/public_equity_live_executor.py` — chasing fix + RTH gate
- MOD `backend/route_registry.py` + `services/scheduling/jobs.py` — wiring


## 2026-08-11/12 — Alpha Day Trader Phase C SHIPPED

**Storage split (Mongo constraint honored):**
- Raw lifecycle events + latency samples + cross-scanner locks → SQLite `/app/backend/data/alpha_hot_store.sqlite` (WAL mode, 14-day retention)
- Compact operator-facing docs → Mongo only: `alpha_active_setups`, `alpha_outcomes`, `alpha_daytrader_counters`, `alpha_pattern_rollups`, `alpha_runtime_state`
- `alpha_setup_observations` collection retired; all writes routed to hot store

**Full lifecycle journal:**
- Every stage transition (detected → armed → triggered → intent → broker → filled/rejected) captured
- 4 latency samples per successful setup: `signal_to_trigger`, `trigger_to_intent`, `intent_to_broker`, `broker_to_fill`
- Setup timeline endpoint: `GET /api/admin/alpha-daytrader/setup/{setup_id}/timeline`
- Executor rejection reason is pulled from `intent_skip_log` — no fabricated local reasons

**Cross-scanner dedup:**
- `alpha_hot_store.try_acquire_symbol_lock()` mutex at the execution boundary
- Both `alpha_day_trader` and `day_trade_scanner` now acquire before calling `maybe_route_live`
- Fails open on lock-service errors (never blocks a legit trade)
- 120s TTL; reentrant for same setup_id + source

**Break-even protection:**
- New `alpha_breakeven.py` service, 1-minute scheduler job
- Arms at +1R measured against frozen (entry - stop) distance
- Configurable buffer via `ALPHA_BREAKEVEN_BUFFER_BPS` (default 5bps)
- Stops may only tighten, never widen
- Break-even event recorded to hot-store setup timeline

**Pattern performance rollups:**
- `alpha_pattern_performance.compute_rollups()` reads resolved outcomes → per-pattern summary
- Metrics: sample_n, win_rate, avg_win/loss_r, expectancy_r, profit_factor, median MFE/MAE, avg slippage bps, sample_confidence tier
- 15-minute scheduler job
- Endpoints: `GET /api/admin/alpha-daytrader/pattern-performance`, `POST .../recompute`
- Confidence tiers (low<10, medium<30, high≥30) — never auto-disable a pattern

**Runtime activation controls:**
- `alpha_runtime_state.py` — Mongo-backed operator overrides
- Precedence: override → env var → False
- Endpoints: `GET/POST /api/admin/alpha-daytrader/runtime`
- Panel toggles change actual runtime behavior, not just display env values

**Frontend Mission Control panel:**
- `AlphaDayTraderPanel.jsx` under Admin → Alpha Day Trader tab
- Lifecycle counters (candidates → setups → armed → triggers → intents → broker)
- Conversion ratios (color-coded; `trigger_to_intent < 95%` shows amber)
- Pattern performance table (sortable by expectancy)
- Runtime toggles + force-tick + recompute rollups actions
- Recent setups + recent outcomes side-by-side

**Preview env activation:**
- `RISEDUAL_ALPHA_DAYTRADER_SCAN=1` and `_EXECUTE=1` set in `/app/backend/.env`
- Verified via `/api/admin/alpha-daytrader/runtime` and manual tick
- **Prod requires operator to set the same 2 env vars + redeploy**

**Test coverage:**
- 27/27 tests passing (17 Phase A+B + 10 Phase C)
- Hot store: record/read events, latency, dedup lock (blocking + reentrant + release)
- Pattern rollup: expectancy math, PF, confidence tiers
- Break-even: R-multiple math, invalid-stop rejection

**Not shipped (Phase D backlog):**
- Edge Engine (context-aware confidence modifier — pattern × regime × time × relvol × spread × VWAP × ticker class)
- Slippage + MFE/MAE writers into `alpha_outcomes` — the schema is there, callers haven't wired the values yet (will happen when a real position closes through the exit monitor)


## 2026-08-12 — Alpha Phase D: Market Regime (HMM) + Edge Engine

**Market Regime service (`services/market_regime.py`):**
- 4-state Gaussian HMM (hmmlearn 0.3.3) fit on ~180 days of SPY daily bars
- 5 features per bar: log-return, 5-day realized vol, volume z-score, body-to-range ratio, gap size
- States discovered statistically; labels assigned post-hoc from centroids (trend_up / momentum_expansion / risk_off / choppy_meanrevert)
- Non-blocking guarantee: missing data / untrained model / scoring failure → `label="UNKNOWN"`, callers continue
- Fitted model pickled to SQLite (`regime_models` table) — survives restarts
- Compact `alpha_regime_state` doc (single row) in Mongo for the UI
- Scheduler: `_run_alpha_regime_snapshot` every 30 min, `_run_alpha_regime_refit` every 24h

**Edge Engine (`services/alpha_edge_engine.py`):**
- Modifier table per spec: DISCOVERING(<10 samples)→1.00 · +0.50R→1.15 · +0.20R→1.07 · 0R→1.00 · −0.20R→0.90 · <−0.20R→0.75
- Never a hard gate — new setups always get neutral treatment
- Rollup key: `(pattern × regime)` with time_bucket / rvol_bucket / spread_bucket stamped on outcomes
- Writes to `alpha_edge_rollups` (compact Mongo docs); raw event feed still lives in SQLite hot store
- Confidence cap at 1.0 preserved
- 20-minute scheduler rollup

**Wired into `run_alpha_day_trader_tick`:**
- After trigger, before executor handoff: fetch regime, look up (pattern×regime) edge, apply modifier to `intent.confidence`
- Buckets + edge_modifier + edge_state stamped on every `alpha_outcomes` row (so tomorrow's rollups have context)
- Lifecycle event captures raw + modified confidence so operator can audit the delta

**Admin endpoints (4 new):**
- `GET  /api/admin/alpha-daytrader/regime`
- `POST /api/admin/alpha-daytrader/regime/refit` (owner)
- `GET  /api/admin/alpha-daytrader/edge`
- `POST /api/admin/alpha-daytrader/edge/recompute` (owner)

**Mission Control panel additions:**
- Regime tile: current label · confidence % · posteriors bar
- Edge modifiers table by (pattern × regime): samples · expectancy R · modifier · state pill

**Verified live:**
- HMM fit on real SPY (160 samples), current regime = choppy_meanrevert @ 100% posterior
- Refit endpoint working
- Tick runs cleanly with regime + edge in the loop
- 39/39 unit tests passing (17 A+B + 10 C + 12 D)

**Files:**
- NEW `backend/services/market_regime.py` (HMM + persistence)
- NEW `backend/services/alpha_edge_engine.py` (edge lookup + modifier table)
- NEW `backend/tests/test_alpha_phase_d.py`
- MOD `backend/services/alpha_day_trader.py` (regime + edge wired into tick + outcome context stamping)
- MOD `backend/routes/admin_alpha_daytrader.py` (4 new endpoints)
- MOD `backend/services/scheduling/jobs.py` (3 new jobs)
- MOD `backend/server.py` (3 new job entrypoints)
- MOD `frontend/src/components/admin/AlphaDayTraderPanel.jsx` (regime + edge sections)
- MOD `backend/requirements.txt` (hmmlearn==0.3.3)

**Prod redeploy needed to activate Phase D on live.**


## 2026-08-12 — Alpha Phase D+ (Measurement Loop Closed)

**Fill economics wired end-to-end** (`services/alpha_fill_writer.py`):
- `track_open_excursions()` — 1-min job: walks open Alpha positions, updates `peak_price` / `trough_price` on `equity_live_trades`
- `resolve_closed_outcomes()` — 2-min job: on close, computes `realized_r`, `mfe_r`, `mae_r`, `slippage_bps`, `entry_fill_price`, `exit_fill_price` and updates the matching `alpha_outcomes` doc (looked up by `setup_id`). Idempotent via `outcome_resolved` flag.
- `slippage_bps = (entry_fill - trigger_price) / trigger_price × 10000` — uses the frozen `confirmation_price` from the intent as the reference
- Setup timeline gets an `outcome_resolved` event with the full metrics payload

**Edge Engine now consumes ONLY resolved samples:**
- Rollup query filters to rows with `realized_r` set → observed-but-not-measured rows never poison expectancy
- Rollup key upgraded to `(pattern × slow_regime × fast_regime)`
- Lookup falls back through 2 layers: fine-grained → `(pattern × slow)` → neutral DISCOVERING
- Requires ≥10 samples per bucket to leave DISCOVERING

**Fast intraday regime layer** (`services/fast_intraday_regime.py`):
- Rules-based classifier over current-session SPY features (today's return vs prev close, today_range/atr20, volume run-rate, body-to-range ratio)
- 6 labels: `momentum_ignition_up/down`, `volatility_expansion`, `risk_off`, `trend_up/down`, `session_chop`
- Non-blocking — missing bars → `UNKNOWN`
- Complements (never overrides) the slow SPY-daily HMM
- Scheduler: `_run_alpha_fast_regime` every 5 min
- Verified live: today's SPY produces `session_chop` (+0.15%, tight range)

**Panel honesty upgrade:**
- Slow + fast regime shown side-by-side, not merged
- Slow regime posterior now labelled "Model preference" with italic caveat: "This is the fitted model's posterior preference for its own learned states, not the objective probability that the market is in that regime"
- Edge modifiers table gets `slow_regime` + `fast_regime` columns
- Preserves the (∼15%) modifier scale — no automatic size reductions from regime transitions (informational only)

**Endpoints (3 new):**
- `GET  /api/admin/alpha-daytrader/fast-regime`
- `POST /api/admin/alpha-daytrader/fast-regime/refresh` (owner)
- `POST /api/admin/alpha-daytrader/fills/resolve` (owner)

**Test coverage:**
- 48/48 unit tests passing (17 A+B + 10 C + 12 D + 9 D+)
- Fill metrics: realized_r, MFE, MAE, slippage, missing-excursion fallback, unknown-risk flag
- Fast regime: all 6 labels have explicit classifier tests

**Files:**
- NEW `backend/services/alpha_fill_writer.py`
- NEW `backend/services/fast_intraday_regime.py`
- NEW `backend/tests/test_alpha_phase_d_plus.py`
- MOD `backend/services/alpha_day_trader.py` (fast_regime stamped on outcomes + intent reason)
- MOD `backend/services/alpha_edge_engine.py` ((pattern × slow × fast) key + 2-tier fallback lookup + resolved-only filter)
- MOD `backend/routes/admin_alpha_daytrader.py` (3 new endpoints)
- MOD `backend/services/scheduling/jobs.py` + `server.py` (4 new scheduled jobs)
- MOD `frontend/src/components/admin/AlphaDayTraderPanel.jsx` (slow + fast side-by-side, posterior wording)

**Not yet:** operator-adjustable modifier weighting — deferred per your instruction until real fill economics accumulate.


## 2026-08-12 — Alpha Phase E: Resolved Trade Report + Setup Timeline + Discovery Radar

**Order shipped per operator instruction: 1 → 2 → 3.**

### 1. First Resolved Trade Report (`services/alpha_trade_report.py`)
- Reads `alpha_outcomes` (Mongo, one doc per unique resolved setup)
- Pulls latency samples from SQLite hot store on demand — no Mongo duplication
- Filters to rows with `realized_r` set (`only_measured=True` default) so incomplete outcomes never pollute totals
- Per-row fields: symbol, pattern, slow_regime, fast_regime, detected_price, confirmation_price, entry_fill_price, exit_fill_price, realized_r, mfe_r, mae_r, entry_slippage_bps, exit_slippage_bps, realized_pnl_usd, close_reason, intent_to_broker_ms, edge_agreement
- **Edge agreement**: for each resolved trade, record whether the Edge Engine's `edge_state` at entry (POSITIVE/NEGATIVE) matched the sign of `realized_r`. AGREE / DISAGREE / N/A (for DISCOVERING/FLAT). Observation only — never gates.
- Header totals: count, win rate, avg realized R, gross P&L, median entry slippage, edge-agreement ratio
- `GET /api/admin/alpha-daytrader/resolved-trades?limit=50&include_unmeasured=false`

**Extended fill economics** (`alpha_fill_writer._resolve_metrics`):
- Renamed `slippage_bps` → `entry_slippage_bps` (against frozen `confirmation_price`)
- Added `exit_slippage_bps` — measured against stop_price for stop hits, target_price for target hits, omitted for time-based exits
- Added `realized_pnl_usd` and `gross_notional_usd` when position size is known
- Stamped `close_reason` on the outcome row

### 2. Setup Timeline URL (frontend)
- Uses existing `GET /api/admin/alpha-daytrader/setup/{setup_id}/timeline` (SQLite hot store; no Mongo duplication)
- Modal viewer inside `AlphaDayTraderPanel.jsx` with 4-phase latency tiles + full event log + payload JSON
- Deep-link via URL hash `#alpha-timeline-<setup_id>` — refreshable / shareable
- One-click "Timeline" button next to each row in the Resolved Trade Report
- Also opens by clicking any row in the "Recent setups" list
- "Copy URL" button for sharing

### 3. Edge Discovery Radar (frontend)
- Filters `alpha_edge_rollups` to `samples < 10`
- Per-bucket columns: pattern, slow, fast, samples, "N more" to threshold, progress bar, interim expectancy
- Interim expectancy shown for information only — badge state does not change until the bucket reaches 10 samples (per Edge Engine rules)

### Tests
- 57/57 passing (17 A+B + 10 C + 12 D + 9 D+ + 9 E)
- New in E: edge_agreement matrix, exit-slippage on stop/target/time exits, realized_pnl_usd

### Files
- NEW `backend/services/alpha_trade_report.py`
- NEW `backend/tests/test_alpha_trade_report.py`
- MOD `backend/services/alpha_fill_writer.py` (extended metrics)
- MOD `backend/routes/admin_alpha_daytrader.py` (+1 endpoint)
- MOD `backend/tests/test_alpha_phase_d_plus.py` (renamed slippage field)
- MOD `frontend/src/components/admin/AlphaDayTraderPanel.jsx` (report, radar, timeline modal, deep-link)

### Not added by design
- No new execution gates
- No auto-disable behavior
- No operator-adjustable modifier weighting (deferred until real samples accumulate)
- No regime-transition alerts


## 2026-08-12 — Phase F: MooMoo Integration (V1 — Market Data + Broker Adapters + Router)

Per operator spec: parallel with Public.com · US equities + options schema · live tiny-notional · no auto-fallback · Moomoo US.

### Adapters (both built, correctly separated)
- **`services/moomoo_market_data_adapter.py`**: `snapshot_quote`, `snapshot_order_book`, `to_level2_snapshot` (Alpha's L2 schema), `market_state`, `entitlements`, lazy shared `OpenQuoteContext`, symbol translation, degraded-when-OpenD-down semantics
- **`services/moomoo_broker_adapter.py`**: `account_info`, `positions`, `orders`, `fills`, `submit_equity`, `submit_option` (schema-only), `cancel_order`. Transient `unlock_trade` reading `MOOMOO_TRADE_UNLOCK_PASSWORD` from environment only, then re-locking in `finally`. Never logs or persists the password.

### V1 pre-network safety gates on `submit_equity`
1. `MOOMOO_LIVE_ENABLED` env off → refuse
2. Missing `MOOMOO_ACC_ID` or no OpenD context → refuse
3. `qty*price > MOOMOO_MAX_NOTIONAL_USD` (default $50) → refuse
4. Existing MooMoo position open → refuse (single-position rule)
5. `MOOMOO_TRADE_UNLOCK_PASSWORD` missing → refuse
6. RTH-only enforced via `Session.RTH` on `place_order`
7. Options adapter always returns `options_disabled` unless flag flipped, and even then `options_execution_policy_not_ready` until policy ships

### Broker Router (`services/broker_router.py`)
- Precedence: intent-forced → per-bot Mongo config → `BROKER_DEFAULT` env
- Only `{"public", "moomoo"}` accepted; unknown → default (public)
- **No auto-fallback** — a MooMoo rejection stays observable

### SQLite hot-store table
- `broker_comparison`: broker, client_order_id, broker_order_id, symbol, side, qty, limit_price, submit_latency_ms, ack_latency_ms, fill_latency_ms, fill_price, slippage_bps, status, error, extra JSON. Written on every MooMoo submit. **Never** duplicated to Mongo.

### Admin routes (7 new)
- `GET /api/admin/moomoo/status` — non-secret operational status
- `GET /api/admin/moomoo/entitlements`
- `GET /api/admin/moomoo/quote/{symbol}`
- `GET /api/admin/moomoo/order-book/{symbol}`
- `GET /api/admin/moomoo/account`
- `GET /api/admin/moomoo/broker-comparison?limit=50`

### Env-key plumbing (values entered by operator into prod secret store — NOT this repo)
- `MOOMOO_OPEND_HOST` (default `moomoo-opend`)
- `MOOMOO_OPEND_PORT` (default 11111)
- `MOOMOO_ACC_ID` — persisted account id only (never credentials)
- `MOOMOO_TRADE_UNLOCK_PASSWORD` — transient runtime secret
- `MOOMOO_LIVE_ENABLED`, `MOOMOO_OPTIONS_ENABLED` — feature flags
- `MOOMOO_MAX_NOTIONAL_USD` (default 50)
- `BROKER_DEFAULT=public`
- All placeholders empty in `/app/backend/.env` — actual values NEVER committed

### OpenD deployment (separate ops task — deferred per operator)
- Official `moomoo-api==10.9.6908` pinned in `requirements.txt`
- OpenD binary + `OpenD.xml` must be deployed as an independent service (Docker/K8s Deployment), TCP 11111, reachable only from the FastAPI namespace
- MooMoo login credentials + `login_pwd_md5` + `rsa_private_key` live only inside OpenD.xml on the deployment target
- Container image built from vendor binary (no official image published)

### Verified
- 12/12 MooMoo unit tests passing (pre-network safety gates, options gating, router precedence, no credential leak in status)
- 69/69 total Alpha tests still green
- 665 routes registered; all 7 MooMoo endpoints return sensible degraded responses when OpenD is unreachable
- Zero credential leakage in `/status` (regression test in place)

### Not built (deferred, deliberate)
- OpenD sidecar deployment manifest — operator determines from prod hosting
- Automatic Public→MooMoo fallback — explicitly not in V1
- Options autonomous execution — schema only
- Frontend broker selector on bot config UI — next pass
- MooMoo L2 wired into `Level2Confirmation` at intent-creation time — the adapter method exists; wiring is a one-line change in `run_alpha_day_trader_tick` once you enable it via `RISEDUAL_ALPHA_L2_SOURCE=moomoo`

### Files
- NEW `backend/services/moomoo_market_data_adapter.py`
- NEW `backend/services/moomoo_broker_adapter.py`
- NEW `backend/services/broker_router.py`
- NEW `backend/routes/admin_moomoo.py`
- NEW `backend/tests/test_moomoo_adapters.py`
- MOD `backend/route_registry.py` (register moomoo router)
- MOD `backend/requirements.txt` (`moomoo-api==10.9.6908` + deps)
- MOD `backend/.env` (secret-name placeholders only)


## 2026-08-12 — MooMoo Phase F.2: Bot Broker Selector UI + L2 Wire-Up + Deployment Guide

### 1. Bot Broker Selector (create + edit)
- `CreateBotRequest` gained `broker: str = "public"` field
- Bot doc now stamps a top-level `broker` field (`"public"` | `"moomoo"`) at insert; bad values coerced to `"public"` so a typo never wires a bot to nothing
- New route `PATCH /api/bots/{bot_id}/broker` — 400 on invalid value, 404 on unknown bot
- Frontend `TradingBotPanel.jsx`:
  - Create form: broker toggle-pair with `Public.com` / `MooMoo` buttons + inline help text
  - `BotCard`: broker badge (cyan Public / fuchsia MooMoo) + inline switcher next to Delete button, `data-testid` attributes for testability
  - `changeBotBroker()` handler on the parent component reloads bot list on success

### 2. MooMoo L2 Wire-Up (non-blocking)
- `alpha_day_trader._fetch_l2_snapshot(symbol)` — reads `RISEDUAL_ALPHA_L2_SOURCE`
  - `none` (default) → returns `None` → `Level2Confirmation` returns neutral **0.50**
  - `moomoo` → calls `moomoo_market_data_adapter.to_level2_snapshot` → real `Level2Snapshot`
- Adapter down → adapter returns `None` → we return `None` → neutral 0.50 preserved
- L2 source stamped on `intent.reason.l2_source` so audit trail is honest
- **Critical safety property regression-tested**: `Level2Confirmation.score(None) == 0.50` (L2 is a modifier, never a gate)
- To activate: set `RISEDUAL_ALPHA_L2_SOURCE=moomoo` in prod env once MooMoo entitlements confirmed (see `docs/OPEND_DEPLOYMENT.md` §8)

### 3. OpenD Deployment Runbook — `/app/docs/OPEND_DEPLOYMENT.md`
- Vendor artifact sourcing (no official image published)
- Complete Dockerfile + `entrypoint.sh` that renders `OpenD.xml` from env at start, never logs credentials
- Two topologies documented:
  - **A. Sidecar** — single Pod, `127.0.0.1:11111`, 1 replica
  - **B. Dedicated Deployment + ClusterIP Service** — for backend replicas > 1 (recommended)
- Two-secret model: `moomoo-opend-secret` (login + MD5 for OpenD only) and `moomoo-backend-secret` (unlock password + acc_id for backend only)
- Mandatory NetworkPolicy locking OpenD ingress to backend Pods
- 4-step verification checklist (`/status`, `/entitlements`, `/quote`, `/account`) before flipping `MOOMOO_LIVE_ENABLED=1`
- Instant rollback via env flip or admin API — **no auto-fallback** preserved
- Secrets hygiene notes

### Tests
- 76/76 across all phases passing (A/B/C/D/D+/E/F/F.2)
- New in F.2:
  - L2 source defaults to `none`
  - Off-source path never calls MooMoo adapter (regression against silent leak)
  - Adapter down → None → neutral 0.50
  - Adapter up → real snapshot that shifts score above 0.50
  - Router reads per-bot `broker` field from Mongo
  - Router falls back to `public` for unknown bot

### Verified end-to-end (preview)
- POST /api/bots with `broker=moomoo` → doc persisted with broker field
- PATCH /api/bots/{id}/broker → round-trips
- Invalid broker → 400 with descriptive detail
- Delete → 200

### Files
- NEW `docs/OPEND_DEPLOYMENT.md`
- NEW `backend/tests/test_alpha_l2_wireup.py`
- MOD `backend/services/trading_bot/_data_access.py` (broker on create)
- MOD `backend/routes/trading_bots.py` (broker field + PATCH endpoint)
- MOD `backend/services/alpha_day_trader.py` (L2 source resolver)
- MOD `frontend/src/components/TradingBotPanel.jsx` (create form toggle + card badge + inline switcher)

