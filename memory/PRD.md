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

## 4. What's Been Implemented (cumulative)

### LLM Provider chat() Refactor (COMPLETED Feb 19, 2026)
- Pushed back on the original suggested `_handle_streaming` / `_handle_standard`
  split — there is no streaming code in either provider, so that pattern
  didn't apply. Took the safe extraction instead.
- **Extracted** the response-parsing block in both
  `risedual_core/risedual_core/llm/anthropic.py` and
  `risedual_core/risedual_core/llm/openai.py` into a private
  `_parse_response(response) -> LLMResponse` helper.
- **Line counts**:
  - `anthropic.chat()`: 105 → 62 lines. New `_parse_response()`: 51 lines.
  - `openai.chat()`: 99 → 56 lines. New `_parse_response()`: 48 lines.
- Both classes still import and expose the same public API. `chat()` is
  now a clean linear flow: build kwargs → API call → `_parse_response`.
- Verified: lint clean, imports work (`AnthropicLLM.chat` /
  `AnthropicLLM._parse_response` both callable), backend restarted
  healthy, `/api/crypto/prices` 200.

### React Hooks Exhaustive-Deps Sweep (COMPLETED Feb 19, 2026)
- User asked to run `npx eslint src/hooks/ --rule '{"react-hooks/exhaustive-deps": "error"}'`.
- **Result: 0 errors** across every custom hook flagged in the original
  code review (`useTTS`, `useStreamingAgent`, `usePushNotifications`,
  `useChatMemory`, `useReferralCapture`, `useModals`). The original
  reviewer's "missing dependencies" claim was categorically wrong.
- Ran the rule across all of `src/` — still 0 errors. Auto-fix removed
  8 orphaned `// eslint-disable-next-line react-hooks/exhaustive-deps`
  comments in `AuthContext.jsx`, `MobileBottomNav.jsx`, and
  `ShareSmartMoneyBoard.jsx` that were suppressing warnings no longer
  fired. Pure cosmetic cleanup.
- Final state: **0 errors, 0 warnings** under strict exhaustive-deps.

### Pyflakes Deep Scan + Re-export Bug Fix (COMPLETED Feb 19, 2026)
- User asked to run `pyflakes` across the whole backend. Found 12 actual
  undefined names (all in non-runtime code):
  - Fixed `scripts/backfill_insider_edgar.py`: missing `SEC_BASE` constant
    added (`"https://data.sec.gov"`).
  - Fixed `tests/test_iteration96_auto_invite.py`: missing `import sys`.
  - Fixed `scripts/train_signal_model.py`: 10 `pd` forward-ref complaints
    resolved by adding `TYPE_CHECKING` guarded `import pandas as pd`.
- **CAUGHT + FIXED a real production bug** introduced earlier: the
  `ruff --fix` pass had aggressively removed `seed_admin` and
  `create_indexes` from `route_registry.py`'s `from routes.auth import …`
  line, thinking they were unused locally. But server.py re-imports them
  from `route_registry`. Backend was crashing on startup with
  `ImportError: cannot import name 'seed_admin'`. Restored the re-exports
  with `# noqa: F401` comment and a warning comment.
- **Final status**: 0 undefined names across the whole backend. All
  services healthy, all tested API endpoints responding 200.

### Code Review Pass (COMPLETED Feb 19, 2026)
- **Fixed**: replaced array-index React `key`s with stable data-driven keys in
  the components I own — `ChipAdoptionInsights.jsx` (3 tables),
  `HelpSearchInsights.jsx` (2 tables), `ChatComponents.jsx` (chips +
  actions using `${idx}-${text}`), `HelpCenter.jsx` (suggestions + results).
  Prevents React reconciliation bugs when lists re-order.
- **Reviewed & declined (false positives)**:
  - "eval() in backtester_service.py line 193" — it's a COMMENT saying
    "Safe Expression Evaluator (replaces eval())". The code is an
    AST-based safe evaluator (`_CMP_OPS`, `_BIN_OPS` using `operator`
    module), not eval. No security issue.
  - "Hardcoded secrets in tests" — these are TEST credentials from
    `test_credentials.md` (e.g. admin@risedual.ai) used by pytest
    fixtures. Not production secrets.
  - "exec/eval in test_iteration36_code_quality.py" — those are SECURITY
    TESTS named `test_rejects_exec` / `test_rejects_eval` that verify the
    app REJECTS dynamic code execution. Part of the safeguard, not a risk.
  - "17 undefined variables" — zero in runtime code
    (routes/services/server.py). All F821 errors are in standalone
    `backend/scripts/` using string-forward-refs like `"pd.DataFrame"`.
