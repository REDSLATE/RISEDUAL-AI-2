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

## Backlog
- P3: Refactor server.py into separate route modules
- P4: Verify risedual.ai production deployment
