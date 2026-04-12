# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform. Requires real market data, crypto & dark pool data, functional trading broker connections, a Stripe subscription gateway ($45/month), an AI chat assistant, and a highly complex AI market prediction engine that scrapes financial news, crypto transactions, and real estate data. Deep navy `#0F172A` background with blue `#0052FF` accents ("Fidelity × Coinbase" aesthetic).

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **Auth**: httpOnly secure cookies (JWT), 90s fetch timeout for AI endpoints
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini)
- **Payments**: Stripe ($45/month Pro subscription)
- **Market Data**: Alpha Vantage (paid tier) with yfinance fallback, Finnhub
- **Email**: Resend

## Critical Technical Decisions
- **No AbortController in authFetch**: Causes postMessage clone errors with Service Workers
- **90s fetch timeout**: AI endpoints take 8-50s; do NOT lower below 90s
- **Dynamic API base URL**: `getApiBase()` in `/app/frontend/src/utils/apiBase.js` resolves to correct domain on any deployment
- **Auto-refresh JWT**: `authFetch` silently refreshes expired access tokens on 401
- **Dynamic CORS middleware**: Reflects request Origin for any deployed domain (risedual.ai, preview, etc.)
- **No crewai package**: Native thread-safe multi-agent engine (`crew_engine.py`) built using ThreadPoolExecutor + asyncio.to_thread
- **Unified Price Provider**: All market data fetching goes through `price_provider.py` (AV → yfinance → MongoDB cache). Never call Alpha Vantage directly.

## Core Features (All Implemented)
- Real-time stock & crypto tickers
- Options Radar, Dark Pool tables
- AI War Room (unified command center)
- AI Intelligence Hub (Score, Patterns, Quick Brief)
- AI Investment Hypothesis (multi-model consensus)
- Multimodal AI Chat (image upload, chart patterns)
- Perplexity-style Company Research
- Market Predictions (scrapes world events, foreign markets, gov filings)
- Sector Rotation Heatmap
- Realtime P&L Tracker
- 8 Mock Broker Integrations (Alpaca, Schwab, IBKR, MooMoo, Webull, Robinhood, Public, Kraken)
- Stripe Subscription Gateway ($45/month)
- Admin Panel with Cache Monitor
- Referral System with Promo Codes
- Trading Journal, Strategy Builder, Strategy Marketplace
- Prediction Accuracy Tracker (Pro feature)
- Native Multi-Agent AI Crew Engine (War Room, Hypothesis, Predictions)

## Price Provider Integration (April 10, 2026)
- Built `price_provider.py`: Smart routing AV → yfinance → MongoDB cache
- Integrated into ALL backend services (stocks + crypto):
  - `sector_service.py`, `war_room_service.py`, `prediction_tracker.py` (done by previous agent)
  - `ai_intelligence_service.py`, `watchlist_intelligence_service.py`, `company_research_service.py`, `market_data_service.py`, `backtester_service.py` (completed this session)
- Added crypto fallback: AV `CURRENCY_EXCHANGE_RATE` → yfinance `{TICKER}-USD` → MongoDB cache
- Fixed Motor Database boolean check bug (`if _db is not None:` instead of `if _db:`)
- Only remaining direct AV call: Earnings endpoint in war_room_service.py

## Deployment Status
- Health Check: PASSED
- CORS: Dynamic origin reflection for any domain
- API routing: Dynamic getApiBase() for any deployment
- All features verified via testing agent (100% pass)

## AI Sentiment Heatmap (April 10, 2026)
- Built multi-agent sector sentiment crew in `crew_definitions.py` (3 analysts + 1 strategist = 4 LLM calls for all 11 sectors)
- Added `GET /api/sectors/sentiment` endpoint in `routes/sectors.py` (15min cache TTL)
- Updated `SectorHeatmap.jsx` with AI tab: shows scores (0-100), labels (Bullish/Bearish/Cautious/Neutral), reasoning, rotation call, risk regime
- Color mapping: 0-25 red → 25-45 orange → 45-55 neutral → 55-75 green → 75-100 strong green

## Market Vector Memory System (April 10, 2026)
- Built `market_memory_service.py` using ChromaDB + all-MiniLM-L6-v2 (local embeddings, zero API cost)
- Stores past market regime episodes as vectors: price action, macro data, AI predictions, actual results
- Before each prediction, queries for top 3 similar historical regimes and injects context into AI prompt
- Auto-saves verified predictions (hits/misses) from `prediction_tracker.py` as new memory episodes
- New endpoint: `GET /api/accuracy/memory` (Pro only) returns memory stats
- Persistent storage at `/app/backend/data/chromadb`

### Memory Training (Bulk Bootstrap)
- Built `memory_training_service.py`: fetches 2yr daily data for 33 symbols (mega-cap stocks + sector/market ETFs)
- Calculates RSI (14), SMA(20/50) trend, volume signals every 5 trading days
- Tags each regime with actual 5-day forward outcome (hit/miss/neutral)
- **2,973 historical episodes** ingested from 33 symbols
- New endpoints: `POST /api/accuracy/memory/train` (triggers background task), `GET /api/accuracy/memory/train/status`

### Nightly Memory Cleanup (April 10, 2026)
- `nightly_cleanup()` in `market_memory_service.py`: **re-tags** toxic outliers (>80% confidence + wrong) as `toxic_lesson` and prunes data older than 90 days
- Scheduled via APScheduler at 2:00 AM UTC in `server.py`
- Manual endpoints: `POST /api/accuracy/memory/cleanup`, `GET /api/accuracy/memory/cleanup/history`
- `GET /api/accuracy/memory` includes `last_cleanup`, `toxic_lessons`, and `active_episodes` counts
- Fixed ChromaDB date comparison: obsolete data pruning now uses Python-side string comparison (ChromaDB `$lt` only works with numbers)

### Toxic Spikes Alert System (April 10, 2026)
- When `nightly_cleanup()` detects high-confidence failures, triggers BOTH email and in-app alerts
- **Email alerts** via Resend (`send_toxic_spikes_email`) sent to admin + owner with summary table of affected tickers
- **In-app notifications** created for all Pro users in MongoDB `notifications` collection (type=`toxic_spike`)
- Frontend `NotificationItem.jsx` renders toxic spike alerts with red AlertTriangle icon, ticker tags, and red border
- Toxic episodes are **re-tagged** in ChromaDB as `outcome='toxic_lesson'` instead of deleted — preserved as negative lessons
- New function `get_toxic_lessons_context()` allows AI to query toxic patterns and learn "what NOT to do"
- General queries (`query_similar_regimes`) auto-exclude `toxic_lesson` entries
- Notification `user_id` stored as string (matching `get_current_user()` format)

