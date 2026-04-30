# RISEDUAL AI — Product Requirements Document

## 1. Original Problem Statement
Build a functional clone of a trading app named **RISEDUAL AI** — a full AI-powered
adversarial trading platform with:
* real market data + functional broker connections (Alpaca, Kraken)
* Stripe subscription gateway + credit economy
* AI chat assistant + streaming agent loop
* AI market prediction engine (multi-model consensus)
* AI Strategy Builder + Strategy Backtester + Strategy Marketplace
* a unified AI War Room (Strategist vs. Auditor loop)
* Watchlist Intelligence, Sector Heatmap, real-time P&L, accuracy tracking
* Thread-Safe Native Multi-Agent Engine + Market Vector Memory System
* VAPID push notifications, Live Order Flow Heatmaps
* Paper trading wired to the AI chat
* SEO optimisation + custom domain deployment

## 2. Tech Stack
* Frontend: React · TailwindCSS · lucide-react · html2canvas · EventSource (SSE)
* Backend: FastAPI · Motor (async MongoDB) · APScheduler · emergentintegrations
* DB: MongoDB
* 3rd-party: OpenAI/Anthropic/Google via Emergent Universal Key · OpenRouter · Stripe · Kraken · Alpaca · OpenFIGI · SEC EDGAR · Resend · Finnhub · FRED · FMP · Alpha Vantage

## 3. What's Been Implemented (latest first)

### Step 10 (Outcome Grading) + Risk Quality KPIs Widget + Toxic Alert Live-Validated (Apr 30, 2026)

**Outcome grading wired (step 10 of the IP lifecycle)**: when a
crypto trade closes, `crypto_closer.py` now appends an
`OUTCOME_VERIFIED` proof block to the entry-side IP chain — same
`entity_id`, so the chain runs `SIGNAL_CREATED → ADVERSARIAL →
AUDITOR → AUTHORITY → FAILURE_MODE → RISK_BUDGET →
EXECUTION_ATTEMPTED → OUTCOME_VERIFIED` (8 blocks). Threading: the
bot now persists `proof_chain_entity_id` on every trade row at
fill time; the closer reads it back to anchor the outcome event.
Live verified: forced a backdated SOL close, the chain hash-verifies
end-to-end with `pnl=0.0, outcome="flat"` in the OUTCOME_VERIFIED
payload. Failure of the proof append never blocks a close (wrapped
+ logged).

**Risk Quality KPIs widget**:
- New backend endpoint `GET /api/admin/conviction/quality-kpis?weeks=N`
  (in `routes/admin.py`) bucketing the cleaned-up predictions
  collection by ISO week. Returns:
  - `unique_failure_patterns`: count of distinct
    `(symbol, failure_code)` tuples per week (post-dedup — the
    real KPI now that the toxic-spikes spam bug is fixed).
  - `calibration_gap`: `avg_confidence − empirical_accuracy` per
    week, with mixed-scale confidence normalisation (0-1 vs 0-100).
  - `summary.trend`: `improving` / `stable` / `degrading` /
    `insufficient_data` — head-vs-tail third comparison, robust to
    single-week outliers (>0.05 absolute gap delta flips the badge).
- New frontend component `frontend/src/components/admin/QualityKPIsStrip.jsx`
  mounted in the Conviction admin tab above the existing buckets.
  Two cards side-by-side:
  - **Unique Failure Patterns** card: bar chart (red ≥5 patterns,
    amber 1-4, slate empty) + this-week's-patterns list.
  - **Calibration Gap** card: signed-value display (color-coded —
    emerald <0.10, amber <0.18, red ≥0.18) + trend pill with
    direction icon + SVG line chart with dashed zero reference,
    cyan polyline, dot per populated week. Degrading-trend warning
    banner appears below the chart when applicable.
- Live values: `summary.trend = "improving"`, current_gap =
  -0.064 (healthy), current unique failures = 0 this week.

