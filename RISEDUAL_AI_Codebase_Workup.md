# RISEDUAL AI — Complete Codebase Workup
**Generated: April 10, 2026**

---

## 1. OVERVIEW

**RISEDUAL AI** is a production-grade AI-powered trading intelligence platform built on React + FastAPI + MongoDB + ChromaDB. It provides real-time market data, multi-agent AI predictions, institutional order flow analysis, strategy building, and a Stripe subscription gateway.

- **Frontend**: React 18, TailwindCSS, Shadcn/UI, Recharts, SSE (EventSource)
- **Backend**: FastAPI, Motor (async MongoDB), ChromaDB (vector embeddings), SSE-Starlette
- **Database**: MongoDB (`risedual_db`) — 23 collections, 3,200+ documents
- **Vector Store**: ChromaDB (`market_episodes`) — local `all-MiniLM-L6-v2` embeddings
- **External APIs**: Binance US (L2 order books), Alpha Vantage (market data), yfinance (fallback), Finnhub (congressional trades), Stripe (payments), Resend (emails), OpenAI GPT-5.2 / GPT-4o-mini via Emergent LLM

**Codebase Size**: ~14,500 lines backend (Python), ~16,500 lines frontend (JSX/JS), 69 test iterations

---

## 2. ARCHITECTURE

```
Browser (React SPA)
    |
    |-- HTTPS --> Kubernetes Ingress (nginx) --> port 3000 (React)
    |-- /api/*  --> Kubernetes Ingress --> port 8001 (FastAPI)
    |-- SSE    --> /api/stream/* (EventSource long-poll)
    |
FastAPI (port 8001)
    |-- 20 Route Modules (auth, ai, market, broker, strategy, etc.)
    |-- 30+ Service Modules (AI agents, scrapers, analyzers)
    |-- Motor Async --> MongoDB (risedual_db)
    |-- ChromaDB --> Local Vector Store (market_episodes)
    |-- Binance US WebSocket --> Order Flow Stream Manager
    |-- Background Tasks (schedulers, nightly cleanup)
```

---

## 3. BACKEND — ROUTE MODULES (`/app/backend/routes/`)

| Route File | Prefix | Endpoints | Auth | Purpose |
|---|---|---|---|---|
| `auth.py` | `/api/auth` | 28 functions | Public/JWT | Login, register, forgot password, admin seed, brute-force protection |
| `ai.py` | `/api` | 25 functions | JWT | Chat (multimodal), company research, market prediction |
| `market.py` | `/api` | 24 functions | Mixed | Stock/crypto data, order flow, price quotes |
| `market_data.py` | `/api/market-data` | 22 functions | Mixed | Tickers, options flow, dark pool data |
| `broker.py` | `/api/broker` | 39 functions | JWT | Connect broker, execute trades (MOCKED), portfolio |
| `accuracy.py` | `/api/accuracy` | 31 functions | JWT | Prediction tracking, verification, accuracy stats |
| `strategy.py` | `/api/strategy` | 21 functions | JWT | Strategy builder, backtester, marketplace |
| `intelligence.py` | `/api/intelligence` | 11 functions | JWT/Pro | AI War Room, watchlist intelligence |
| `workspace.py` | `/api/workspace` | 17 functions | JWT | User workspace, alerts, preferences |
| `subscription.py` | `/api/subscription` | 11 functions | Mixed | Stripe checkout, plan management ($45/mo) |
| `admin.py` | `/api/admin` | 14 functions | Admin | User management, broker OAuth config, cache |
| `journal.py` | `/api/journal` | 17 functions | JWT | Trading journal entries |
| `push.py` | `/api/push` | 13 functions | Mixed | VAPID web push subscribe/unsubscribe/test |
| `stream.py` | `/api/stream` | 8 functions | Public | Live Insights SSE (heartbeat + events) |
| `orderflow_stream.py` | `/api/stream` | 1 function | Public | Live order flow SSE per symbol |
| `whale_radar.py` | `/api/stream` | 1 function | Public | Multi-ticker whale radar SSE |
| `sectors.py` | `/api/sectors` | 8 functions | Mixed | Sector heatmap data |
| `digest.py` | `/api/digest` | 11 functions | JWT | Daily digest emails |
| `promo.py` | `/api/promo` | 14 functions | Mixed | Promotional campaigns |
| `referral.py` | `/api/referral` | 11 functions | JWT | Referral system + leaderboard |

