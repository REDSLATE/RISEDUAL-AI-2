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

**DB changes:**
- Dropped 49 duplicate predictions (SPY @ $679.46, NEUTRAL, from prior session's runaway logger)
- Predictions now carry `last_seen_at`, `dedup_count`, and
  `verified_{24h,1w}.neutral_tolerance_used` fields — backward-compatible
  (absent on older rows)

**Auth / secrets:** unchanged. Owner creds remain `admin@risedual.ai` / `RiseDual2026!`.

**Known follow-ups (not blockers):**
- Schwab endpoints will go live the moment `SCHWAB_OAUTH_CLIENT_ID` /
  `SCHWAB_OAUTH_CLIENT_SECRET` are added to `backend/.env`.

---

## 🟢 Shipped to production

> Blocks below were live at the time of the `mark-deployed` command. The
> commit hash is the state that was deployed — use `git diff <hash> HEAD`
> to see what's changed since.

### ↓ No deploys recorded via this file yet ↓

*(This file was created mid-project. Prior deploys exist but are not
catalogued here. The first `mark-deployed` run will create the first
"Shipped" block and bracket the un-deployed backlog cleanly.)*