**Toxic-spikes alert live-validated** (so we don't have to wait for
tomorrow's cron): direct call to `nightly_cleanup()` produced 22
distinct retagged patterns, all unique by `(symbol, date,
confidence)` — zero duplicates. Email render shows just 1 NVDA
row, no collapse callout needed (data is clean). The user's
recurring "76 failures × 3 visible patterns" spam is structurally
impossible now — `MAX_DEDUP_HITS=5000` prevents the upstream cause,
the email-render dedup is a defense-in-depth that didn't even need
to fire.

**Tests**: 9/9 backend tests in `test_iteration155_quality_kpis.py`
+ all regression endpoints green (proof-chain, guard-shadow,
calibration). Lint clean across all 5 modified files.

### Guard Shadow Admin UI + Manual-Order IP Contract Unification (Apr 30, 2026)

**Guard Shadow admin panel** — `frontend/src/components/admin/GuardShadowPanel.jsx`,
wired into AdminPanel as new "Guard Shadow" tab (Insights group, Eye
icon). Surfaces the Decision Pipeline Guard shadow-rollout data:
- **Summary card** — 3 stats: Shadow Mode (ENFORCING/ON badge),
  Decisions Logged (allow/block split), Would-Block Rate (color-coded:
  green <10%, amber 10-25%, red ≥25%) with prescriptive subtitles
  ("Low — safe to enforce" / "Aggressive — review before enforcing").
- **Window selector** — 24h / 3d / 7d / 30d buckets.
- **By-source breakdown** — per-source allow/block counts and percent
  (`crypto_bot`, `manual_order:smart_orders`, etc).
- **Top blocking reasons** — for the "Aggressive" surface; shows which
  gates are vetoing trades the most.
- **Decision feed** — paginated raw rows with source / only-blocked /
  entity-substring filters. Click a row to expand → shows
  `would_notional` vs `executed_notional` (the actual data operators
  need to evaluate enforcement-readiness), risk multiplier, last
  proof hash.
- 17/17 frontend test cases passed live (iteration_154). Verified
  expanded rows correctly surface `would_notional=$113.75` vs
  `executed_notional=$325.00` — the head-to-head data the rollout
  playbook calls for.

**Manual-order path unified** — `services/manual_order_guard.py` now
calls `run_risedual_ip_decision` instead of
`run_guarded_decision_pipeline_async`. Manual orders (smart orders,
options trades, broker routes) now write the same 6-block IP chain
as the crypto bot: `ADVERSARIAL_DECISION → AUDITOR_VERDICT →
AUTHORITY_VALIDATED → FAILURE_MODE_CLASSIFIED → RISK_BUDGET_APPLIED →
EXECUTION_ATTEMPTED`. Auditor calibration veto + explicit authority
validation are now logged on every manual order (previously only
the 4-step K→M→I→J pipeline ran). Updated
`tests/test_manual_order_guard.py` to expect 6 events; 47/47 backend
tests green. Lint clean.

### Toxic Spikes Alert Spam Bug — Root-Caused & Fixed (Apr 30, 2026)

The recurring "76 high-confidence failures, only 3 visible patterns
× 15 copies each" spam email — the operator's long-standing
complaint. Root cause was NOT the price-anchor bypass I initially
suspected; live MongoDB inspection revealed every duplicate had
identical `price_at_prediction=$202.06`. The actual culprit was
`MAX_DEDUP_HITS = 1` in `services/prediction_tracker.py`. Each
prediction record could only be extended once (~30min lifetime under
continuous polling). A `signal_dispatcher` firing every 10 minutes
for 4+ hours produced ~8 fresh records per (symbol, direction)
session — same prediction at the same price, logged 15 times,
each verified independently as a "miss" → spammy alert.

**Fixes (3 layers)**:

1. **Root cause** — `MAX_DEDUP_HITS` raised from 1 → 5000 (effectively
   uncapped for any plausible production run; the price-similarity
   check `<0.2% drift` still forces a fresh record on real price moves).
2. **Defense-in-depth** — added `prediction_date` (YYYY-MM-DD) to every
   new prediction document so alert filters / digests have a clean
   date field, and added a `price <= 0` guard at the top of
   `log_prediction` that drops the row with a sentinel id rather than
   inserting a poison-anchor record.
3. **Email rendering** — `_toxic_spikes_html` now collapses
   `(symbol, date, confidence)` duplicates before the 10-row cap and
   surfaces a callout: *"collapsed N duplicate rows — same prediction
   logged multiple times. Showing distinct patterns only."* Even if
   future bugs slip dupes through, operators see distinct patterns,
   not spam.

**Historical data cleanup**: ran a one-shot Mongo dedup pass that
collapsed 1764 duplicate records (kept the oldest of each
`(symbol, direction, feature, user_id, confidence, price, calendar_day)`
group). Predictions dropped from ~2035 → 271; toxic count
dropped from 124 → **13** (the actual unique high-conf misses).

**Verified**: simulated the exact spam input (45 rows of NVDA/QQQ/SPY
× 15) through the email renderer — output is now 3 distinct rows
with the "collapsed 42 duplicate rows" callout. Lint clean. Backend
healthy after restart.

### Canonical IP Contract Wired Into Crypto Bot + Per-Patent Enforcement Flags (Apr 30, 2026)

**Bot now executes via the canonical contract.** Replaced the
`run_guarded_decision_pipeline_async` call site in
`services/crypto_paper_trader.py` with `run_risedual_ip_decision`. The
crypto fleet is now the live execution path for the full 7-block IP
chain — `SIGNAL_CREATED → ADVERSARIAL_DECISION → AUDITOR_VERDICT →
AUTHORITY_VALIDATED → FAILURE_MODE_CLASSIFIED → RISK_BUDGET_APPLIED →
EXECUTION_ATTEMPTED`. Verified end-to-end on the preview: bot ran, all
hashes verified (`valid=True`), 7 ordered events per trade.

**Per-patent enforcement flags** for staged rollout
(`EnforcementPolicy.from_env()`):
- `PATENT_K_ENFORCE` — adversarial enforcement
- `PATENT_M_ENFORCE` — failure-mode block_trade
- `PATENT_I_ENFORCE` — risk budget rejection
- `AUDITOR_ENFORCE` — calibration veto
- `AUTHORITY_ENFORCE` — authority expiry/countersignature

When a flag is OFF, the corresponding gate STILL RUNS (proof chain
captures intent) but its rejection is downgraded — the trade flows to
the next gate using the operator's pre-guard sizing. Stage rollout
maps directly:
- **Stage 1**: All flags = 0 → only proof chain mandatory.
- **Stage 2**: `PATENT_M_ENFORCE=1` → liquidity gaps + data-quality block.
- **Stage 3**: `PATENT_K_ENFORCE=1` → adversarial conflict blocks.
- **Stage 4**: `PATENT_I_ENFORCE=1` → risk budget enforces.

Defaults: every flag is on. Operators flip them to `0` for shadow
gates. The contract response includes a `policy` dict so callers can
audit which gates were active per decision.

**Effective-action plumbing**: gates always *propose* an action +
notional; downstream uses gate output only when the gate is enforced.
When shadowed, the operator's signal action and `base_notional ×
base_multiplier` flow through unchanged so the rest of the contract
inspects what the operator actually wanted to do, not the gate's
safer fallback.

**Tests**: 21/21 green in `test_risedual_ip_logic.py` —
+6 new policy tests (shadow-passes-through-K, shadow-passes-through-auditor,
shadow-keeps-original-notional, partial-only-failure-mode-enforced,
from-env-defaults, from-env-reads-off-flag). Lint clean.

**PRD.md split**: trimmed from 2587 → 235 lines; historic implementation
log moved to `/app/memory/CHANGELOG.md`. PRD.md now keeps recent
entries + architecture + pointer to CHANGELOG.

### Canonical IP Decision Contract + Shadow-Mode Rollout (Apr 30, 2026)

The patent stack is now expressed as a single legal/technical contract
(`services/risedual_ip_logic.py`) plus a shadow-mode middle gear so
operators can compare "would-block" vs "actually executed" before
flipping enforcement on.

**`services/risedual_ip_logic.py` — `run_risedual_ip_decision(ctx)`**:
The 11-step lifecycle codified as one master function. Every gate
hash-logs to the proof chain — including REJECTIONS — so the legal
audit trail is complete whether the trade fires or doesn't:
1. Observe market (caller)
2. Generate candidate (`CandidateSignal`)
3. **Adversarial validation** (Patent K) → `ADVERSARIAL_DECISION` block
4. **Auditor calibration veto** (new) → `AUDITOR_VERDICT` block
5. **Authority validation** (Patent H/I expiry+countersignature) →
   `AUTHORITY_VALIDATED` block
6. **Failure mode classify** (Patent M) → `FAILURE_MODE_CLASSIFIED` block
7. **Adaptive risk budget** (Patent I) → `RISK_BUDGET_APPLIED` block
8. Execute (caller's `ExecutionClient`) → `EXECUTION_ATTEMPTED|FILLED`
9. Hash-log everything (Patent J — already inline)
10. Grade outcome (separate flow)
11. Feedback into retrain (separate flow)

Plus the **`can_execute(decision)`** rule predicate and 4
non-negotiable invariants (canonical action, multiplier ≤ authority,
no HOLD with notional, no loosening without countersignature) baked
in as `assert`s — programming errors fail loudly, not silently.

**`services/auditor_calibration.py`** — the missing step-4 module.
Pure function `review_calibration(rolling_accuracy, calibration_gap,
signal_confidence)` returns PASS / CAUTION / VETO. Wide gaps veto
unconditionally; medium gap + high confidence is a compound veto.
Catches the "model unanimously confident but historically wrong"
failure mode that adversarial enforcement (Bull-vs-Bear spread)
doesn't.

**Shadow-mode middle gear** (`GUARD_SHADOW_MODE` env flag):
- `services/guard_shadow_log.py` — single-writer module that writes
  guard verdicts to a new `guard_shadow_log` Mongo collection. Never
  raises; a logging failure must not block live trading.
- Wired into `manual_order_guard.py` and `crypto_paper_trader.py` —
  when `GUARD_SHADOW_MODE=1` the guard runs end-to-end (proof chain
  populates), but the verdict is RECORDED rather than ENFORCED.
  Routes proceed with the original notional. Default: `0`.
- New admin endpoints under `/api/admin/guard-shadow/`:
  - `GET /summary?hours=N` — counts + would-block rate + top blocking
    reasons + per-source breakdown (`crypto_bot` / `manual_order:smart_orders` etc).
  - `GET /decisions?limit=N&source=...&only_blocked=true&entity_substr=...`
    — paginated raw feed.
- Live verified end-to-end on the preview: flipped flag on, ran the
  crypto fleet (SOL SHORT @ $83.84, $318.33 notional), shadow row
  persisted with `would_allow: true`, source=`crypto_bot`. Flipped
  flag off, summary correctly reflects `shadow_mode_enabled: false`
  while keeping the historic row queryable.

**Tests**: 15/15 green in `test_risedual_ip_logic.py` covering happy
path (6 proof events), every rejection branch (invalid action,
adversarial reject, auditor compound veto, expired authority,
failure-mode block, execution failure), the `can_execute` predicate,
dry-run mode, and 5 auditor calibration unit tests covering the
compound-veto edge cases. Lint clean across all 5 new files.

**The IP rule, in plain English**: An AI may propose, but only a
governed, adversarially validated, failure-aware, authority-scoped,
proof-logged decision may execute.

### Tier 1 Visual Polish + Paper/Live Consolidation + Exception Sanitization (Apr 30, 2026)

**Per-item Paper/Live pickers consolidated** (user request — single global switch):
- `SmartOrderPanel.jsx` — replaced the Paper/Live/Preview `<Select>`
  (line 184) with a read-only mode badge (`data-testid="smart-order-mode-display"`)
  that mirrors the global navbar `TradingModePill`. The "simulate"
  preview path is still reachable via the existing dedicated Simulate
  button on the panel.
- `TradingBotPanel.jsx` `CreateBotForm` — replaced the per-bot Paper/
  Live dropdown with a read-only badge (`data-testid="create-bot-mode-display"`)
  inheriting from `useTradingMode()`. New bots created in whichever
  mode the operator's navbar pill says.
- Single source of truth confirmed: every mode change now flows
  through `POST /api/trading-mode/switch` with the 30s cooldown +
  audit pipeline. No bypass paths.

**CryptoPaperDashboard re-skin** (cosmetic harmonisation):
- Migrated palette from `bg-neutral-950/900/800` (gray) to
  `bg-slate-800/40 + slate-700/30` so the panel matches every other
  admin tab (Shadow, Adversarial, Proof Chain, etc).
- "Run Bot" promoted to the cyan brand CTA (`bg-[#3DE8D9]
  hover:bg-[#7AEEE0]`); "Close Due Trades" demoted to a quieter
  slate variant — proper visual hierarchy between primary +
  secondary actions.
- Stat cards now use uppercase tracking-wider labels + monospace
  values; recent-trades rows use cyan selection ring matching the
  brand.

**OrderFlowHeatmap duplicate-key fix**:
- `key={row-${row.price}}` could collide when grid binning produced
  the same rounded price. Now `key={row-${ri}-${row.price}}`.
- Console "Encountered two children with the same key" warning
  on the heatmap grid is gone. The remaining duplicate-key warnings
  in the console come from the external `emergent-main.js` platform
  script (not app code) — nothing actionable on our end.

**Global exception handler review** (P2):
- 5× `f"Broker error: {exc}"` 502 leaks in `routes/options_trading.py`
  replaced with sanitized "Broker [order placement|spread routing|
  unavailable]" — full exception still logged via `log_error()` for
  ops debugging.
- 1× `error: str(exc)` leak in `routes/public_api.py` `/ml-signal/batch`
  (200-response bypassed the global sanitizer) replaced with stable
  `"signal_unavailable"` token. Real exception logged via
  `logger.warning`.
- 72/72 related backend tests green.

**Frontend agent verification**: 15/15 cases passed (iteration_153)
— mode badges render correctly + match global mode, dropdowns gone,
all 4 admin surfaces still render, Run Bot + Close Due Trades both
functional, OrderFlowHeatmap fix confirmed.

### Patent J Proof Chain Explorer — admin UI wired + Mongo-aligned hash (Apr 30, 2026)
- Wired `frontend/src/components/admin/ProofChainExplorer.jsx` into
  `AdminPanel.jsx` Insights group as new "Proof Chain" tab
  (ShieldCheck icon, sits next to Patent Watch).
- Fixed cross-environment hash drift in `services/proof_chain.py`:
  Mongo BSON Date strips tzinfo and truncates microseconds to
  milliseconds on round-trip. Added `canonical_created_at(dt)`
  helper used by both `build_proof_block` (write) and
  `verify_chain` (read). Wiped 81 stale dev blocks; fresh chains
  verify clean.
- 15/15 backend tests green; frontend agent: 7/7 cases.

### Earlier Architecture — Patents I, J, K, M wired into Decision Pipeline Guard
- Patent I (Authority-Scoped Risk Budgeting), J (Tamper-evident
  Proof Chain), K (Structured Adversarial Enforcement), M (Failure
  Mode Intelligence) — all live and guarding crypto bot, broker
  routes, smart orders, options trading. 116 backend tests green.
- `PATENT_GUARD_ENABLED` env flag controls activation.


- Wired `frontend/src/components/admin/ProofChainExplorer.jsx` into
  `AdminPanel.jsx` Insights group as new "Proof Chain" tab
  (ShieldCheck icon, sits next to Patent Watch). Stats card +
  searchable entity list + chain detail pane with Verified/Tampered
  badge.
- Fixed cross-environment hash drift in `services/proof_chain.py`:
  Mongo BSON Date strips tzinfo and truncates microseconds to
  milliseconds on round-trip, so the verifier-side recompute
  produced different SHA-256s than the insert-side hash. Added
  `canonical_created_at(dt)` helper that normalises to UTC tz-naive
  + ms-precision before isoformatting. Both `build_proof_block`
  (write) and `verify_chain` (read) and `routes/admin_proof_chain.py`
  use it so insert-time and verify-time hash material is byte-identical.
- Wiped 81 stale dev blocks (hashed under the pre-fix tz-aware iso
  form). Fresh bot run repopulated cleanly: 6 blocks across 3
  entities (BTC/ETH/SOL), `valid=true` on chain detail.
- **Tests**: 15/15 green across `test_proof_chain.py` (7),
  `test_proof_chain_e2e.py` (3), `test_decision_pipeline_guard.py`
  (3), `test_adversarial_enforcer.py` + `test_failure_mode_classifier.py`.
  Lint clean.
- **Frontend agent verified**: 7/7 test cases passed
  (iteration_152.json) — tab renders, stats card, entity list
  filters, chain detail loads, Verified badge shows, block expansion
  reveals prev_hash/payload_hash/actor/created_at + payload dropdown.

### Earlier Architecture — Patents I, J, K, M wired into Decision Pipeline Guard
- Patent I (Authority-Scoped Risk Budgeting), J (Tamper-evident
  Proof Chain), K (Structured Adversarial Enforcement), M (Failure
  Mode Intelligence) — all live and guarding crypto bot, broker
  routes, smart orders, and options trading. 116 backend tests green.
- `PATENT_GUARD_ENABLED` env flag controls activation.

## 3. Architecture
```
/app
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── hubs/
│   │   │   │   ├── WarRoomHub.jsx        ← NEW (Phase 1) — merges 5 AI surfaces
│   │   │   │   ├── StockDetailHub.jsx    ← NEW (Phase 2) — merges Company+StockFit+13F
│   │   │   │   ├── ResearchHub.jsx       ← v2 slimmed from 10→5 tabs
│   │   │   │   ├── OptionsHub.jsx        ← v2 icon-only
│   │   │   │   ├── WorkspaceHub.jsx      ← v2 13-icon tabs + legend
│   │   │   │   └── IconTabBar.jsx        ← NEW (Phase 4) — reusable icon tab strip
│   │   │   └── chat/                     (decomposed chat components)
│   │   └── hooks/
│   │       └── useV2Nav.js               ← NEW — feature flag (v2 is default)
└── backend/
    ├── services/
    │   ├── email_service.py              ← Rewritten (light-theme templates)
    │   ├── digest_service.py             ← Rewritten (pulls real market data)
    │   ├── referral_rewards.py           ← Wired to send tiered reward emails
    │   ├── sec_13f_service.py
    │   └── cusip_mapper.py
    ├── routes/
    │   ├── auth.py                       ← Admin panel bug fixed (role=admin access)
    │   ├── digest.py                     ← Preview returns new content_summary
    │   └── stockfit_13f.py
    └── scripts/
        └── merge_owner_into_admin.py     ← NEW — Red Slate → Admin account merge
```


## 4. Older Implementation History

Entries older than ~30 days (or beyond the most recent 5 features) live in
[CHANGELOG.md](./CHANGELOG.md) to keep PRD.md scannable.
When PRD.md crosses 700 lines, roll the oldest "What's Been Implemented"
entries out to CHANGELOG.md the same way.

