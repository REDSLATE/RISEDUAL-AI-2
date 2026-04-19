# RISEDUAL AI — Deployment Notes

> **What this file is:** a running journal of every code change, split into
> "Shipped to production" vs. "Queued for next deploy". Whenever you ask
> "what changed since my last deploy?", this file is the answer.
>
> **How it stays current:**
> 1. The agent (me) appends entries to the "Queued for next deploy" section
>    at the end of every change it makes.
> 2. When you deploy, run `/app/scripts/mark-deployed.sh "short label"` —
>    that moves every queued entry into a new "Shipped" block, timestamped
>    with the commit hash that was live at the moment you ran it.
> 3. Nothing here is inferred. Every entry names the files touched, the
>    behavioural impact, and which session introduced it.
>
> **Do NOT edit past "Shipped" blocks by hand** — they're historical record.
> Edits to the "Queued" section are fine; you own that bucket.

---

## 🟡 Queued for next deploy

> Everything below this line has been merged into the main branch on the
> sandbox/preview but has **not** been marked as shipped. Review before
> hitting Deploy.

*Nothing queued. Agent will append here as changes land.*

### 2026-02-19 — Landing-page pricing cards alignment fix
*Session: continued*

**Problem:** User flagged Pro card sitting visibly lower than Free/Starter/Pro Max
on the landing-page pricing grid (mobile + desktop). Root cause: Pro used
`border-2` (2px) while siblings used `border` (1px). That 1px delta made the
Pro card 2px taller + wider and pushed its content down relative to neighbors.

**Fix:** `PlanCard` in `frontend/src/components/LandingPage.jsx`:
- All cards now use `border-2` (equal 2px) with `border-transparent` + a
  `ring-1` for non-highlighted cards — keeps the subtle outline without
  the pixel shift.
- Added `flex flex-col h-full` so every card stretches to the tallest
  sibling (defensive — keeps rows aligned even if feature lists differ).
- Feature list marked `flex-grow` so CTA buttons line up at the bottom.

**Verified:** live preview screenshot (desktop + mobile) — all 4 cards
now have identical top + bottom edges, CTAs flush on the bottom row.


---

## 🟢 Shipped to production

> Blocks below were live at the time of the `mark-deployed` command. The
> commit hash is the state that was deployed — use `git diff <hash> HEAD`
> to see what's changed since.

### 2026-04-19 16:43 UTC — Shipped as `feb19-pricing-ml-retrain-resilience` (`1f76967`)

Commit: `1f769676be0e56b2dd6d12c037d9617a79269a42`

### 2026-02-19 — Dynamic NEUTRAL tolerance + live bot execution wiring
*Session: post-handoff recovery session*

**Behavioural changes (users may notice):**
- Accuracy stats on the dashboard now use **per-symbol ATR-based tolerance**
  instead of a flat 5%/2% band. Volatile tickers (NVDA, TSLA) get wider
  tolerance; low-vol ETFs (SPY) get tighter. Historical NEUTRAL calls were
  retro-rescored once under the new rule.
- Grid, Signal, and Webhook **trading bots in LIVE mode now actually execute
  through your connected broker** (Kraken for crypto, Alpaca for stocks).
  Previously only Paper mode worked; live had a TODO stub that silently did
  nothing. Owner-gated — no public users can trigger this.
- Price quotes cached with a **sliding 5-min TTL, max 3 touches** (~15 min
  max lifetime). Repeat quote reads for the same symbol never re-hit
  upstream until cache expires.
- Predictions deduped on a **sliding 15-min window, max 2 touches** (~30 min
  max lifetime). Rapid-fire repeat signals collapse onto the same
  `prediction_id` with `dedup_count` tracking. Cleaned 49 duplicate SPY
  NEUTRAL rows from the DB.
- `/api/accuracy/stats` and `/api/accuracy/history` now include a
  `pricing_freshness.disclaimer` explaining quote age — surfaced on the
  AccuracyBadge tooltip and as a small footnote in MemoryDashboard.
- **New owner-only** endpoints: `GET /api/admin/price-cache-stats` and
  `POST /api/admin/price-cache-invalidate/{symbol}` for cache ops
  visibility.