- **Reviewed & deferred (post-deploy)**: component size refactors
  (Watchlist 428L, Navbar 346L, RiseDualGPTChat 350L), LLM `chat()`
  function decomposition, `useV2Nav/useTTS/useStreamingAgent` missing
  hook deps. These are real improvements but touch production-critical
  paths on the eve of deploy — post-deploy work with proper QA.

### Misclick Rate + Pre-Deploy Cleanup (COMPLETED Feb 19, 2026)
- **Backend stats**: `/api/analytics/chip-events/stats` now classifies
  `action-clicked` events into forward-clicks and undo-clicks (chip_text
  starts with `"Undo "`) and returns:
  - `undo_count` + global `misclick_rate` = undos / forward-clicks
  - Per-hub `undo_clicked` + `misclick_rate`
  - `top_actions` now EXCLUDES undo entries (leaderboard shows what users
    actually want, not what they bounce from)
- **Admin UI**: new `Misclick` column in Per-Hub Breakdown table — red ≥25%,
  amber ≥10%, slate otherwise. Hover title shows raw counts.
- **Bug found + fixed (pre-existing)**: `backend/routes/analytics.py` had a
  broken duplicate `trigger_help_search_digest` endpoint at line 276 with no
  success return body. FastAPI was registering two routes for the same
  path — the stub could have taken precedence over the real one. Deleted.
- **Auto-fixed 499 unused imports** across `backend/routes/*.py` via ruff.
  All runtime code (routes/, services/, server.py) is now lint-clean except
  1 cosmetic unused-local in server.py. Remaining backend lint noise is
  entirely in standalone `backend/scripts/` which aren't imported at runtime.
- Frontend lints 100% clean (`components/`, `utils/`, `App.js`).
- **E2E verified**: stats endpoint returns `action_clicked=12, undo_count=1,
  misclick_rate=0.083`, Misclick column renders correctly in the admin panel.
- **Ready to deploy.** ✅

### Undo Last Deep-Link Toast (COMPLETED Feb 19, 2026)
- Every call to `openWarRoomForTicker` now shows a sonner toast bottom-right
  ("Analyzing XLK · From Sector Heatmap · [Undo]") with a 5s duration and
  an Undo button.
- Undo dispatches `risedualai-navigate` back to the view the user was on
  (snapshotted via `window.__risedualActiveView` before the nav), so any
  misclick on a tile is reversible with one click.
- Undo actions also log `action-clicked` telemetry with chip_text like
  `"Undo XLK War Room (Sector Heatmap)"`, giving the admin Chip CTR
  dashboard a proxy for misclick rate per source.
- Toast suppressed when origin was already `warroom` (no meaningful back
  state) to avoid a "noisy" experience once the user is inside the hub.
- **App.js fix along the way**: `window.__risedualActiveView` is now
  synced via `useEffect` on activeView changes (was only written during
  `navigateTo`, so it was `undefined` on initial mount — which broke the
  undo snapshot on the very first tile click).

### Component Sweep + Deep-Link Consolidation (COMPLETED Feb 19, 2026)
- **Deleted orphans** (no consumers anywhere):
  - `components/intelligence/ScoreView.jsx`
  - `components/BotsDashboard.jsx`
  - `components/CryptoSection.jsx` (already removed in previous commit)
- **New shared util** `/app/frontend/src/utils/deepLink.js` exporting a single
  `openWarRoomForTicker({ticker, source, subTab?, suffix?})` helper that:
  1. Logs `action-clicked` telemetry to `/api/analytics/chip-event`
  2. Dispatches `risedualai-navigate` to the right hub/subtab
  3. Dispatches `risedualai-warroom` ticker broadcast
- **Refactored 5 callers** to use the helper — previously each had a
  hand-written ~18-line try/fetch/dispatch block:
  - `components/heatmap/SectorTile.jsx` (2 call sites: AI view + price view)
  - `components/CryptoTicker.jsx` (crypto tile)
  - `components/FearGreedGauge.jsx` (verdict tab, with `suffix` carrying the
    live regime)
  - `components/Watchlist.jsx` (SM shift alert + per-row SM Board button)
- **Net reduction**: ~90 lines of duplicated code deleted. Changing telemetry
  shape or adding a step now means editing one file.
- Lint clean. E2E verified on 3 independent surfaces (XLF sector, ETH crypto,
  Fear & Greed) after the consolidation — zero runtime errors.

