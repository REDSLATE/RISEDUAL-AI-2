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

### Portfolio Risk Engine + NewsAPI.ai Metadata Flags (COMPLETED Feb 20, 2026)
- **Portfolio Risk Engine** (`services/trading_bot_service.py`): global
  caps on top of the existing per-trade `MAX_POSITION_USD=$2000` limit.
  `MAX_PORTFOLIO_EXPOSURE=$3000` (aggregate notional across open
  positions) and `MAX_CONCURRENT_TRADES=5` (concurrency). New helpers
  `get_total_exposure`, `get_open_trade_count`, and
  `apply_portfolio_constraints` (concurrency-cap-first, then shrink to
  remaining headroom). `execute_signal(..., open_positions=...)` gains
  an opt-in kwarg; when supplied, trades that exceed either cap skip
  with `reason="portfolio limits reached"` (no broker order fires).
  Omitting the kwarg preserves backwards-compatible behaviour.
- **NewsAPI.ai metadata flags**
  (`services/search_war_room/adapters/newsapi.py`): opt-in fields
  from the NewsAPI onboarding email — `includeArticleImage`,
  `includeArticleConcepts`, `includeArticleCategories`,
  `includeSourceRanking`, `includeSourceImage`. Response parser now
  surfaces `image`, `source_image`, `source_ranking` (Alexa rank),
  top-3 `concepts` (label/type/score), and top-2 `categories` on
  each item — used by the War Room UI for richer cards.
- **Tests**: 25 new tests — `test_portfolio_risk_engine.py` (21) and
  `test_newsapi_metadata_flags.py` (4). Full regression green:
  **195/195 passing** across the Tier 3 / conviction / calibration /
  patterns / polygon test surface.