- **New public compliance page** at `/compliance/{broker}-oauth` (generic
  `/compliance/oauth` alias). Renders a full 3-legged OAuth 2.0 capability
  matrix pulled live from `/api/broker/oauth/capabilities`. Pre-built for
  Schwab, IBKR, Alpaca — adding more brokers to the backend config auto-
  populates new pages. Link added to landing page footer and logged-in
  app footer.

**Files touched (14):**
- `backend/services/sliding_cache.py` ★ NEW
- `backend/services/price_provider.py` — wired sliding cache into all 5 entrypoints
- `backend/services/prediction_tracker.py` — dynamic tolerance, dedup, disclaimer, `MAX_DEDUP_HITS=1`
- `backend/services/trading_bot_service.py` — live bot execution → broker
- `backend/routes/accuracy.py` — new `/rescore-neutral`, disclaimer wiring
- `backend/routes/admin.py` — owner-only cache stats endpoints + `_require_owner`
- `frontend/src/components/ComplianceOAuth.jsx` ★ NEW (parameterized compliance page)
- `frontend/src/App.js` — `/compliance/(broker-)?oauth` route
- `frontend/src/components/Footer.jsx` — "Compliance & Security" link
- `frontend/src/components/LandingPage.jsx` — footer "Compliance" link (only change)
- `frontend/src/components/AccuracyBadge.jsx` — disclaimer in tooltip
- `frontend/src/components/MemoryDashboard.jsx` — 9px slate-600 disclaimer footnote
- `memory/PRD.md` — changelog entries
- `memory/DEPLOYMENT_NOTES.md` ★ NEW (this file)

**Env vars added/changed:** none.

### 2026-02-19 — OpenAI direct API key rotation
*Session: continued*

**Env vars changed:**
- `OPENAI_API_KEY` rotated in `backend/.env`. Backend restarted. No code changes —
  existing OpenAI client code paths (still routed through Emergent LLM Key for
  most flows) pick up the new key automatically where direct OpenAI is used.
- Reminder: user should rotate this key at https://platform.openai.com/api-keys
  before production since it was transmitted in a chat session.

**Files touched:** `backend/.env` only.

### 2026-02-19 — Subscription price sync ($45 → $55)
*Session: continued*

**Problem:** User flagged the daily digest email still offered the Pro
plan at `$45/mo` while the frontend had been showing `$55/mo` for weeks.
Stale price drift across touchpoints.

**Files touched:**
- `backend/services/digest_service.py` — CTA in upgrade block (line 432)
- `backend/services/payment_service.py` — `SUBSCRIPTION_PRICE_MONTHLY`
  and `SUBSCRIPTION_PRICE_ANNUAL` constants ($45→$55 / $486→$594)
- `backend/tests/test_daily_digest.py` — assertion updated

**Verified clean:** full grep sweep of `/app/backend` and `/app/frontend/src`
returns no remaining `$45`/`45/mo`/`45.00 monthly` references. (The
`$45` in `test_iteration121_credit_system.py` is the "Power" credit
top-up SKU — legitimately different product, stays put.)

**Manual verification:** sent a fresh digest via `POST /api/digest/send-now`
to confirm new email output carries `$55/mo`.



**Behavioural changes:**
- **Nightly warm-up at 03:30 UTC** pre-resolves the top-500 federal
  recipients to tickers. Runs before the 06:00 daily digest so first
  gov-contracts dashboard load each morning is instant.
- **New owner-only endpoints:**
  - `POST /api/admin/usaspending-warmup?limit=N` — trigger on demand
  - `GET /api/admin/usaspending-health` — cache + config snapshot
- Warm-up is idempotent (rerun safely; already-cached entries skip OpenFIGI).
- **Telemetry insight:** top-100 federal recipients resolve 53/53
  publicly-traded names via hand map alone. 0 OpenFIGI calls needed at
  current scope. OpenFIGI stays as long-tail safety net.

**Files touched:**
- `backend/services/usaspending_service.py` — added `warmup_top_recipients()`
- `backend/routes/admin.py` — two new endpoints
- `backend/server.py` — added `usaspending_warmup` scheduled job


*Session: continued*