**Total: ~350+ API endpoints across 20 route modules**

---

## 4. BACKEND — SERVICE MODULES (`/app/backend/services/`)

### AI / Machine Learning
| Service | Lines | Purpose |
|---|---|---|
| `crew_definitions.py` | 562 | Multi-agent AI crews (War Room, Hypothesis, Prediction) with adversarial Edge/Veto injection |
| `crew_engine.py` | ~200 | Thread-safe native multi-agent execution engine |
| `ai_service.py` | ~300 | GPT-5.2 chat integration (multimodal: text + image) |
| `ai_intelligence_service.py` | ~400 | AI War Room intelligence briefs, patterns, scores |
| `hypothesis_service.py` | ~250 | Single-model hypothesis generation |
| `multi_model_hypothesis_service.py` | ~300 | Multi-model AI consensus (GPT-5.2 + GPT-4o-mini) |
| `market_prediction_service.py` | 356 | Full market prediction pipeline (scrapes + AI synthesis) |
| `post_mortem_service.py` | ~200 | AI-powered failure analysis with GPT-5.2 |
| `market_memory_service.py` | ~400 | ChromaDB vector store: store/query/prune market episodes |
| `memory_training_service.py` | ~200 | Nightly retraining: prune toxic memories, boost edge patterns |
| `prediction_tracker.py` | 307 | Prediction verification, accuracy stats, failure mode classification |

### Market Data
| Service | Lines | Purpose |
|---|---|---|
| `market_data_service.py` | ~300 | Alpha Vantage + yfinance market data with caching |
| `price_provider.py` | ~150 | Unified price quotes (AV -> yfinance -> Binance fallback chain) |
| `order_flow_service.py` | 384 | Static order flow: Binance L2 (crypto) + yfinance volume profile (stocks) |
| `orderflow_ws_service.py` | 295 | Live Binance WebSocket stream manager + whale detection |
| `sector_service.py` | ~200 | Sector heatmap data aggregation |
| `market_sentiment_service.py` | ~150 | Fear & Greed index integration |

### Scraping
| Service | Lines | Purpose |
|---|---|---|
| `financial_scraping_service.py` | ~300 | Financial news scraping (multiple RSS/web sources) |
| `crypto_scraping_service.py` | ~200 | Crypto-specific news and sentiment scraping |
| `real_estate_scraping_service.py` | ~150 | Real estate market data scraping |
| `world_events_service.py` | ~200 | Global events scraping (Reuters, AP, BBC) |
| `foreign_markets_service.py` | ~200 | International market data (Asian, European exchanges) |
| `gov_filings_service.py` | ~150 | Congressional trading data (Finnhub) |
| `company_research_service.py` | ~250 | Perplexity-style company deep research |

### Infrastructure
| Service | Lines | Purpose |
|---|---|---|
| `auth_helpers.py` | ~100 | JWT token creation/validation, bcrypt hashing |
| `cache.py` | ~100 | In-memory + MongoDB caching layer |
| `push_service.py` | 185 | VAPID web push: subscribe, broadcast, whale alerts |
| `email_service.py` | ~150 | Resend email integration (alerts, digests) |
| `digest_service.py` | ~200 | Daily digest generation and delivery |
| `payment_service.py` | ~150 | Stripe payment processing |
| `broker_service.py` | ~200 | Broker integration (OAuth config, MOCKED execution) |
| `strategy_service.py` | ~300 | Strategy creation, backtesting, marketplace |
| `backtester_service.py` | ~250 | Historical backtesting engine |
| `war_room_service.py` | ~200 | AI War Room orchestration |
| `watchlist_intelligence_service.py` | ~200 | Per-symbol watchlist AI analysis |
| `finnhub_service.py` | ~100 | Finnhub API client |