### Strategist Context — Win Pattern Injection (April 10, 2026)
- Added `get_strategist_context()` to `market_memory_service.py`: filters ChromaDB for `outcome='hit'` only
- All 3 AI crews (War Room, Hypothesis, Market Prediction) now inject win patterns into synthesizer prompts
- Format: "HISTORICAL WIN PATTERNS for {ticker} (RSI ~{n})" with similarity scores, dates, and actual outcomes
- Graceful fallback: returns generic patterns if no ticker-specific wins exist

### Dual-Signal Adversarial AI — Edge vs Veto (April 10, 2026)
- Added `get_strategist_veto_context()`: queries ChromaDB for `outcome='toxic_lesson'` (past high-confidence failures)
- All 3 AI crews now inject BOTH success patterns ("Edge") and toxic lessons ("Veto") into synthesizer prompts
- Adversarial Check logic: if current conditions resemble a DANGER pattern more than a SUCCESS pattern, AI must lower confidence below 50%
- AI must explicitly state in its thesis/summary why the current setup is NOT a trap
- Only assigns high confidence (>70%) when conditions mirror SUCCESS patterns with NO overlap to DANGER patterns

### Failure Mode Classification (April 10, 2026)
- Auto-classifies WHY predictions fail using price action heuristics in `_classify_failure()`
- 5 failure modes: TECH_FAKEOUT (stop-loss hunt), MACRO_SHOCK (surprise data), LIQUIDITY_GAP (low volume), REGIME_SHIFT (trend→range), UNKNOWN
- Stored in both MongoDB `predictions.verified_24h.failure_code` and ChromaDB metadata
- Veto context shows `[FAILURE_CODE]` tags so AI knows the specific trap type
- New endpoints: `GET /api/accuracy/failure-modes`, `GET /api/accuracy/failure-breakdown`, `POST /api/accuracy/classify/{id}`
- Nightly cleanup preserves failure_code when re-tagging toxic entries

### AI-Powered Post-Mortem Analysis (April 10, 2026)
- `post_mortem_service.py`: when a prediction fails, fetches Finnhub company news + market news, sends to GPT-4o-mini
- LLM classifies the failure with reasoning + key headline (e.g., "MACRO_SHOCK due to CPI surprise")
- Upgrades the initial heuristic classification with news-aware AI analysis
- Stored in MongoDB `predictions.verified_24h.post_mortem` and `post_mortem_log` collection, plus ChromaDB metadata
- Falls back to heuristic if LLM fails or no API key
- Auto-triggers after each 24h verification in `verify_pending_predictions()`
- Manual trigger: `POST /api/accuracy/post-mortem/{prediction_id}`, History: `GET /api/accuracy/post-mortem/history`

### Real-Time SSE Insight Stream (April 10, 2026)
- `routes/stream.py`: Server-Sent Events endpoint at `GET /api/stream/insights`
- Pushes live events: `new_verification`, `post_mortem`, `toxic_alert`, `memory_update`
- In-memory event buffer (max 100 events) with `GET /api/stream/recent` REST fallback
- Events pushed from: `nightly_cleanup()`, `verify_pending_predictions()`, `run_and_update_post_mortem()`

### Memory Dashboard UI (April 10, 2026)
- `MemoryDashboard.jsx`: Pro-only modal with 4 tabs (Overview, Cleanup, Failures, Post-Mortem)
- Overview: Stat cards (Total Episodes, Active, Toxic Lessons, Hit Rate), Memory Health panel (ChromaDB, Collection, Mongo, Last Cleanup)
- Cleanup: Timeline of cleanup runs with toxic/obsolete badges, expandable toxic details, "Run Now" button
- Failures: Color-coded failure mode breakdown with progress bars + reference guide
- Post-Mortem: AI classification results with reasoning, key headlines, heuristic vs AI comparison
- Accessible from Navbar Platform dropdown and Pro user menu
- Non-Pro users see upgrade wall with Lock icon

### Broker OAuth Admin (April 10, 2026)
- Owner-only admin panel tab to configure Alpaca OAuth Client ID/Secret
- Credentials encrypted with AES-256 (Fernet) before MongoDB storage
- `BrokerOAuthConfig.jsx`: Configure/Update/Delete UI with password input, eye toggle, preview
- Backend reads DB credentials first, falls back to env vars (`_get_oauth_credentials()`)
- Endpoints: `GET/POST/DELETE /api/admin/broker-oauth/{broker_id}`
- OAuth status check: `GET /api/broker/oauth/{broker_id}/status` returns configured state

### Order Flow / Institutional Wall Detection (April 10, 2026)
- **Dual-source architecture**: Crypto → Binance L2 Order Book (api.binance.us, fallback api.binance.com); Stocks → yfinance intraday volume profile (5min bars, 2-day lookback)
- Binance L2 provides real bid/ask depth (500 levels), spread, best bid/ask, and coin quantities
- Wall = price level with volume >3x median (minor) or >5x (major); classified as support or resistance
- Institutional bias: INSTITUTIONAL_BID (>58% bid volume), INSTITUTIONAL_ASK (<42%), BALANCED
- `get_order_flow_context()` returns formatted text injected into all 3 AI crews (War Room, Hypothesis, Prediction)
- Adversarial check updated: "heavy ASK walls = resistance ceiling; heavy BID walls = support floor"
- Frontend `OrderFlowPanel.jsx`: Conditional rendering — shows Spread/Bid/Ask/depth for Binance, POC/price range/bars for yfinance. L2 badge for crypto. Snapshot/Live tab switcher (Live only for crypto).
- API: `GET /api/order-flow/{symbol}` (no auth required, smart routing based on CRYPTO_TICKERS set)