**Behavioural changes:**
- USASpending now uses a **two-stage resolver**: hand-curated 50-ticker
  map first (0ms, ~80% of the biggest awards), then OpenFIGI `/v3/search`
  fallback for long-tail names (ABBV, TER, MMM, biotech, etc.). Results
  cached in the existing `cusip_ticker_map` Mongo collection keyed by
  `query`.
- Shares the existing `OPENFIGI_API_KEY` — zero new env vars.
- Only negative-caches **confirmed** "no match" responses. Transient
  failures (429, 5xx, network) are not cached, so a temporary outage
  doesn't poison the lookup table.
- Ticker coverage for USASpending recipients effectively unlimited
  (OpenFIGI indexes ~10 million tradable securities).

**Files touched:**
- `backend/services/cusip_mapper.py` — added `resolve_name_to_ticker()`
- `backend/services/usaspending_service.py` — two-stage `_resolve_ticker()`
- `backend/route_registry.py` — wire `set_usaspending_db`

**Env vars:** unchanged (`OPENFIGI_API_KEY` reused).

### 2026-02-19 — USASpending.gov gov_contracts fallback
*Session: continued*

**Behavioural changes:**
- The only remaining gap after the Quiver resilience layer was
  `gov_contracts` (Quiver's endpoint returns 500 consistently, and we
  had no fallback). Added free public USASpending.gov as the fallback —
  `gov_contracts_count` went from 0 → 15 real awards (e.g. Boeing $32B
  DoD, Lockheed $30B DoD, Humana $51B DoD).
- Free API, no auth, no rate-limit concerns at our traffic.
- Hand-curated 50-ticker prime-contractor map (LMT, BA, NOC, RTX, etc.)
  filters USASpending recipients to only publicly-tradable names.
- 6-hour sliding cache matching the Quiver layer.
- Source string now reads `quiverquant+finnhub+scraping+usaspending`
  when all chains fire.

**Files touched:**
- `backend/services/usaspending_service.py` ★ NEW
- `backend/services/gov_filings_service.py` — USASpending fallback wired
  after Quiver returns empty

**Env vars:** unchanged (public API).


*Session: continued*

**Behavioural changes:**
- QuiverQuant's API has been returning 500s on 3 of 4 endpoints (lobbying,
  insiders, gov contracts) for weeks; only `congresstrading` works.
  Previously each request wasted a 30-second timeout — gov-filings page
  load was ~90s when 3 Quiver endpoints failed serially.
- New **per-endpoint circuit breaker**: 3 consecutive 5xx responses →
  endpoint skipped for 15 min, returns `[]` in ~0ms. Existing fallback
  chain (Finnhub → SEC scrapers in `gov_filings_service`) kicks in
  immediately instead of after a timeout cascade.
- New **6-hour sliding-TTL response cache**. Quiver "live" data updates
  once per business day, so repeat polls share the same response.
  `congresstrading` latency: 11s cold → 0ms hot.
- **Fixed path typo:** `govcontracts` → `govcontractsall` (matches the
  official `quiverquant` SDK). Our prior URL was 404ing before it even
  hit the 500s.
- **New owner-only endpoint** `GET /api/admin/quiver-status`: returns
  per-endpoint circuit state (`healthy` / `warning` / `open`), consecutive
  failure count, and cooldown seconds remaining. Cache stats included.

**Files touched:**
- `backend/services/quiver_service.py` — full rewrite with circuit +
  cache + correct paths
- `backend/routes/admin.py` — new `/quiver-status` endpoint

**Env vars:** unchanged.

**Known follow-ups:**
- Pattern 1 QuantConnect bridge (from user's discussion) deferred —
  user chose to fix the existing direct-API path first. If Quiver's
  backend outage persists > 1 month, revisit.

**DB changes:**
- Dropped 49 duplicate predictions (SPY @ $679.46, NEUTRAL, from prior session's runaway logger)
- Predictions now carry `last_seen_at`, `dedup_count`, and
  `verified_{24h,1w}.neutral_tolerance_used` fields — backward-compatible
  (absent on older rows)

**Auth / secrets:** unchanged. Owner creds remain `admin@risedual.ai` / `RiseDual2026!`.

**Known follow-ups (not blockers):**
- Schwab endpoints will go live the moment `SCHWAB_OAUTH_CLIENT_ID` /
  `SCHWAB_OAUTH_CLIENT_SECRET` are added to `backend/.env`.