### Fear & Greed Verdict Tab Redesign + Clickable Deep-Link (COMPLETED Feb 19, 2026)
- Replaced the semi-circular gauge arc with a **verdict-tab card** that mirrors
  the AI War Room PASS/VETO / BULLISH / BEARISH visual language.
- **Clickable**: the whole verdict tab is now a `<button>`. One click fires:
  - `action-clicked` telemetry with a rich chip_text capturing the live
    regime at click time, e.g. `"Open SPY War Room (Fear & Greed: GREED 68)"`
    — so admin `Chip CTR` can track not just adoption but WHICH sentiment
    regimes users act on.
  - Navigates to War Room Adversarial subtab.
  - Dispatches `risedualai-warroom` with `SPY` (S&P proxy).
- Hover affordance: the date swaps to a subtle "Ask War Room →" hint (band-
  colored) so the deep-link intent is discoverable without cluttering the
  resting state.
- Verified E2E: clicked verdict at 68 GREED → War Room opened → SPY
  auto-analyzed ("Deploying War Room for SPY" running Strategist + Auditor).

### Markets Density Toggle (COMPLETED Feb 19, 2026)
- New `MarketsSection.jsx` component hosts both heatmaps with a 3-way
  segmented toggle: Both · Crypto · Sectors (icons: LayoutGrid · Bitcoin ·
  BarChart3). Default is "Both". Persisted in `localStorage` under
  `risedual:markets-view`.
- Moved `CryptoTicker` out of the global top-of-app strip (was rendered on
  every view under the navbar) into the Dashboard Markets section — reclaims
  vertical space on Research/Options/Workspace views where it wasn't needed.
- `CryptoTicker` component slimmed: removed its own `bg/border/padding`
  chrome so it can be embedded cleanly inside the new wrapper.
- Verified E2E: default `Both` shows both heatmaps; clicking `Crypto`
  collapses sectors; clicking `Sectors` collapses crypto. localStorage
  persistence confirmed across reloads.

### Crypto Heatmap Tiles with War Room Deep-Link (COMPLETED Feb 19, 2026)
- Rebuilt `CryptoTicker.jsx` from an auto-scrolling horizontal marquee into a
  responsive grid (`grid-cols-2 sm:grid-cols-4 lg:grid-cols-8`) matching the
  `SectorTile` visual language — color-coded by % change (heat scale tuned
  tighter for crypto volatility: ≥5% deep green, ≤-5% deep red).
- Each tile is now a clickable `<button>` → same deep-link bundle:
  telemetry `action-clicked` with source `"Crypto Heatmap"` → navigate to
  War Room Adversarial → dispatch ticker broadcast. BTC / ETH / BNB / SOL /
  XRP / ADA / DOGE / AVAX all route correctly.
- Verified E2E: clicked BTC tile → War Room opened → BTC auto-analyzed.
  Mobile + desktop layouts confirmed.
- Admin `Chip CTR` panel now differentiates three heat-source flavours in
  top-actions: `Sector Heatmap`, `AI Sector Heatmap`, `Crypto Heatmap`.

### Sector Heatmap Deep-Link + Recent Tickers Strip (COMPLETED Feb 19, 2026)
- **Sector Heatmap tiles** (`SectorTile.jsx`) are now clickable `<button>`s.
  One click on any sector ETF (XLK, XLF, XLV, etc.) — either the "AI sentiment"
  view or the classic "% change" view — dispatches the standard deep-link
  bundle: telemetry (`action-clicked`) → nav to War Room adversarial tab →
  ticker broadcast. Source tag in chip_text: `"Open XLK War Room (Sector
  Heatmap)"` / `"(AI Sector Heatmap)"` so the admin dashboard can split them.