### Real-Time Order Flow Heatmap (April 10, 2026)
- **Live SSE stream** (`GET /api/stream/orderflow/{symbol}`): Backend connects to Binance US WebSocket (`wss://stream.binance.us`) internally, processes L2 depth updates every 1s, and relays to frontend via SSE (K8s-ingress-compatible).
- `orderflow_ws_service.py`: Singleton `OrderFlowStream` manages shared Binance WS connections per symbol, with auto-reconnect, subscriber management, and rolling 60-snapshot history cache.
- **Heatmap visualization**: 24 price bins × 60-second rolling window. Log-scaled intensity (np.log1p) with sqrt-compressed RGB colors. Green = bids (support), Red = asks (resistance), Gray = empty bins.
- **Wall movement detection**: Compares consecutive snapshots to detect wall appearances/vanishes in real time.
- **Bid/Ask pressure bar**: Real-time ratio visualization with smooth CSS transitions.
- **Mid-price indicator**: Yellow border separator between bid and ask zones.
- Frontend `OrderFlowHeatmap.jsx`: EventSource consumer with LIVE badge, auto-reconnect, history pre-load.
- Live tab only available for crypto tickers (BTC, ETH, SOL, etc.); stocks remain snapshot-only.

- Frontend `LiveInsightsFeed.jsx`: EventSource consumer with LIVE badge, auto-reconnect, collapsible feed
- Renders verification hits/misses with failure badges, post-mortem reasoning, toxic spike ticker tags
- Placed after PnL Tracker in the main dashboard layout

### VAPID Web Push Whale Alerts (April 10, 2026)
- Push notifications via `pywebpush` with VAPID keys (configured in `.env`)
- `notify_whale_wall()` triggers browser push when a wall with intensity >= 85 appears in the live Binance stream
- 5-minute cooldown per `{ticker}:{price}` to prevent spam
- `orderflow_ws_service.py` automatically fires whale alerts during SSE streaming
- Frontend: `usePushNotifications.js` hook, `service-worker.js`, toggle in `UserWorkspace.jsx`
- Push works even when the browser tab is closed (VAPID standard)

### 0-100 Intensity Scale (April 10, 2026)
- All order flow walls include `intensity` field: `min(int(((ratio-1)/9)*100), 100)`
- Applied to: Binance L2 (static + live SSE), yfinance volume profile
- Frontend: `Int` column, `W` (whale) badge for >= 85, `M` (major) for < 85 + 5x ratio, `m` (minor) for rest
- Volume display uses K/M suffixes instead of raw decimals

### Multi-Ticker Whale Radar (April 10, 2026)
- SSE endpoint `GET /api/stream/whale-radar`: Subscribes to 10 crypto pairs (BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, DOT, LINK, MATIC) simultaneously via single connection
- Emits 3 event types: `radar_status` (once, lists tickers), `tick` (every snapshot per ticker), `whale` (only when intensity >= 85 walls detected)
- Frontend `WhaleRadar.jsx`: 5-column grid with live ticker tiles showing price, bias icon, whale badges, mini pressure bars, wall counts
- Yellow ring highlights tiles with active whale walls
- Whale Detections feed at bottom shows real-time whale events with prices and intensity scores
- Shared Binance connections via `OrderFlowStream` singleton (no duplicate WebSocket connections)

### Order Flow AI Context Injection (Verified April 10, 2026)
- `get_order_flow_context()` returns formatted text for AI crews
- Injected into War Room (crew_definitions.py:66-90), Hypothesis, and Prediction Synthesizer crews
- Crypto tickers get Binance L2 context (walls, spread, bias); stocks get yfinance volume profile context
- SPY used as macro proxy for market-wide predictions

### Landing Page / Splash (April 10, 2026)
- `LandingPage.jsx`: Full marketing splash page shown to unauthenticated visitors
- Branding: **RISEDUAL AI** is the main product; **TradeAlgoGPT** shown with red strikethrough (`line-through decoration-red-500`) as competitor
- Sections: Header (sticky glassmorphism nav), Hero, How It Works (Strategist/Auditor/Nightly Retraining), Comparison table (RISEDUAL AI vs ~~TradeAlgoGPT~~), Features bento grid, Pricing ($45/mo), Testimonials (3 traders), FAQ accordion, CTA, Footer
- All CTAs trigger `openRegister()` → auth modal on Sign Up tab
- Smooth scroll navigation via anchor links
- Mobile responsive with hamburger menu
- After login → full trading dashboard
- **Verified (Iteration 71)**: All branding correct, auth flow works, 100% pass




### Enriched Regime Format (April 10, 2026)
- Added `market_sentiment_service.py`: Fear & Greed Index (Alternative.me API, free) + VIX level (yfinance)
- Regime snapshots now include structured `{metrics: {rsi, vol_delta, change_1d, trend}, sentiment: {fg_index, fg_label}}`
- Training fetches 730 days of historical F&G data and tags each snapshot by date
- Volume delta: % above/below 20-day average volume
- New endpoint: `GET /api/sentiment/fear-greed` returns live F&G + VIX

## Broker OAuth 2.0 (April 10, 2026)
- Added OAuth 2.0 authorization flow for Alpaca (extensible to other brokers)
- New endpoints: `GET /api/broker/oauth/{broker_id}/status`, `/authorize`, `/callback`
- Frontend: OAuth button shown when configured, URL param callback handling, auth method badge
- `AlpacaTradingService` supports both API key and OAuth bearer token authentication
- CSRF-protected via `oauth_states` collection with one-time state tokens
- To enable: Set `ALPACA_OAUTH_CLIENT_ID` and `ALPACA_OAUTH_CLIENT_SECRET` in backend/.env

## Historical Sentiment Tracking (April 10, 2026)
- Every AI sentiment run is auto-logged to `sentiment_history` collection in MongoDB
- New endpoint: `GET /api/sectors/sentiment/history?limit=20` returns per-sector trend timeseries
- Frontend: Sparkline SVGs show sentiment trend per sector tile, snapshot count badge in header

## Known Limitations
- Broker integrations are mocked (no real OAuth flows)
- Finnhub Congressional Trading API returns 403 on free tier (gracefully handled)
- Binance global (api.binance.com) geo-blocked from some cloud regions; uses Binance US (api.binance.us) as primary with global as fallback
- Crypto prices may return empty when AV rate-limited (no yfinance fallback for crypto exchange rates)

### Dashboard Terminology Alignment (April 10, 2026)
- Updated all AI section headers/subtitles to match landing page "Strategist vs Auditor" and "Adversarial AI" terminology
- AI War Room: "Adversarial AI Command Center — Strategist signals, Auditor validates"
- AI Investment Hypothesis: "Adversarial AI — Strategist generates thesis, Auditor stress-tests it"
- AI Intelligence Hub: "Strategist-powered stock scoring, pattern detection, and instant briefs"
- AI Market Predictions: "Adversarial AI — Strategist predicts, Auditor vetoes weak signals"
- War Room results: "Strategist vs Auditor" header, "Strategist Bull Case", "Auditor Bear Case", "Strategist Catalysts", "Auditor Risk Flags"
- Hypothesis results: "Strategist Catalysts", "Auditor Risk Flags", "Strategist & Auditor Breakdown"
- Navbar Platform dropdown: Added AI War Room (first, red), Order Flow, Whale Radar
- Mobile nav: Added AI War Room, Order Flow, Whale Radar
- **Verified (Iteration 72)**: All terminology correct, 100% pass

