# RISEDUAL AI — Changelog

## April 12, 2026 — Code Quality Sweep (P0/P1)
- **Security: MD5 → SHA-256** in `routes/accuracy.py`, `services/market_memory_service.py`, `services/post_mortem_service.py`
- **Security: Hardcoded test credentials centralized** — 25 test files updated to import from `conftest_creds.py`
- **React: Index-as-key anti-pattern fixed** in 9 components (WarRoomCards, OrderFlowHeatmap, MemoryDashboard, DarkPoolData, PaperTrading, PredictionCards, OrderFlowPanel, WhaleRadar, HypothesisResults)
- **Backend refactoring:** `broker.py` oauth_callback split into 4 helpers; `accuracy.py` classify_failure extracted ChromaDB helper
- **Confirmed: eval()/exec() already removed** by previous agent — safe AST evaluator in backtester_service.py
- **Verified:** Iteration 86 — 100% pass (15/15 backend, all frontend)

## April 12, 2026 — Security Audit Dashboard + Component Splitting
- **New Feature: Security Audit Dashboard** in Admin Panel (new "Security" tab)
  - Backend: 5 endpoints under `/api/admin/security/` (overview, failed-logins, oauth-rotations, broker-connections, unlock)
  - Frontend: Stat cards + expandable sections showing real security data
- **Admin Panel access fixed** for `admin` role (was owner-only in Navbar + App.js)
- **Navbar refactored** — mobile menu extracted to `MobileMenu.jsx` (322 → 239 lines)
- **React Hooks: Zero warnings** — ESLint scan of 136 files with exhaustive-deps rule returned 0 issues
- **Verified:** Iteration 87 — 100% pass (20/20 backend, all frontend)


## April 11, 2026 — Media, Legal, Broker, OAuth, Voice
- Media Upload / Object Storage System (storage_service.py, MediaManager.jsx)
- Broker API Key Vault + Role-Based Execution
- 3-Legged OAuth + PKCE + Refresh Token Rotation
- Trade Execution Push Notifications
- Legal Pages (Terms, Privacy, Risk, Disclaimer)
- Voice Chat (TTS + STT via OpenAI)
- SEO Optimization (meta tags, structured data, sitemap)
- About Us Page
- Landing Page commercial video embed
- Real Polygon.io Dark Pool data integration
- Custom WhaleRadar + AdversarialHub UI

## April 10, 2026 — Core AI & Market Systems
- AI Sentiment Heatmap (multi-agent sector analysis)
- Market Vector Memory System (ChromaDB + MiniLM embeddings)
- Memory Training (2,973 historical episodes)
- Nightly Cleanup + Toxic Spikes Alerts
- Dual-Signal Adversarial AI (Edge vs Veto)
- Failure Mode Classification
- AI Post-Mortem Analysis
- Real-Time SSE Insight Stream
- Memory Dashboard UI
- Order Flow / Institutional Wall Detection
- Real-Time Order Flow Heatmap (Binance L2)
- VAPID Web Push Whale Alerts
- Multi-Ticker Whale Radar
- Portfolio Agent with AI Tool Calling
- Paper Trading System
- Historical Sentiment Tracking
- Enriched Regime Format (Fear & Greed + VIX)