---

## 5. FRONTEND — COMPONENT MAP (`/app/frontend/src/`)

### Core Layout
| Component | Lines | Purpose |
|---|---|---|
| `App.js` | 167 | Main layout orchestrator, component ordering, auth gating |
| `Navbar.jsx` | ~250 | Top navigation, user menu, mobile hamburger |
| `Footer.jsx` | ~80 | Site footer |
| `MobileBottomNav.jsx` | ~100 | Mobile bottom tab bar |
| `ScrollToTop.jsx` | ~30 | Scroll-to-top button |
| `ErrorBoundary.jsx` | ~40 | React error boundary |

### Authentication & Payments
| Component | Lines | Purpose |
|---|---|---|
| `AuthModal.jsx` | ~150 | Login/register modal |
| `auth/AuthForm.jsx` | ~200 | Email/password form with validation |
| `auth/ForgotPasswordForm.jsx` | ~100 | Password reset flow |
| `ResetPasswordModal.jsx` | ~80 | Reset password confirmation |
| `SubscriptionPricing.jsx` | ~200 | $45/month plan with Stripe checkout |
| `PaymentStatus.jsx` | ~60 | Payment success/cancel page |
| `ProBlurWall.jsx` | ~40 | Blur overlay for Pro-only features |

### Market Data
| Component | Lines | Purpose |
|---|---|---|
| `StockTicker.jsx` | ~150 | Live stock ticker marquee |
| `CryptoTicker.jsx` | ~120 | Live crypto price ticker |
| `CryptoSection.jsx` | ~100 | Crypto market overview |
| `DataTable.jsx` | ~200 | Real-time market data table |
| `OptionsRadar.jsx` | ~200 | Options flow visualization |
| `OptionsFlowScreener.jsx` | ~150 | Options flow filtering |
| `DarkPoolData.jsx` | ~150 | Dark pool transaction table |
| `SectorHeatmap.jsx` | ~200 | S&P 500 sector heatmap (treemap) |

### Order Flow & Whale Detection
| Component | Lines | Purpose |
|---|---|---|
| `OrderFlowPanel.jsx` | 234 | Snapshot order flow with Binance L2 / yfinance, 0-100 intensity, Live/Snapshot tabs |
| `OrderFlowHeatmap.jsx` | ~300 | Real-time SSE heatmap: 24 price bins, bid/ask colors, wall detection |
| `WhaleRadar.jsx` | ~180 | Multi-ticker whale radar: 10 crypto pairs, SSE stream, detection feed |

### AI Features
| Component | Lines | Purpose |
|---|---|---|
| `TradeGPTChat.jsx` | ~400 | Multimodal AI chat (text + image upload + chart patterns) |
| `ChartPatternLibrary.jsx` | ~300 | Interactive SVG chart pattern library |
| `CompanyResearch.jsx` | ~250 | Perplexity-style company research with AI synthesis |
| `MarketPrediction.jsx` | ~300 | AI market prediction cards with confidence scores |
| `prediction/PredictionCards.jsx` | ~150 | Individual prediction card rendering |
| `AIWarRoom.jsx` | ~300 | AI War Room with multi-agent consensus |
| `warroom/WarRoomCards.jsx` | ~150 | War Room result cards |
| `AIHypothesis.jsx` | ~250 | AI hypothesis generator with model selector |
| `hypothesis/*.jsx` | ~300 | Hypothesis sub-components (results, locked, model selector) |
| `AIIntelligence.jsx` | ~200 | AI intelligence dashboard (brief, patterns, scores) |
| `intelligence/*.jsx` | ~250 | Intelligence sub-views |
| `WatchlistIntelligence.jsx` | ~200 | Per-symbol AI watchlist analysis |
| `MemoryDashboard.jsx` | ~250 | ChromaDB memory visualizer (Pro) |