### Portfolio Agent with AI Tool Calling (April 11, 2026)
- Upgraded from keyword-injection to proper **agentic tool calling** using GPT-5.2 function calling via LiteLLM + Emergent proxy
- Backend: `portfolio_agent.py` — 6 tools: `get_portfolio_snapshot`, `get_position_detail`, `get_trade_history`, `get_watchlist_news`, `place_paper_order_intent`, `confirm_paper_order`
- **Confirmation-Gated Trading**: AI NEVER executes trades directly. Creates a proposal first → asks user to confirm with proposal ID → only then executes. Proposals stored in MongoDB `pending_orders` collection.
- AI decides which tools to call based on user query (up to 5 iterations)
- Falls back to context injection if tool calling fails
- Non-portfolio queries + image uploads bypass the agent, use standard AI service
- Architecture inspired by user-provided TypeScript starter kits (OpenAI/Anthropic tool calling patterns)
- **Verified (Iteration 74-75)**: 100% pass rate, 31 total tests

### Paper Trading System (April 11, 2026)
- Backend: `paper_trading_service.py` + `routes/paper_trading.py`
- MongoDB collections: `paper_portfolios` (user portfolios), `paper_trades` (trade history)
- Users start with $100K simulated cash
- Live prices: Alpha Vantage (stocks) + Binance US (crypto)
- Endpoints: GET /api/paper/portfolio, POST /api/paper/trade, GET /api/paper/trades, POST /api/paper/reset
- Frontend: `PaperTrading.jsx` modal with Portfolio/Trade/History tabs
- Accessible from Navbar user menu (desktop + mobile)
- AI Chat Integration: `routes/ai.py` detects portfolio keywords (portfolio, positions, P&L, holdings, etc.) and auto-injects real portfolio context into the AI prompt for personalized advice
- **Verified (Iteration 73)**: 100% pass rate (18/18 backend, all frontend)

### Token Swap & Mobile Menu Fix (April 10, 2026)
- Swapped MATIC → SHIB across all 5 files (orderflow_ws_service, whale_radar, WhaleRadar.jsx, order_flow_service, OrderFlowPanel.jsx)
- Fixed mobile hamburger menu: action buttons now use `flex-wrap` with user info on its own line, all buttons readable
- Added AI War Room, Order Flow, Whale Radar to mobile nav grid

### SEO & Search Engine Optimization (April 11, 2026)
- **Meta Tags**: Rich title, description, author, keywords targeting "AI trading platform", "adversarial AI", "options flow", "dark pool", etc.
- **Open Graph + Twitter Cards**: Professional social sharing with image, description, site name
- **Structured Data (JSON-LD)**: 3 schemas — SoftwareApplication ($45/mo pricing, features, rating), Organization (logo, contact), FAQPage (3 Q&As for rich snippets)
- **robots.txt**: Allows indexing, blocks /api/ and /admin
- **sitemap.xml**: 5 URLs (home, features, pricing, comparison, FAQ) with priority/frequency
- **Canonical URL**: Points to `https://risedual.ai`
- **Semantic HTML**: Proper h1→h2→h3→h4 heading hierarchy on landing page

### Vivid Solid Color Palette (April 11, 2026)
- User requested bright solid colors (red, yellow, lime, green) instead of semi-transparent muted ones
- Background darkened to `#060E1F` (near-black navy) for maximum contrast
- Sector heatmap: solid `bg-green-500`, `bg-lime-400`, `bg-yellow-400`, `bg-orange-500`, `bg-red-500` (no opacity)
- All gain/loss indicators app-wide: lime-400 (gains), orange-400 (losses)
- Accent colors: violet → brighter violet-300, amber → amber-300
- Teal accent brightened: `#35D6C8` → `#3DE8D9`
- **Verified (Iteration 78)**: 100% pass, all 11 heatmap tiles + legend confirmed solid

### Contrast Fix for Navy Background (April 11, 2026)
- User reported card borders, text, and legends overpowered by navy `#0A2A63` background
- Card borders: `slate-700/50` → `slate-500/30` (brighter)
- Card surfaces: `slate-800/40` → `slate-700/30` (more opaque)
- Muted text: `slate-500/600` → `slate-400` (brighter)
- Bottom nav: inactive text `slate-400` → `slate-300` (readable)
- Divider borders: `slate-800` → `slate-600/30`
- **Verified (Iteration 77)**: 100% pass, mobile (390px) + desktop (1920px) both verified

### Brand Guide Color Palette Update (April 11, 2026)
- Applied RISEDUAL brand guide colors across all 50+ frontend files (197 color references)
- Primary navy: `#0F172A` → `#0A2A63` (deeper true navy blue)
- Accent teal: `#0052FF` → `#35D6C8` (AI actions, CTAs, active states)
- Hover teal: `#2563EB` → `#67E3D3` (hover/interactive states)
- CSS custom properties added to index.css: `--brand-navy`, `--brand-teal`, `--brand-teal-light`, `--brand-deep-blue`, `--brand-navy-light`
- Dual-identity principle: Teal = AI emphasis, Navy = platform trust
- Landing page already aligned (uses Tailwind `teal-400/500`)
- **Verified (Iteration 76)**: 100% pass, all components + mobile verified

### Card Background Brightness Fix (April 11, 2026)
- User reported "the boxes are too dark" — card backgrounds blended into `#060E1F` body
- Brightened 30+ component files systematically:
  - `bg-[#0B1120]` → `bg-[#111C30]` (container backgrounds)
  - `bg-[#060E1F]` → `bg-[#0F1A2E]` (card-level backgrounds, not body/nav)
  - `bg-slate-700/40-45` → `bg-slate-700/60` (all card surfaces)
  - `bg-slate-700/35` → `bg-slate-700/55` (secondary surfaces)
  - `bg-slate-900/60` → `bg-slate-800/50` (inner metric boxes, badges)
  - `bg-slate-800/40` → `bg-slate-800/55` (landing page cards)
  - StatCard gradients: `/30` opacities → `/50` (MacroShared + PnLTracker)
  - ForeignMarketsTab correlation signals: gradient opacity boosted
  - WatchlistIntelligence summary: gradient opacity boosted
  - MemoryDashboard borders: `slate-800/60` → `slate-600/30`