### risedual_core Refactor Overlay (COMPLETED Feb 19, 2026)
- User uploaded a pre-tested refactored zip of `risedual_core`. I did NOT
  apply blindly — verified:
  - Current hypothesis_logger.py had a **silent bug**: importing
    `risedual_core.ml.patterns` (doesn't exist locally), caught by a broad
    `except` — so chart-pattern enrichment was silently failing in prod.
    The upload ships `ml/patterns.py` (708 lines) and FIXES this.
  - The upload supersedes my prior `_parse_response` extractions in
    anthropic.py/openai.py with a cleaner module-level pure function
    version. Both approaches achieve the same goal; the module-level one
    is better (pure function, no self dependency).
  - Added `_handle_retry` helper in `clients/base.py`.
  - `pyproject.toml` adds optional `keyvault = ["cryptography>=42.0.0"]`
    extras — non-breaking.
  - Upload deletes `ml/calibration_gate.py`; preserved our existing compat
    shim because `scripts/backtest.py` still imports from it.
- **Safety process**: backup to `/tmp/risedual_core.backup.*`, overlay
  files, clear `__pycache__`, restart backend.
- **Verified post-overlay**:
  - All 8 changed modules import cleanly.
  - Backend restarts with no errors.
  - `/api/crypto/prices`, `/api/fear-greed`, `/api/stocks/quote/AAPL` all 200.
  - `detect_all_patterns()` returns 8 pattern classifications on a sample
    5-row OHLCV (previously silently failed).
  - 0 undefined names across `risedual_core/`, lint 100% clean.

### Conviction Calibration Admin UI (COMPLETED Feb 20, 2026)
- New admin Insights tab **Conviction** reading `GET /api/admin/conviction/calibration?days={7|30|90}`.
- Backend endpoint buckets verified predictions (`verified_24h.correct` set) by either `conviction.score` (primary) or `confidence` (fallback), returning per-bucket totals, correct counts, win-rate, and a monotonic-health boolean.
- `prediction_tracker.log_prediction()` now accepts an optional `conviction` dict; field only persisted when supplied.
- Live at ship: 182 verified / 30d, confidence curve monotonic=true (Low — · Medium 52.7% 87/165 · High 88.2% 15/17). Conviction buckets empty pending call-site wiring.
- Files: `backend/services/prediction_tracker.py`, `backend/routes/admin.py`, `frontend/src/components/admin/ConvictionCalibration.jsx`, `frontend/src/components/AdminPanel.jsx`.
- Follow-up (P1 backlog): pass conviction dict from `routes/ai.py` & `routes/intelligence.py` when logging predictions so the `by_conviction` badge activates.

### Chat Component Split (COMPLETED Feb 19, 2026)
- Split `chat/ChatComponents.jsx` (440L monolith) into three focused files
  while preserving backward-compat imports via a 7-line shim:
  - `chat/ChatMessages.jsx` (238L) — message list + bubble + chip/action
    adoption telemetry.
  - `chat/ChatInput.jsx` (188L) — input bar, voice recording, gap-hint
    banner. Exports as both `ChatInput` and `ChatInputArea` (legacy alias).
  - `chat/VoiceSelector.jsx` (30L) — voice toggle.
  - `chat/ChatComponents.jsx` (7L) — thin re-export shim.
- E2E verified: chat opens, messages list renders, input accepts "hello".

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

### Nice-to-have
* Thinkorswim-style "Terminal Mode" workspace route (dockable panels, ticker tape,
  monospace density).
* One-click "Send me a fresh digest" button in the Pro dashboard for on-demand
  digest preview.
* Admin panel redundancy cleanup (user flagged duplicate buttons in menus — mobile
  menu done; admin panel itself still pending review).

## 7. Test Credentials
See `/app/memory/test_credentials.md`.


## 7b. Deployment Journal

Running log of what's shipped vs. queued lives in
`/app/memory/DEPLOYMENT_NOTES.md`. Agents must append to the "Queued for
next deploy" section at the end of every meaningful change. When the user
deploys, they run `/app/scripts/mark-deployed.sh "label"` to snapshot the
queue into a timestamped "Shipped" block.


## 8. Changelog

### 2026-04-19 — Sliding-TTL price cache + prediction dedup
* **New service `services/sliding_cache.py`.** Thread-safe, process-local,
  O(1) get/set with sliding TTL — each access resets expiry. Shared across
  sync + async price-provider entrypoints so rapid pulls on the same symbol
  never double-fetch upstream.
* **`price_provider` wired to sliding cache.** All five entrypoints use it:
  `get_quote`, `get_quote_sync`, `get_crypto_quote`, `get_crypto_quote_sync`
  (5 min TTL), and `get_daily_history` / `get_daily_history_sync` (30 min TTL).
  First miss hits upstream (~250 ms); repeat hits return in ~0 ms with
  `source="<provider>:hot"` suffix. MongoDB persistent cache retained for
  cross-restart warm-up.
* **Reset caps on both sliding mechanisms.**
  - `SlidingCache` now supports `max_resets`; the shared `price_cache`
    singleton is configured with `max_resets=2` (5 min TTL × 3 touches =
    ~15 min max lifetime). Past the cap, reads still return the cached
    value but stop extending — the entry ages out and forces a fresh
    upstream fetch.
  - `log_prediction` caps `dedup_count` at `MAX_DEDUP_HITS=1`
    (15 min TTL × 2 touches = ~30 min max sliding lifetime). A third
    identical firing after the cap creates a **new** prediction record
    and verifies against the current price — catching drift that a
    perpetually-sticky signal would otherwise hide.
* **Data cleanup.** Dropped 49 duplicate SPY@$679.46 NEUTRAL predictions
  left over from a previous session's runaway logger.

### 2026-04-19 — Dynamic NEUTRAL tolerance + Live bot execution wiring
* **Dynamic per-symbol NEUTRAL tolerance.** Replaced flat 5% (1w) / 2% (24h) bands
  with ATR-based adaptive bands in `services/prediction_tracker.py`:
  `tolerance = 1.5 × 10-day-ATR%` (24h) or `3 × ATR%` (1w), clamped to [2%, 10%].
  Cached 12h per symbol.
* **Retro rescore endpoint.** `POST /api/accuracy/rescore-neutral?window={24h|1w|both}`
  (owner-only). Persists `neutral_tolerance_used` + `rescored_at` on each touched record.
* **Live bot execution.** `services/trading_bot_service._execute_bot_trade` now
  routes `mode="live"` through `routes.broker._get_or_refresh_client` →
  `client.place_order()`. Grid, Signal, and Webhook bots all execute live.

### 2026-02-18 — Watchlist.jsx refactor complete
* `Watchlist.jsx` reduced from 447-line monolith to 58-line orchestrator.
* Logic extracted to `/app/frontend/src/hooks/useWatchlistData.js` (all fetches,
  localStorage persistence, backend sync, 60s quote refresh, SMS score/history,
  external `risedualai-add-watchlist` event listener).
* UI split across `/app/frontend/src/components/watchlist/`:
  - `WatchlistToolbar.jsx` (header, expand/collapse, add input, cap warning, share/refresh)
  - `SmartMoneyShiftAlerts.jsx` (score-shift alert rows w/ chat prefill + War Room deep-link)
  - `WatchlistTable.jsx` (row rendering + sparkline + Smart Money pill + actions)
* Lint: 0 issues. Smoke test: collapsed + expanded states render, 5 rows + 2 SMS
  shift alerts visible for admin account.
* `managingdirector@redslateholdings.com` marked DEACTIVATED in test_credentials.md
  (do not re-enable).


### 2026-02-18 — Social share / OG preview per ticker
* **Backend**: `GET /api/share/{ticker}` returns a server-rendered HTML page
  with full OpenGraph + Twitter Card + JSON-LD `FinancialProduct` metadata,
  live quote-enriched title (`AAPL · RISEDUAL AI — AI War Room — $270.23 ▲2.59%`)
  and description. Meta-refresh + JS redirect bounces real browsers to
  `/?warroom=TICKER`. Honors `X-Forwarded-Proto`/`X-Forwarded-Host` so canonical
  URL + SPA redirect use the public domain (not cluster-internal). HEAD supported
  for preview crawlers that probe before GET.
* **Frontend**: `App.js` intercepts `?warroom=TICKER` query param on load and
  dispatches the same nav+warroom events `deepLink.js` uses — SPA auto-opens the
  AI War Room with the ticker queued for analysis. URL is cleaned via
  `history.replaceState` so manual reloads don't re-fire.
* **Share button**: Added to `WarRoomHub.jsx` header (next to the v2 badge).
  Label reflects current ticker ("Share AAPL"). Uses Web Share API on mobile
  (native X / iMessage / WhatsApp / Mail / Slack / Signal sheet) and falls back
  to clipboard + toast on desktop.
* **Platform coverage** (via standard OG + Twitter tags):
  X, Facebook, LinkedIn, WhatsApp, iMessage, Slack, Discord, Telegram, Reddit,
  Bluesky, Pinterest, Signal, Teams — plus Google rich results via JSON-LD.
* Smoke-tested with `facebookexternalhit`, `Twitterbot`, `LinkedInBot` User-Agents
  — all receive correct meta tags. Live redirect test: share URL → SPA → War
  Room opens with ticker auto-analyzed. 0 lint issues.

### 2026-02-18 — Onboarding tour positioning + share endpoint tests
* **Tour polish**: `OnboardingTour.jsx` — centered steps now pin to the top of
  the viewport (top: 80px, horizontally centered) instead of blocking the
  middle of the screen. Card is translucent (`bg-[#0B1426]/85 backdrop-blur-md`),
  scrim reduced from 70% → 25% black, so users can actually see what's being
  tour-ed while the tooltip guides them.
* **Share endpoint tests**: `/app/backend/tests/test_share_endpoint.py` —
  11-test pytest suite covering: 200 HTML response, all required OG tags,
  Twitter Card tags, JSON-LD FinancialProduct, ticker case normalisation,

### 2026-02-18 — Share link referral attribution
* **Backend**: `/api/share/{ticker}` accepts optional `?ref=CODE` query param
  (alphanumeric + dash, 1-32 chars, XSS-sanitised). Preserved through the SPA
  redirect URL as `/?warroom=TICKER&ref=CODE`.
* **Frontend App.js**: `?warroom=` handler now preserves the `?ref=` param
  instead of stripping it, so `useReferralCapture` + AuthModal pick it up at
  signup for credit attribution.
* **Frontend WarRoomHub.jsx**: lazily fetches the authenticated user's
  referral code via `/api/referral/info` and appends `?ref=CODE` to every
  copied share URL. Every Pro user becomes a passive growth engine — click on
  their shared analysis → visitor signs up → referrer credited automatically.
* **Tests**: +5 new pytest cases in `test_share_endpoint.py` for ref
  preservation, sanitisation, dash-prefixed codes, overlong rejection, and
  default-no-ref behaviour. **16/16 pass.**

  SPA redirect, input sanitisation (overlong + special chars), X-Forwarded-Host
  handling, cluster-internal host fallback, HEAD method support. **11/11 pass**.


### 2026-02-18 — Terminal Mode, OG PNG generator, Share ROI badge
* **Terminal Mode workspace** (`/app/frontend/src/components/hubs/TerminalModeHub.jsx`):
  Thinkorswim-inspired 2×2 dockable grid (Watchlist / Market Signals / War Room
  deep-link hints / Headlines stream). Drag splitters reapportion layout;
  positions persist to localStorage. Monospace typography (JetBrains Mono),
  tight density. Header includes live SPY/QQQ/IWM/VIX pulse + ET session clock.
  Added to Tools menu (`nav-terminal-btn`) and reachable via
  `navigateTo('terminal')` event. Lazy-loaded for fast initial paint.
* **Dynamic OG PNG generator** (`/api/share/img/{ticker}.png`): 1200×630 PNG
  composited server-side with Pillow — brand gradient background, huge
  monospace ticker, live price, colored % change (lime up / orange down), teal
  "Open War Room →" pill CTA. 60s in-memory cache keyed by ticker+price-bucket.
  Share HTML now points `og:image` + `twitter:image` at this endpoint instead
  of the static logo — every share on X/Slack/LinkedIn/WhatsApp/Discord now
  renders a bespoke live-price card.
* **Share button ROI badge** (`WarRoomHub.jsx`): Share button lazy-fetches
  `/api/referral/info` and surfaces the authenticated user's `completed_referrals`
  as a lime pill badge on the button (e.g. "Share AAPL · 3"). Tooltip reads
  "3 signups via your links so far" — turning a one-off action into a habit
  loop by giving the user continuous social-proof feedback on their shares.
* **Regression check**: `test_share_endpoint.py` 16/16 pass. All key endpoints
  (ready, quote, sectors, share HTML, share PNG, share with ref, referral
  leaderboard) return 200 + correct payloads.
* Lint: 0 issues across all new/modified files.

### 2026-02-18 — On-demand digest "Send me one now" button
* **Backend**: `POST /api/digest/send-now` — authed users trigger an immediate
  personalized digest delivery to their own inbox. Rate-limited to 1/hour via
  a `last_on_demand_digest_at` timestamp on the user doc. Bypasses the opt-out
  flag because the request is explicit. Returns content summary (predictions,
  smart-money, alerts, watchlist-intel) so the UI can toast-display it.
* **Digest service**: extracted `send_digest_to_user(db, user)` helper (reuses
  `collect_digest_data` + `build_digest_html` + `_routed_send`) so
  single-user sends don't duplicate logic from the scheduled job.
* **Frontend** (`UserWorkspace.jsx`): new teal "Send me one now" button next
  to the existing subscribe/unsubscribe toggle. Shows spinner while sending,
  `sonner` toast on success ("Fresh digest is on its way — 5 predictions, 6
  smart-money alerts, 2 market alerts"), handles 429 gracefully, and a
  subtitle clarifies the button exists ("Morning briefing at 6:00 AM UTC ·
  on-demand preview available").
* **Tested**: Live API returns 200 on first call (admin@risedual.ai received
  digest with 5 predictions + 6 smart-money + 2 alerts + overview), 429 on
  second with clean retry-after message.



### 2026-02-18 — Admin Panel cleanup
* `AdminPanel.jsx`: full refactor to eliminate duplicate/misleading controls
  and improve scanability with 12 tabs.
* **Context-aware header** — title shows `Admin · {TabLabel}` and subtitle
  adapts per tab (`50 users total` on Users, `MongoDB cache tiers & TTLs` on
  Cache, `Market-data + email failover health` on Providers, etc.). Was
  always-stale `{users.length} users total`.
* **Scoped Refresh button** — the header's RefreshCw only appears on the
  Users tab, where it actually refreshes the user list. On Cache / KeyVault /
  ChipAdoption / HelpSearch / etc., each tab already has its own context-
  specific Refresh button, so the header one was a misleading no-op.
* **Proper close button** — literal "x" character replaced with lucide `X`
  icon in a rounded hover button. `data-testid="admin-close-btn"` for tests.
* **Grouped tabs** — 12 tabs now organised under three subtle group labels
  (PEOPLE · OPERATIONS · INSIGHTS) with thin dividers in the tab bar. No
  behavioural change, just scanability.
* **Replaced nested ternary** — 12-way `tab === 'x' ? <X/> : tab === 'y' ? …`
  cascade replaced with `TAB_COMPONENTS` lookup object. ~40 lines shorter,
  trivially extensible.
* Lint: 0 issues. Live-verified with screenshots — tab switching updates
  header title, subtitle, and refresh-button visibility correctly.

### 2026-02-18 — Mobile: Connect Broker entry-point
* **Bug**: `<BrokerConnect />` was rendered only inside the desktop-only
  `<div className="hidden lg:flex">` block of Navbar, so the Connect Broker
  button was completely absent on mobile — users couldn't wire Alpaca/Kraken
  from their phones. Reported by user on deployed site where they needed to
  re-paste Kraken keys from mobile.
* **Fix**: added a window-event handshake — `BrokerConnect` listens for
  `risedualai-open-broker-connect` and opens its own modal. Added a prominent
  teal "Connect Broker" chip in the MobileMenu utility row (next to Admin + Help)
  that dispatches the event. Clean, zero-duplication — BrokerConnect's own
  state + portal modal handle the rest.
* **Verified**: mobile viewport (414×896) → hamburger → Connect Broker chip →
  Broker modal opens with all brokers listed. 0 lint issues.




### 2026-02-18 — Removed Red Slate deactivated account (root cause of READ ONLY bug)
* **Bug**: deployed site showed broker "READ ONLY" for `admin@risedual.ai`
  because `_is_execution_allowed()` required `role == "owner"`, but the seed
  logic had created that account with `role: "admin"`. The `owner` role was
  assigned only to `managingdirector@redslateholdings.com`, which the user
  deactivated in Feb 2026 — leaving production with no live `owner` account
  and every broker connection locked to read-only.
* **Fix**:
  - `/app/backend/.env`: `OWNER_EMAIL=admin@risedual.ai`, removed now-unused
    `ADMIN_EMAIL` / `ADMIN_PASSWORD` vars.
  - `/app/backend/routes/auth.py` `seed_admin()`: consolidated to a single
    owner seed. On every startup, promotes `admin@risedual.ai` to
    `role: owner, is_active: True`. Added one-shot cleanup that deletes any
    remaining Red Slate row with `role in [merged, free]` — no more
    resurrection, no more confusion.
* **Verified on preview**: Red Slate row deleted, `admin@risedual.ai`
  role=owner, is_active=True, and `GET /api/broker/execution-status` returns
  `{execution_allowed: true, mode: "live"}`.
* **On production deploy**: seed cleanup runs automatically → Red Slate row
  deleted → `admin@risedual.ai` promoted to `owner` → broker flips from
  READ ONLY → LIVE TRADING with no manual intervention.


### 2026-02-18 — Code review triage & genuine cleanups
* External code review flagged 200+ findings; auditing them surfaced that the
  "critical" items (eval/exec, 18 undefined vars, hardcoded secrets) are all
  **false positives** from a context-blind static scanner:
  - "eval() in backtester_service.py:193" → line is a comment announcing the
    AST-based safe evaluator that already replaced eval.
  - "eval/exec in test_iteration36" → security tests that verify the evaluator
    REJECTS eval/exec strings (intentionally split `"ev"+"al"`).
  - "18 undefined variables" → `pyflakes .` returns empty.
  - "Hardcoded secrets" → mostly env-var NAMES (`"RESEND_API_KEY="` searched
    inside .env) or dev-only preview passwords from test_credentials.md.
* **Genuine cleanups performed**:
  - `tests/conftest_creds.py`: consolidated — `OWNER_EMAIL` now aliases to
    `ADMIN_EMAIL` (both point to `admin@risedual.ai`) after Red Slate removal.
  - `tests/test_iteration134/135`: moved to `os.getenv()` + safe defaults.
  - `tests/test_iteration135`: removed dead `owner_session` fixture
    (referenced deleted Red Slate account, never consumed).
  - `tests/test_iteration42`: updated stale assertion to pass after Red Slate
    cleanup.
  - `utils/deepLink.js` + `utils/recentTickers.js`: replaced 6 empty
    `/* silent */` catch blocks with `console.debug()` so real failures are
    still observable.
* **Deferred to post-launch** (refactoring risk vs reward): 207 hook-dependency
  warnings (~70% false positive), AppContent/Navbar component splits,
  localStorage "encryption" (already non-sensitive), 544 `is` vs `==` lint
  nits in tests, type-hint coverage, inline-prop useMemo micro-perf.
* Regression: `test_share_endpoint.py` 16/16 pass. Lint: 0 issues.