### Trading Tools
| Component | Lines | Purpose |
|---|---|---|
| `StrategyBuilder.jsx` | ~300 | AI-assisted strategy builder |
| `StrategyMarketplace.jsx` | ~250 | Community strategy marketplace |
| `strategy/StrategyPreview.jsx` | ~100 | Strategy preview card |
| `BacktestResults.jsx` | ~200 | Backtesting results visualization |
| `BrokerConnect.jsx` | ~200 | Broker connection UI (Alpaca, IBKR, etc.) |
| `QuickTrade.jsx` | ~150 | Quick trade execution panel |
| `PnLTracker.jsx` | ~200 | Real-time P&L tracking |
| `PortfolioAnalyzer.jsx` | ~200 | Portfolio analysis tools |
| `TradingJournal.jsx` | ~250 | Trading journal with entries |
| `Watchlist.jsx` | ~200 | User watchlist management |

### Macro Data
| Component | Lines | Purpose |
|---|---|---|
| `MacroDashboard.jsx` | ~200 | Macro data tabs container |
| `macro/WorldEventsTab.jsx` | ~150 | Global events feed |
| `macro/ForeignMarketsTab.jsx` | ~150 | International markets data |
| `macro/CongressTab.jsx` | ~150 | Congressional trading activity |

### Alerts & Notifications
| Component | Lines | Purpose |
|---|---|---|
| `AlertsPanel.jsx` | ~200 | Notification center |
| `alerts/NotificationItem.jsx` | ~80 | Individual notification card |
| `LiveInsightsFeed.jsx` | ~150 | SSE-powered live insight stream |
| `MarketSignals.jsx` | ~150 | Market signal alerts |

### Admin
| Component | Lines | Purpose |
|---|---|---|
| `AdminPanel.jsx` | ~200 | Admin dashboard |
| `admin/AdminTools.jsx` | ~150 | Admin utility tools |
| `admin/BrokerOAuthConfig.jsx` | ~200 | Broker OAuth key management (Fernet encrypted) |
| `admin/CacheMonitor.jsx` | ~100 | Cache monitoring |
| `admin/PromoManager.jsx` | ~150 | Promotional campaign management |

### Social & Marketing
| Component | Lines | Purpose |
|---|---|---|
| `UserWorkspace.jsx` | ~300 | User workspace/settings, push toggle |
| `FilterPanel.jsx` | ~100 | Data filtering controls |
| `ReferralLeaderboard.jsx` | ~150 | Referral program leaderboard |
| `SocialShareButtons.jsx` | ~80 | Social media share buttons |
| `PromoBanner.jsx` | ~80 | Promotional banner |
| `AccuracyBadge.jsx` | ~40 | Prediction accuracy badge |

---

## 6. DATABASE SCHEMA (MongoDB: `risedual_db`)

| Collection | Documents | Key Fields |
|---|---|---|
| `users` | 45 | email, password_hash, role (user/admin/owner), plan (free/pro), watchlist[], created_at |
| `chat_sessions` | 34 | session_id, user_id, messages[], created_at |
| `predictions` | 2 | ticker, direction, confidence, entry_price, status, created_at |
| `market_memory_log` | 2,978 | ticker, episode_type, outcome, conviction, is_accurate, failure_code, created_at |
| `notifications` | 73 | user_id, type, title, message, read, metadata, created_at |
| `payment_transactions` | 25 | session_id, user_id, amount, currency, plan, status |
| `strategies` | 4 | user_id, name, type, rules, indicators |
| `marketplace_strategies` | 5 | author_id, strategy, rating, downloads |
| `trades` | 9 | user_id, ticker, side, quantity, price, broker, status |
| `watchlists` | 6 | user_id, symbols[], created_at |
| `watchlist_intelligence` | 6 | user_id, symbol, analysis, updated_at |
| `hypothesis_history` | 2 | user_id, ticker, hypothesis, models, created_at |
| `war_room_cache` | 5 | ticker, analysis, agents, updated_at |
| `sentiment_history` | 4 | date, fear_greed_index, sector_data |
| `price_cache` | 61 | symbol, price, change_pct, cached_at |
| `push_subscriptions` | 2 | user_id, endpoint, keys, created_at |
| `login_attempts` | 13 | ip, email, attempts, locked_until |
| `referral_codes` | 6 | user_id, code, uses |
| `referrals` | 12 | referrer_id, referee_id, status |
| `referral_rewards` | 4 | user_id, reward_type, amount |
| `promos` | 1 | title, discount, active |
| `memory_cleanup_log` | 9 | run_date, pruned_count, stats |
| `password_reset_tokens` | 0 | user_id, token, expires_at |