- Body background stays `#060E1F`; cards now clearly "float" above it
- **Verified (Iteration 79)**: 100% pass, all sections desktop + mobile (390px) confirmed visible

### Gov Filings Scraper Fix (April 11, 2026)
- Fixed Capitol Trades scraper: added `?assetType=stock` filter to return actual stock trades instead of private LLCs
- Fixed HTML parser: using proper CSS selectors (`a.text-txt-interactive`, `h3.issuer-name`, `span` tags) instead of raw text splitting
- Fixed ticker extraction: `GOOGL:US` → `GOOGL` (split on colon)
- Fixed date formatting: `6 Mar2026` → `6 Mar 2026` (regex insert space before 4-digit year)
- Removed broken QuiverQuant fallback (SPA, no server-rendered table)
- All macro data now flows into AI predictions: congressional trades + Fed announcements + insider trades + earnings + world events + foreign markets
- **Verified via API**: 12 congressional trades (real tickers: GOOGL, AVGO, SBUX, META, AAPL), 10 Fed announcements, 20 insider trades, 30 earnings

### Lobbying Data + Fear & Greed Index Integration (April 11, 2026)
- Imported user-provided datasets into MongoDB:
  - `lobbying_data`: 13,340 records (12,189 with amounts >$0) — 1,111 unique stock tickers
  - `fear_greed_index`: 2,393 daily readings (Aug 2018 – Feb 2025)
- Built `lobbying_service.py` — MongoDB aggregation queries (top spenders, by ticker, by issue)
- Built `fear_greed_service.py` — live CNN scraping + historical DB fallback
- Added API routes: `GET /api/lobbying`, `GET /api/lobbying/ticker/{ticker}`, `GET /api/lobbying/top-spenders`, `GET /api/fear-greed`
- Added **Fear & Greed Gauge** to dashboard (SVG arc gauge, 7d/30d averages, 90-day sparkline)
- Added **Top Corporate Lobbying Spenders** table to Congress Trades tab (ticker, client, total spent, filings, issue)
- Fed both datasets into AI prediction engine (lobbying in gov_filings prompt, fear & greed as market sentiment context)
- **Verified (Iteration 80)**: 100% pass (21/21 backend, all frontend), no regressions

### About Us Page (April 11, 2026)
- Created dual-mode `AboutUs.jsx` component: embedded section on Landing Page + full-screen overlay for logged-in users
- Content: Mission statement, stats bar (8+ sources, 2X AI, 24/7, 12K+ lobbying), Core Values (4 cards), Platform Capabilities (6 cards), Team bios (4 placeholder members with gradient avatars)
- Navigation: Landing page nav link "About Us" (scrolls to section), Resources dropdown "About RISEDUAL AI" (opens overlay), Mobile menu "About Us" button
- **Verified (Iteration 81)**: 100% pass, all modes + no regressions

### Voice Chat (TTS + STT) (April 11, 2026)
- Added Text-to-Speech: AI reads responses aloud via OpenAI TTS (Emergent LLM Key)
- Voice selector in chat header: Off → Female (nova) → Male (onyx) → Off cycle
- Added Speech-to-Text: Mic button records audio via MediaRecorder API → Whisper transcription
- Backend: `POST /api/chat/tts` (text→MP3 base64) + `POST /api/chat/stt` (audio upload→text)
- **Verified (Iteration 82)**: 100% pass (8/8 backend, all frontend controls)


### Broker API Key Vault + Role-Based Execution (April 11, 2026)
- All users can connect their own broker API keys (Alpaca, Schwab, IBKR, MooMoo, Webull, Robinhood, Public, Kraken) for read-only portfolio sync
- Only the **owner** account (`managingdirector@redslateholdings.com`) has live trade execution privileges
- Backend: `_is_execution_allowed()` checks `user.role == 'owner'` — gates `POST /order` and `DELETE /order` with 403
- New endpoint: `GET /api/broker/execution-status` returns `{execution_allowed, mode}` for the current user
- Frontend: `AccountDashboard` shows "LIVE TRADING" (lime badge) for owner, "READ ONLY" (amber badge) for others
- "New Order" button and order form only visible to owner; cancel order buttons also owner-only
- Read-only endpoints (account, positions, orders, portfolio-sync) remain accessible to all authenticated users
- **Verified (Iteration 84)**: 100% pass (16/16 backend, all frontend)

### 3-Legged OAuth + Refresh Token Rotation + PKCE (April 11, 2026)
- Full 3-legged OAuth 2.0 authorization code flow: user redirect → broker auth page → callback with code → token exchange
- **Refresh Token Rotation**: On every token refresh, the old refresh token is invalidated and a new one is issued. Rotation events logged in `oauth_token_audit` collection for security audit trail
- **PKCE (S256)**: Proof Key for Code Exchange support for Schwab and IBKR (generates code_verifier/code_challenge pair per authorization)
- **Automatic Token Refresh**: `_get_or_refresh_client()` checks token age, auto-refreshes at 80% of expiry window
- CSRF protection via one-time `state` tokens stored in `oauth_states` collection
- All tokens encrypted at rest with AES-256 (Fernet)
- OAuth configs for Alpaca, Schwab, IBKR (extensible to more brokers)
- Public capability endpoint: `GET /api/broker/oauth/capabilities` — returns full OAuth compliance report
- Per-broker status: `GET /api/broker/oauth/{broker_id}/status` — includes PKCE support flag

### Trade Execution Notifications (April 11, 2026)
- Push notification + in-app notification sent to owner when a live trade is executed
- In-app: stored in `notifications` collection (user_id, type, symbol, side, qty, order_id, broker_id, read flag)
- Push: sent to owner's VAPID subscriptions with order details (symbol, side, qty, broker, status)
- Frontend: toast confirmation on successful order (`sonner` toast with order ID and status)
- Non-blocking: notification failure doesn't block the order response

