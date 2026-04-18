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

### Help Center v2 (COMPLETED Feb 18, 2026)
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