### ChromaDB Vector Store (`market_episodes`)
- **Embedding Model**: `all-MiniLM-L6-v2` (local, no API calls)
- **Metadata**: `{outcome, failure_code, is_accurate, conviction, ticker, episode_type}`
- **Used for**: Adversarial AI learning — "Edge" patterns (wins) vs "Veto" patterns (losses)

---

## 7. ENVIRONMENT VARIABLES

### Backend (`/app/backend/.env`)
| Key | Purpose |
|---|---|
| `MONGO_URL` | MongoDB connection string |
| `DB_NAME` | Database name (`risedual_db`) |
| `EMERGENT_LLM_KEY` | Universal key for GPT-5.2, GPT-4o-mini |
| `ALPHA_VANTAGE_API_KEY` | Stock market data (free tier: 5 calls/min) |
| `STRIPE_API_KEY` | Stripe secret key |
| `STRIPE_PUBLISHABLE_KEY` | Stripe publishable key |
| `STRIPE_PRICE_ID` | Stripe price ID for $45/month plan |
| `RESEND_API_KEY` | Email delivery |
| `VAPID_PRIVATE_KEY` | Web push signing |
| `VAPID_PUBLIC_KEY` | Web push subscription |

### Frontend (`/app/frontend/.env`)
| Key | Purpose |
|---|---|
| `REACT_APP_BACKEND_URL` | API base URL |
| `REACT_APP_VAPID_PUBLIC_KEY` | Push notification subscription |

---

## 8. REAL-TIME DATA FLOWS

### SSE Streams (Server-Sent Events)
1. **`/api/stream/insights`** — Live Insights Feed: post-mortem completions, toxic spikes, prediction verifications
2. **`/api/stream/orderflow/{symbol}`** — Per-symbol order flow: Binance L2 depth snapshots every 1s
3. **`/api/stream/whale-radar`** — Multi-ticker: 10 crypto pairs simultaneously, whale events + tick summaries

### Binance WebSocket Architecture
```
Binance US WSS (wss://stream.binance.us:9443)
    |
    |-- BTC: btcusdt@depth20@1000ms
    |-- ETH: ethusdt@depth20@1000ms
    |-- SOL: solusdt@depth20@1000ms
    |-- ... (10 pairs total)
    |
OrderFlowStream Singleton (shared connections)
    |
    |-- Process: Parse L2, detect walls, compute 0-100 intensity
    |-- Whale Alert: If intensity >= 85 → VAPID push notification (5-min cooldown)
    |-- SSE Relay: Push to connected EventSource clients
```