### Legal Pages — Terms, Privacy, Risk Disclosure, Disclaimer (April 11, 2026)
- Created `LegalPages.jsx` — tabbed modal with 4 comprehensive legal documents
- **Terms of Service**: Account rules, $45/mo subscription terms, brokerage key liability, IP, limitation of liability, indemnification, Florida governing law
- **Privacy Policy**: Data collection (account, payment via Stripe, broker keys AES-256, AI chat, push tokens), third-party sharing (Stripe, Alpha Vantage, OpenAI, Gemini), retention, cookies, user rights
- **Risk Disclosure**: General trading risks, AI prediction limitations, options/crypto risks, dark pool data caveats, paper trading limitations, no guarantee of profits
- **Disclaimer**: Not a broker-dealer/investment advisor, no fiduciary relationship, "as is" warranty, assumption of risk
- Entity: **RISEDUAL CORPORATION**, State of **Florida**, contact: legal@risedual.ai
- Footer links (both landing page and logged-in dashboard) open legal modal to the correct tab
- Sign-up form: Terms consent checkbox must be checked before "Create Account" is enabled; links in consent text open legal modal
- Login form: no checkbox shown
- **Verified (Iteration 85)**: 100% pass (11/11 frontend tests)
### Media Upload / Object Storage System (April 11, 2026)
- Built media upload system using Emergent Object Storage (`emergentintegrations`)
- Backend: `storage_service.py` (init, put, get) + `routes/media.py` (6 endpoints)
- Endpoints: `POST /api/media/upload` (single), `POST /api/media/upload-chunk` (chunked 2MB), `GET /api/media` (list), `GET /api/media/landing-video` (public), `GET /api/media/file/{id}` (download), `DELETE /api/media/{id}` (soft-delete)
- Chunked upload: splits files >2MB into 2MB chunks, assembles on last chunk, cleans up /tmp
- Max file size: 100MB. Supports video, image, audio, PDF
- MongoDB `media_files` collection stores metadata (file_id, storage_path, content_type, size, category)
- Admin Panel: New "Media" tab with MediaManager UI — upload button, category selector (Landing/Commercial/General), file list table with view/delete
- Landing Page: `CommercialVideo` component between Hero and HowItWorks — fetches `/api/media/landing-video`, shows `<video>` player if video exists, gracefully hides if none
- Object storage initialized at server startup via `init_storage()`
- **Verified (Iteration 83)**: 100% pass (14/14 backend, all frontend)


### Code Quality Sweep (April 12, 2026)
- **P0 DONE: eval()/exec() removal** — Already completed by previous agent. `backtester_service.py` uses safe AST evaluator with whitelisted indicators only.
- **P0 DONE: Hardcoded secrets in test files** — 25 test files updated to import credentials from `conftest_creds.py` instead of hardcoding emails/passwords inline.
- **P1 DONE: MD5 → SHA-256** — Replaced weak `hashlib.md5()` with `hashlib.sha256()` in `routes/accuracy.py`, `services/market_memory_service.py`, `services/post_mortem_service.py`.
- **P1 DONE: Index-as-key anti-pattern** — Fixed in 9 React components: `WarRoomCards.jsx`, `OrderFlowHeatmap.jsx`, `MemoryDashboard.jsx`, `DarkPoolData.jsx`, `PaperTrading.jsx`, `PredictionCards.jsx`, `OrderFlowPanel.jsx`, `WhaleRadar.jsx`, `HypothesisResults.jsx`. All use content-based stable keys.
- **P1 DONE: Backend refactoring** — `routes/broker.py` `oauth_callback` (105 lines) split into 4 helpers: `_resolve_origin`, `_exchange_oauth_code`, `_validate_oauth_account`, `_store_oauth_connection`. `routes/accuracy.py` `classify_failure` refactored with extracted `_update_chromadb_failure_code` helper.
- **Verified (Iteration 86)**: 100% pass (15/15 backend, all frontend), no regressions

## Remaining Code Quality Items
- P0: React Hooks missing dependencies — RESOLVED. ESLint scan of all 136 source files returned zero hook warnings. All hooks properly use `useCallback`, `useMemo`, and correct dependency arrays.
- P2: Split large React components — PARTIALLY DONE. Navbar mobile menu extracted to `MobileMenu.jsx` (322→239 lines). StrategyBuilder already well-structured with sub-components. Remaining: AIHypothesis, AdminPanel, App.js are at acceptable sizes (196-214 lines).

### Security Audit Dashboard (April 12, 2026)
- **Backend**: New `routes/security_audit.py` with 5 endpoints under `/api/admin/security/`:
  - `GET /overview` — high-level stats (failed logins, locked accounts, OAuth rotations, broker connections, user counts)
  - `GET /failed-logins` — detailed failed login attempts with lockout status
  - `GET /oauth-rotations` — OAuth token rotation audit log
  - `GET /broker-connections` — active broker connections (sensitive fields excluded)
  - `POST /unlock/{identifier}` — manual unlock for brute-force locked accounts
- **Frontend**: `admin/SecurityAudit.jsx` — admin panel tab with 4 stat cards and 3 expandable sections
- **Access fix**: Admin Panel now accessible by both `owner` and `admin` roles (was owner-only)
- **Navbar refactor**: Mobile menu extracted to `MobileMenu.jsx` component
- **Verified (Iteration 87)**: 100% pass (20/20 backend, all frontend)

### Mobile Menu & Download Bug Fixes (April 12, 2026)
- Fixed source code PDF download in AdminTools.jsx — switched from plain `fetch()` to `authFetch()` with credentials
- Restructured mobile nav menu from `flex-wrap` to `grid-cols-3` layout — clean 3-column grid for all 9 action buttons
- Admin Panel tabs now horizontally scrollable on mobile (`overflow-x-auto`, smaller text `text-xs sm:text-sm`)
- Added `pb-20` bottom padding to mobile menu to prevent overlap with bottom navigation bar
- **Verified (Iteration 88)**: 100% pass — download returns 713KB PDF, all mobile elements readable

### Persistent Chat Memory + RiseDualGPT Rename (April 12, 2026)
- **Renamed TradeGPT → RiseDualGPT** — component renamed to `RiseDualGPTChat.jsx`, header shows "RiseDualGPT"
- **Persistent Chat Memory** (Pro-only):
  - Backend: `chat_memory_service.py` — auto-extracts key facts/preferences from conversations using GPT-5.2
  - MongoDB collections: `chat_memories` (stored memories), `chat_memory_prefs` (user toggle state)
  - AI system prompt injected with memory context: "PERSISTENT MEMORY — Things you remember about this user..."
  - Memory extraction runs asynchronously via `asyncio.create_task()` after each chat exchange
  - CRUD endpoints: `GET /api/chat/memory`, `POST /api/chat/memory/toggle`, `DELETE /api/chat/memory/{id}`, `DELETE /api/chat/memory`