- **Recent Tickers strip** lives in the War Room hub header ("Recent: NVDA
  TSLA AAPL"). Persisted in `localStorage` under `risedual:recent-tickers`
  (max 3, deduped, most-recent first). All three War Room subtabs call
  `addRecent(symbol)` on analyze success, so both manual searches and
  deep-link arrivals populate it. Click a pill → re-dispatches
  `risedualai-warroom` so the active subtab re-runs without any typing.
- Utility: `/app/frontend/src/utils/recentTickers.js` — exports `addRecent`,
  `getRecent`, `subscribeRecent` (pub/sub so the hub re-renders instantly).
- **Verified E2E**: clicked XLK tile → War Room opened → XLK auto-analyzed
  → Recent strip showed NVDA/TSLA/AAPL pills from localStorage in one render.

### CTR Breakdown By Hub + Full War Room Ticker Broadcast (COMPLETED Feb 19, 2026)
- **Backend**: `GET /api/analytics/chip-events/stats` now also returns
  `by_hub[]` aggregated per `context_hub` with `shown`, `clicked`, `l1_ctr`,
  `action_shown`, `action_clicked`, `l2_ctr`, sorted by total event volume.
- **Admin panel**: "Per-Hub Breakdown" table added under Chip CTR tab — shows
  which app surfaces (Dashboard / Research / War Room / Options / Workspace)
  drive the highest L1 (chat chip) and L2 (deep-link action) CTR. Green when
  CTR ≥20%, amber 10–19%, slate <10%.
- **Ticker-broadcast pattern extended**: `MarketPrediction` and
  `AIHypothesis` now both listen to the `risedualai-warroom` event, mirroring
  `AIWarRoom`. Any deep-link navigation with `{view:'warroom', subTab:'X'}`
  + a ticker dispatch auto-fills the ticker and runs analysis on whichever
  subtab is landed on.
- **WarRoomHub reactive sync**: previously only read `initialTab` on mount —
  now syncs via `useEffect`, so repeat-clicking different War Room deep-links
  while already on the hub switches the sub-tab correctly.
- **Verified E2E**: clicked SM Shift Alert → War Room Hypothesis subtab →
  TSLA auto-filled → "GPT-5.2 is analyzing TSLA..." started. Per-Hub
  Breakdown table shows live aggregated data across 3+ hubs.

### Smart Money Board → War Room Deep-Link (COMPLETED Feb 19, 2026)
- Every Smart Money Shift Alert row in `Watchlist.jsx` now has a compact
  "WAR ROOM →" button (and per-row `Swords` icon for SM-scored rows).
- Click flow: (1) logs `action-clicked` telemetry with chip_text
  `"Open {TICKER} War Room (SM Board|SM Shift Alert)"`, (2) dispatches
  `risedualai-navigate` → War Room hub, (3) dispatches `risedualai-warroom`
  with the ticker.
- `AIWarRoom.jsx` listens to `risedualai-warroom`, populates the symbol input,
  and auto-fires `analyze()` so the Strategist vs. Auditor run starts in one
  click — zero keystrokes between "I see a Smart Money shift" and "I have AI
  verdict."
- Verified E2E: clicked NVDA shift alert → War Room hub rendered → NVDA input
  auto-filled → "Deploying War Room for NVDA" analysis auto-started.

### Floating Chat Window (COMPLETED Feb 19, 2026)
- Un-pinned the chat from screen edges on both breakpoints.
- Desktop (≥lg): 400×560 floating card, ~24px from bottom-right, backdrop-blur
  + elevated shadow.
- Mobile: ~12px side margins × 72dvh height above the bottom nav — no longer
  a full-screen takeover; navbar/ticker remain visible.

### Level-2 AI Chat Actions — Inline Deep-Link Buttons (COMPLETED Feb 19, 2026)
- **Backend**: `/api/chat/followups` now returns `{chips[], actions[]}`. The LLM
  picks 0–2 deep-link actions when the reply has clear routing intent (ticker
  discussed → research/watchlist; predictions → warroom; options chains →
  options; portfolio/P&L → workspace). Labels capped at 40 chars, tickers validated.
- **Backend**: `/api/analytics/chip-event` now accepts `action-shown` /
  `action-clicked` in addition to `shown` / `clicked`, so L2 adoption is tracked
  independently from L1 chips.
- **Backend**: `/api/analytics/chip-events/stats` now returns `action_shown`,
  `action_clicked`, `action_ctr`, `top_actions[]` alongside the existing chip
  stats.
- **Frontend**: New event bus `risedualai-navigate` (listened in `App.js`) lets
  any surface deep-link into a hub via `{view, subTab}` payload. The chat
  uses it for L2 clicks.
- **Frontend**: Chat UI renders a cyan "Go" row of action buttons (with `→`
  suffix) ABOVE the gray "Next" row of L1 chips. Clicking an action:
  1. Logs `action-clicked` telemetry.
  2. Dispatches the correct nav event (`risedualai-navigate` for hubs;
     `risedualai-research`/`risedualai-add-watchlist` for ticker-aware actions).
  3. Closes the chat so the target hub becomes visible.
- **Admin**: Chip CTR tab upgraded to a 5-KPI grid (Shown · Clicked · L1 CTR ·
  L2 CTR amber · Signal) plus a second table for top-clicked deep-link actions.
- **Verified**: 17/17 backend tests passed; E2E chat flow + admin panel verified
  by the testing agent (iteration_135).

### Chip Adoption Admin Dashboard (COMPLETED Feb 19, 2026)
- **New component** `/app/frontend/src/components/admin/ChipAdoptionInsights.jsx`
  mirrors the `HelpSearchInsights` pattern: 4 KPI cards (Shown · Clicked · CTR ·
  Level-2 Signal), 7d/30d/90d window toggle, top-clicked chips table.
- **Wired into AdminPanel** as a new tab `Chip CTR` (icon: MessageSquare) between
  `Help Search` and `Tools`.
- **Signal thresholds**: &ge;20% CTR = `High` (ship Level-2 deep-links),
  10–19% = `Medium`, &lt;10% = `Low` (redesign before investing).
- **E2E verified**: logged in as admin, opened panel, clicked `Chip CTR` tab,
  confirmed all KPIs + top-clicked table populate from real Mongo events.

### Level-1 AI Chat Follow-up Chips + Adoption Telemetry (COMPLETED Feb 19, 2026)
- **Backend** `POST /api/chat/followups` (Emergent LLM, `gpt-4o-mini`) generates 3
  contextual follow-up suggestions after every assistant reply.
- **Backend** `POST /api/analytics/chip-event` logs `shown` + `clicked` events to
  `chip_events` collection (non-blocking, accepts anon + authed users).
- **Backend** `GET /api/analytics/chip-events/stats?days=N` — admin-only; returns
  shown/clicked counts, CTR, top clicked chips.
- **Frontend**:
  * `RiseDualGPTChat.jsx` fires `clicked` telemetry in `onFollowupClick` before
    dispatching the prefill → sendMessage flow.
  * `ChatComponents.jsx` fires `shown` telemetry via a `useEffect` + `Set` ref
    (dedupe by `msgIdx::chipText`) so each rendered chip is counted exactly once.
  * Both calls are fire-and-forget (silent on failure).
- **Purpose**: CTR from this loop gates the decision to build Level-2 inline
  deep-link action buttons. Low CTR → skip Level-2; high CTR → invest.

### Help Search Weekly Digest — Proactive Admin Push (COMPLETED Feb 18, 2026)
- **New service** `/app/backend/services/help_search_digest.py`:
  - Aggregates last-7-days `help_search_events` via $group pipeline.
  - Skip threshold: <3 zero-result events → no admin spam on quiet weeks.
  - Only sends to active `admin`/`owner` roles with email (excludes merged/deactivated).
- **New HTML template** `_help_search_digest_html` in `email_service.py`:
  - KPI row (Total · Zero-Result · Gap Signal Low/Medium/High by rate).
  - "Biggest Gap This Week" callout with top query + user count.
  - Top-15 table: query, count, unique users, context hubs.
  - Light-theme Gmail-safe layout consistent with digest emails.
- **APScheduler job** registered in `server.py` — `cron` Mon 7:00 UTC, id=`help_search_weekly_digest`.
- **Manual trigger** `POST /api/analytics/help-search/send-digest` (admin-only) + "Email digest" button in the admin panel Help Search tab.
- **Bug fix**: `seed_admin()` in `routes/auth.py` now respects merged state. Previously every restart resurrected `managingdirector@redslateholdings.com` to `role=owner, is_active=True` — now checks for `role=='merged'` or `merged_into_email` first, only updates password hash for audit access.

## Help Center v2 (COMPLETED Feb 18, 2026)
- **Rewrote `/app/frontend/src/components/HelpCenter.jsx`** to match v2 aesthetic:
  - Uses the same `IconTabBar` component as War Room / Research / Options / Workspace.
  - 8 sections × ~45 tips, all content refreshed for v2 architecture (War Room hub, Stock Detail merge, Classic UI toggle, paper trading gates, referral rewards, live broker status).
  - Global fuzzy search across all titles + content with score-ranked results.
  - "Take me there →" deep-links that close the modal and navigate to the exact hub/sub-tab.
  - Context-aware: receives `activeView` from App so search events are tagged with the hub the user was viewing.
- **Search telemetry** — debounced (700ms) POST to `POST /api/analytics/help-search` on every non-trivial query; fire-and-forget.
- **Admin panel "Help Search" tab** (`/app/frontend/src/components/admin/HelpSearchInsights.jsx`):
  - 3 KPI cards: Total events, Zero-Result events, Gap Signal (Low/Medium/High based on zero-result rate).
  - Top zero-result queries table: count, context hub, last-seen timestamp.
  - Top queries overall with avg results per query.
  - 7d / 30d / 90d window toggle.
- **Backend endpoints** (`/app/backend/routes/analytics.py`):
  - `POST /api/analytics/help-search` — logs `{q, results_count, context_hub, user_id, is_anon, ts}` to new `help_search_events` collection. Skips queries <2 or >120 chars.
  - `GET /api/analytics/help-search/stats?days=N&limit=N` — admin-only; aggregates via $group pipeline; returns zero_result_top + top_queries + overall stats.

## v2 UI Consolidation (COMPLETED Feb 18, 2026)
1. **War Room hub** — 5th top-level nav between Dashboard and Research (orange accent).
   Merges Adversarial AI + Predictions + Hypothesis + Signals + Intelligence (5 → 1).
2. **Stock Detail hub** — shared ticker input dispatches `risedualai-research` event.
   Company + StockFit + 13F Holders all respond to the same search (3 → 1).
3. **IconTabBar** reusable component — icon-only tabs + native hover tooltip + ⓘ legend
   popover + active-tab breadcrumb. Used by War Room, Research, Options, Workspace.
4. **Slim mobile menu** — 20 buttons → 7 (hubs + Admin + Logout). All sub-tabs live
   inside each hub's icon bar now, no duplication in the menu.
5. **v2 promoted to DEFAULT** (previously opt-in `?v2=1`). Classic UI preserved as
   one-click archive (`?v1=1` or "Classic UI" pill in header).

### Admin + Account Management — NEW
* **Admin panel "0 users" bug fixed** — `role=admin` accounts can now access
  `/api/auth/admin/users` (previously owner-only). `require_admin()` helper added.
* **Red Slate owner account merged into `admin@risedual.ai`**:
  * 108 docs re-pointed (73 predictions, 24 credit events, 8 chat sessions, 2 smart
    orders, 1 trading bot).
  * Empty duplicate singletons discarded (paper_portfolios, user_credits,
    chat_memory_prefs, watchlists).
  * `referral_codes` conflict tagged `_merged_...` for manual review.
  * `admin@risedual.ai` promoted to `role=owner`.
  * Red Slate shell kept deactivated (`role=merged`, `is_active=false`) for audit.
  * Kraken LIVE connection + 13 api_keys + 32 paper trades + 6 paper positions
    already under admin — preserved intact.

### Email System — REWRITTEN
* `_base_html` light-theme template (Gmail/Outlook-safe with `bgcolor` attrs).
* 8 template variants rebuilt with dark text on light backgrounds + preheader.
* `digest_service.py` rewritten to pull real market data from:
  * `prediction_cache` (market_overview narrative)
  * `predictions` (top AI predictions, last 48h)
  * `smart_money_scores` (institutional flow)
  * `sec_13f_alerts` (regime shifts, last 7d)
* Test accounts (`@test.com`, `test_*@`, `emailtest*`) excluded from sends.
* Pacing added (250 ms/send) to respect Resend's 5 req/sec limit.
* Tiered reward emails wired into `scan_hit_threshold_rewards` and
  `scan_monthly_leaderboard_rewards`.

### Previous Session Work (preserved)
* SEC 13F holder tracking + regime-shift alerts (via EDGAR direct scraping)
* OpenFIGI CUSIP → ticker mapping w/ MongoDB caching
* Watchlist Smart Money Score badges + 30-day sparklines
* "Share My Smart Money Board" PNG export (html2canvas + QR code)
* Tiered Referral Rewards + top-5 Leaderboard
* ML Pipeline (risedual_core), Thread-Safe Multi-Agent Engine
* VAPID push notifications

## 5. Known Issues / Limitations
* QuiverQuant endpoints return 500 — blocked on external provider (P2).
* Alpaca LIVE Client ID/Secret pending user submission — paper works, live gated.

## 6. Backlog / Roadmap
### P2 — Upcoming
* Accumulate 30 live paper trading days to unlock ML Tier 3.
* Kraken crypto broker regular-user flow (after Alpaca goes live).

### Nice-to-have
* Thinkorswim-style "Terminal Mode" workspace route (dockable panels, ticker tape,
  monospace density).
* One-click "Send me a fresh digest" button in the Pro dashboard for on-demand
  digest preview.
* Admin panel redundancy cleanup (user flagged duplicate buttons in menus — mobile
  menu done; admin panel itself still pending review).

## 7. Test Credentials
See `/app/memory/test_credentials.md`.