### AI Multi-Agent Pipeline
```
User Request (e.g., "Analyze AAPL")
    |
    1. Data Gathering (parallel):
    |   ├── yfinance/Alpha Vantage → Price, technicals
    |   ├── Financial scraping → News sentiment
    |   ├── Order flow → Institutional walls + bias
    |   └── ChromaDB → Historical Edge/Veto patterns
    |
    2. Agent Execution (sequential):
    |   ├── Strategist Agent (finds entry using Edge patterns)
    |   ├── Risk Auditor Agent (veto using Veto patterns + live walls)
    |   └── Synthesis Agent (final BUY/SELL/AVOID + conviction 0-100)
    |
    3. Post-Processing:
    |   ├── Store prediction → MongoDB
    |   ├── Store episode → ChromaDB vector store
    |   └── Push SSE event → Live Insights Feed
    |
    4. Nightly:
        ├── Verify predictions → Mark accurate/inaccurate
        ├── Classify failures → TECH_FAKEOUT, MACRO_SHOCK, etc.
        ├── AI Post-Mortem → GPT-5.2 analyzes why it failed
        └── Prune toxic memories → Delete high-conviction failures
```

---

## 9. AUTHENTICATION & AUTHORIZATION

- **Method**: JWT tokens (bcrypt password hashing)
- **Roles**: `user`, `admin`, `owner`
- **Plan Tiers**: `free` (limited features), `pro` ($45/month via Stripe)
- **Brute Force**: IP + email based lockout after 5 failed attempts
- **Pro-Gated Features**: AI War Room, Hypothesis, Memory Dashboard, Watchlist Intelligence

### Accounts
| Role | Email | Password |
|---|---|---|
| Owner | `managingdirector@redslateholdings.com` | `RedSlate2026!` |
| Admin | `admin@risedual.ai` | `RiseDual2026!` |

---

## 10. EXTERNAL INTEGRATIONS

| Service | Status | Purpose | Key Required |
|---|---|---|---|
| Binance US API | LIVE | L2 order books, depth streams | No (public) |
| Alpha Vantage | LIVE | Stock quotes, technicals | Yes (free tier) |
| yfinance | LIVE | Fallback market data | No |
| Finnhub | LIVE | Congressional trades | No (free tier) |
| OpenAI GPT-5.2 | LIVE | AI agents, chat, post-mortems | Emergent LLM Key |
| OpenAI GPT-4o-mini | LIVE | Fast AI tasks | Emergent LLM Key |
| Stripe | LIVE | $45/month subscription billing | Yes |
| Resend | LIVE | Email alerts, daily digests | Yes |
| VAPID Web Push | LIVE | Browser push notifications | Auto-generated |
| ChromaDB | LIVE | Vector memory (local) | No |
| Alpaca/IBKR | MOCKED | Broker execution | OAuth config ready |

---

## 11. TESTING

- **69 test iterations** logged in `/app/test_reports/`
- **100+ backend test files** in `/app/backend/tests/`
- **Testing methods**: pytest, curl, Playwright browser automation, SSE stream validation
- **Latest pass rates**: Iterations 66-69 all 100% pass

---

## 12. DEPLOYMENT

- **Current**: Emergent Preview (`risedual-trading.preview.emergentagent.com`)
- **Target**: `risedual.ai` (GoDaddy domain, hosting TBD)
- **Config files**: `Dockerfile`, `docker-compose.yml`, `nginx.conf`, `DEPLOYMENT_GUIDE.md`

---

## 13. KNOWN LIMITATIONS

| Item | Status | Notes |
|---|---|---|
| Broker execution | MOCKED | OAuth config built, execution logic not connected |
| Alpha Vantage rate limit | 5/min free | Causes intermittent N/A values; user can upgrade |
| Binance global | GEO-BLOCKED | Uses Binance US as primary; global as fallback |
| MATIC on Binance US | LOW LIQUIDITY | Returns empty order book |
| Reuters/AP RSS | DNS BLOCKED | From this K8s pod; world events use cached data |

---

## 14. FILE COUNTS

| Category | Files | Lines |
|---|---|---|
| Backend Python (services + routes + server) | 62 | ~14,500 |
| Frontend JSX/JS (components + utils) | 75+ | ~16,500 |
| Backend Tests | 45 | ~8,000 |
| Test Reports | 69 | JSON logs |
| **Total** | **250+** | **~39,000** |