- **Frontend**: Brain icon in chat header (teal when ON, gray when OFF), expandable memory panel with ON/OFF toggle, memory list with per-item delete, "Clear all" option
- Memory enabled by default for Pro users
- **Manual Memory Pinning**: Pin icon on AI message bubbles (max 5 pinned memories). Pinned memories shown with Pin icon in memory panel with X/5 counter. Server-side limit enforced — 6th pin returns 400.
- **"What do you remember about me?"**: Quick-action suggestion in empty chat for Pro users — fills input on click
- **Verified (Iteration 89-90)**: 100% pass (11/11 pin tests + 9/9 memory tests, all frontend)

### Ticker Search for Market Predictions (April 12, 2026)
- Added **ticker-specific search** to AI Market Predictions — the last Adversarial Pipeline section without a search bar
- New backend endpoint: `GET /api/market/prediction/{symbol}` — fetches macro data + ticker-specific price data, runs full adversarial AI crew focused on that asset
- Frontend: Search input + quick-select pills (General Market, AAPL, TSLA, NVDA, BTC, SPY, AMZN, META, GOOGL) with active highlighting
- All 3 Adversarial Pipeline sections (War Room, Hypothesis, Market Predictions) now have search functionality
- **Bug Fix**: Ticker-specific predictions no longer show "SPY" — crew prompts, synthesizer, PredictionCard, and AdversarialHub all use the actual searched ticker
- **Verified (Iteration 91-92)**: 100% pass (6/6 ticker tests, all frontend + regression)

### Code Quality Sweep Round 2 (April 12, 2026)
- **CRITICAL: 5 test file syntax errors fixed** — IndentationError in test files 53, 61, 62, 68, 74. Root cause: previous credential centralization script injected imports inside indented function bodies.
- **CRITICAL: Remaining hardcoded secrets fixed** — test_iteration89/90 now use conftest_creds.py. All test files verified clean.
- **eval()/exec() in test_iteration36** — confirmed these are security regression tests (they verify dangerous code is REJECTED). No fix needed.
- **Index-as-key fixed** — LandingPage.jsx (stars, FAQ items), WarRoomCards.jsx (risks). Zero index-as-key remaining across all components.
- **ai.py chat() refactored** — 85-line function split into 4 focused helpers: `_enforce_rate_limit`, `_ensure_session`, `_get_memory_context`, `_trigger_memory_extraction`. Main chat handler now 35 lines.
- **whale_radar.py refactored** — Deep nesting (depth 5) split into 3 helpers: `_build_whale_event`, `_build_tick_event`, `_process_snapshot`. Queue polling flattened with early `continue`.
- **RiseDualGPTChat.jsx split** — 424→343 lines. Extracted `ChatHeader.jsx` (53 lines) and `MemoryPanel.jsx` (71 lines) into `chat/` sub-components.
- **Verified (Iterations 93-94)**: 100% pass (10/10 + 11/11 backend, all frontend, no regressions)

### Beta Waitlist System (April 12, 2026)
- **Backend**: `waitlist_service.py` + `routes/waitlist.py` — 7 endpoints (4 public, 3 admin)
  - Priority Score: `position - (referral_count * 20)`. Lower = higher priority. 1 referral skips 20 spots.
  - Public: `POST /join`, `GET /status/{code}`, `GET /leaderboard`, `GET /stats`
  - Admin: `GET /admin/list`, `POST /admin/invite` (batch), `POST /admin/select-founding` (Founding 100)
- **Frontend**: `WaitlistModal.jsx` — join form → status view with rank, referrals, priority score, referral link, Share on X/LinkedIn
- **Admin Panel**: `WaitlistAdmin.jsx` — stats grid, batch invite control, Founding 100 selection button, sortable entry list
- **Landing Page**: All CTAs changed from "Get Started"/"Start Free Trial" to "Join Waitlist"/"Join the Waitlist"
- **App.js**: WaitlistModal opens on landing page CTA clicks (replaces AuthModal for unauthenticated users)
- MongoDB collections: `waitlist` (entries), `waitlist_counter` (atomic position sequencing)

### Waitlist Auto-Invite Cron + Email System (April 12, 2026)
- **Daily Cron Job** (9:00 UTC): `_run_waitlist_auto_invite()` via APScheduler — auto-invites top 5 users by priority score
- **Beta Access Keys**: Secure `BETA-XXXX-XXXX-XXXX` format via `secrets.token_hex()`, stored with 7-day expiry
- **War Room Invite Email**: Subject: "You've been bumped to the front: Welcome to the War Room." — includes rank, referral stats, beta key, CTA button
- **Referral Success Email**: Triggered async when someone joins via referral link — shows new rank, spots skipped, referral count
- **Admin Manual Trigger**: `POST /api/waitlist/admin/auto-invite` (batch_size 1-20) for on-demand invites
- Both emails use Resend API with branded HTML templates matching RISEDUAL's visual identity
- **Verified (Iteration 96)**: 100% pass (14/14 backend, all frontend)

### Beta Key Redemption + Embeddable Widget (April 12, 2026)
- **Beta Key Redemption**: `POST /api/auth/redeem-beta-key` — validates key, checks expiry (7 days), creates Pro account (30-day trial), marks waitlist entry as 'active'
- **AuthModal**: Added 3rd tab "Beta Key" with monospace key input, email, name, password fields, "Activate Beta Access" button
- **UX Flow**: WaitlistModal → "Have a beta key? Redeem it here" → opens AuthModal on Beta Key tab
- **Embeddable Widget**: `GET /api/waitlist/embed/widget.js` — self-contained JavaScript that creates a full waitlist form on any external site. Supports referral codes via `RiseDualWaitlist.init('container', {ref: 'CODE'})`
- **Admin Embed Snippet**: Copyable HTML embed code in Admin Panel Waitlist tab
- **Verified (Iteration 97)**: Backend 100% pass, frontend UX gap fixed (beta key entry point added)

### Code Quality Sweep Round 3 (April 12, 2026)
- **Hardcoded secrets**: Fixed 5 newer test files (93-97) — all now use conftest_creds.py. Zero hardcoded credentials remaining.
- **`is N` → `== N`**: Fixed incorrect identity comparisons across test files
- **Empty catch blocks**: WhaleRadar.jsx (3 catches) and WaitlistAdmin.jsx (1 catch) now log warnings/errors
- **Insecure random**: `polygon_dark_pool_service.py` now uses `hashlib.sha256` for deterministic seeding instead of `hash()`
- **redeem_beta_key refactored**: Extracted `_validate_beta_key()` helper — validation + expiry + duplicate checks separated
- **embed_widget_js refactored**: 72-line inline JS template → separate file `templates/waitlist_widget.js` with placeholder replacement
- **Verified (Iteration 98)**: 100% pass (13/13 backend, all frontend, no regressions)

### Code Quality Sweep Round 4 (April 12, 2026)
- **Index-as-key**: Final 7 instances fixed in CompanyResearch.jsx (5) and SectorHeatmap.jsx (2). Zero remaining across entire codebase.
- **Dynamic `__import__()`**: Replaced with proper imports in `sector_service.py` and `accuracy.py` (2 instances)
- **`_refresh_oauth_token`**: Split from 77 lines into 3 focused helpers: `_request_token_refresh`, `_log_token_rotation` + main (now 35 lines)
- **OrderParams dataclass**: Already existed and in use — confirmed correct pattern for broker `place_order` args
- **Verified (Iteration 99)**: 100% pass (8/8 backend, all frontend)

### P2 Resolution: SectorHeatmap + server.py (April 12, 2026)
- **SectorHeatmap.jsx**: 382→163 lines (57% reduction). Extracted into `heatmap/` folder:
  - `HeatmapHeader.jsx` (56 lines) — period buttons, refresh, AI/performance mode toggle
  - `HeatmapLegend.jsx` (43 lines) — color scale legends for both modes
  - `SectorTile.jsx` (119 lines) — tile rendering with AI sentiment + performance variants, sparklines
- **server.py**: 360→253 lines, imports reduced from **56 to 12**. Extracted `route_registry.py` (98 lines):
  - `register_all_routers(app)` — registers all 25 route modules
  - `wire_db(db)` — passes MongoDB to all route and service modules
- **Verified (Iteration 100)**: 100% pass (18/18 backend, all frontend, no regressions)

### Login Access + Admin/Owner Waitlist Guard (April 12, 2026)
- **Login accessible from landing page**: Added "Log In" link to desktop and mobile navbar on the landing page. Opens AuthModal directly (bypasses waitlist).
- **Admin/Owner blocked from waitlist**: `join_waitlist()` now checks `db.users` for admin/owner role BEFORE adding to waitlist. Returns `blocked: true` with message. Any accidentally-added entries are auto-cleaned.
- **Frontend**: WaitlistModal handles `blocked` response with toast notification.
- **Verified (Iteration 101)**: Login + waitlist guard working

### User Badges — Creator, Founding 100, Beta (April 12, 2026)
- **UserBadge component** (`UserBadge.jsx`) — reusable badge with priority rendering:
  - **Creator** (Owner): Crown icon, amber/gold gradient — "Creator — Managing Director"
  - **Creator** (Admin): Shield icon, orange/red gradient — "Creator — Admin"
  - **Founding 100**: Sparkles icon, violet/purple gradient
  - **Beta**: Zap icon, teal
  - **Pro/Free**: Existing behavior as fallback
- **Backend**: `user_response()` now returns `founding_member` and `beta_access` fields
- **Navbar**: UserBadge shown in user dropdown menu + mobile menu
- **Verified (Iteration 102)**: 100% pass — all badge variants render correctly

### Login Hardening (April 12, 2026)
- **P0 Fix**: Hardened login endpoint against production-specific database state issues
- `check_brute_force`: Now handles naive datetimes (normalizes to UTC-aware), catches all exceptions, and clears corrupt records
- `login()`: Separately checks for missing `password_hash` before calling `verify_password`; catches bcrypt exceptions
- `seed_admin`: Handles missing `password_hash` with try/except for both admin and owner accounts
- Fixed Motor DB boolean bug in `orderflow_ws_service.py` (`if not self._db` → `if self._db is None`)
- All error paths return proper 401/429 status codes, never 500
- **Verified (Iteration 103)**: 100% pass (13/13 backend, frontend login flow confirmed)

### Code Quality Sweep Round 5 (April 12, 2026)
- **Hardcoded secrets**: Fixed 5 newer test files (99-103) — all now use `conftest_creds.py`. Zero hardcoded credentials remaining across entire test suite.
- **Empty catch blocks**: Fixed 6 components (MediaManager, Watchlist, PaperTrading x2, MemoryDashboard) — all now log with `logger.warn`
- **Console statements**: Replaced 11 direct `console.log/warn/error` calls in 6 components with production-safe `logger` utility. Only `ErrorBoundary.jsx` keeps `console.error` intentionally for production error tracking.
- **Type hints**: Added `FastAPI`, `AsyncIOMotorDatabase`, `Any` type hints to `route_registry.py`
- **get_ticker_prediction refactored**: Extracted `_fetch_ticker_context()` and `_log_ticker_prediction()` helpers — main handler reduced from 72 to 40 lines
- **Already resolved (no action needed)**: `eval()/exec()` (safe AST evaluator), React hook deps (ESLint passes 0 warnings), index-as-key (0 instances), `is` vs `==` (only in comment/print strings), `OrderParams` dataclass (already existed and in use)
- **Verified (Iteration 104)**: 100% pass (16/16 backend, all frontend, no regressions)

### Code Quality Sweep Round 6 — Component Splitting (April 12, 2026)
- **AdminPanel.jsx**: 219→131 lines. Extracted `admin/UsersTab.jsx` (99 lines) — users table with search, role badges, action buttons.
- **App.js**: 204→187 lines. Extracted `ModalManager.jsx` (59 lines) — centralizes all 15 modal renderings.
- **AIHypothesis.jsx**: 197→167 lines. Extracted `utils/exportHypothesis.js` (36 lines) — report export utility.
- **FilterPanel.jsx**: Replaced inline `[value]` arrays with `useMemo` for stable Slider references.
- **BacktestResults.jsx**: Fixed index-as-key in chart Cells — now uses `entry.month`.
- **ai_intelligence_service.py**: Extracted shared `_fetch_symbol_context()` helper — deduplicates data fetching across all 3 AI intelligence functions. Removed redundant `LlmChat` imports (already uses shared `_call_llm`).
- **Verified (Iteration 105)**: 100% pass (9/9 backend, all frontend, no regressions)

## Backlog
- P1: Waitlist analytics dashboard (daily signups, referral conversion rate)
- P1: Deploy to `risedual.ai` custom domain (user confirmed "Yes deploy")
- P2: Badge showcase on public user profiles & Leaderboard with badge visibility
- P3: Alpha Vantage API upgrade guidance
