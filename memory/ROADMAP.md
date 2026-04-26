# RISEDUAL AI — Roadmap

Prioritized backlog. P0 = blocking · P1 = next sprint · P2 = future · P3 = vision.

---

## 🟢 Ready to ship (queued in DEPLOYMENT_NOTES.md)

See `/app/memory/DEPLOYMENT_NOTES.md` → `🟡 Queued for next deploy` for the
current live deploy queue.

---

## P0 — Imminent

- **Wire the crypto bots to the new isolated paper line.** ✅ DONE
  (Apr 25). Service + route + collection + scheduler + Strategist/
  Auditor signal layer + 12h max-hold closer all live. See
  PRD §4 "Crypto Bot v2".

- **Shadow-mode Web Research (Tavily + LLM stance).** ✅ DONE
  (Apr 26). New services: `web_research_service.py` (Tavily HTTP +
  EMERGENT_LLM_KEY stance classifier with strict JSON parser) and
  `research_router.py` (cost-aware gate: high-conviction OR narrative
  regime; 10-min Mongo cache; `CRYPTO_SHADOW_RESEARCH_DISABLED=1`
  ops kill switch). Hooked into `crypto_paper_trader.run_crypto_symbol`
  AFTER the strategist/auditor produced a final LONG/SHORT signal —
  verdict is persisted on both `crypto_signal_audit_log.web_research_shadow_verdict`
  AND `crypto_paper_trades.web_research_shadow_verdict` for later
  expectancy correlation. **Verdict is logged only — never alters
  direction or confidence in the live path.** 41 new pytest cases
  cover gate / cache / classifier / failure-mode / shadow-isolation.

- **Alpaca live-key plumbing.** User has Alpaca approval; waiting on key
  from email. All infrastructure is ready:
  - `.env` slots present (empty): `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
  - `ALPACA_BASE_URL` pinned to `paper-api.alpaca.markets`
  - `services/ml_alpaca_broker.py` adapter complete
  - Safety gates: `_MIN_LIVE_CONFIDENCE=0.50`, `_LIVE_OPT_IN` requires
    explicit `RISEDUAL_LIVE_EXECUTION=1` env var (keep unset during the
    30-day paper window)
  - UI: `BrokerConnect.jsx` + `AlpacaOAuthDemo.jsx`
  - Bot executor (`_execute_bot_trade`) already wired
  - Action when key arrives: paste into `.env`, restart backend, verify
    via Broker Connect health check.

---

## P1 — Next sprint

- **Wire Alpaca crypto execution.** The bots already trade crypto
  symbols on paper (`trading_bot_service.py:512` routes BTC/ETH/SOL/
  DOGE/ADA/XRP/AVAX/DOT/SHIB/LINK/BNB through `get_crypto_quote()`),
  but live orders only flow to Alpaca equities. Alpaca DOES support
  crypto via the same account — just needs the order-submission
  path wired.

  **Scope** (~2 hours, low-risk, post-deploy):
  1. Extend `services/alpaca_service.submit_order()` to detect crypto
     symbols and append `/USD` (e.g. `BTC` → `BTC/USD`) before
     POSTing to `/v2/orders`. Alpaca's symbol convention is the only
     real difference vs equities.
  2. Set `time_in_force = "gtc"` for crypto (Alpaca doesn't accept
     `"day"` on 24/7 markets).
  3. Add `asset_class: "crypto"` tag on the resulting `paper_trades`
     / `trades` row so the auto-closer + ML labeler can distinguish.
  4. UI: in `BrokerConnect`, add a tooltip noting "Alpaca account
     trades both equities and crypto from the same key."
  5. Test: paper-mode order for `BTC` and `ETH`, confirm fills in
     Alpaca dashboard, confirm `trades` row gets `asset_class:
     "crypto"`.

  **What this unlocks**: 24/7 bot trading (crypto markets never
  close), ~25 pairs, one tax statement covering both asset classes.
  Kraken adapter is already coded in `broker_service.py:986` as a
  future P2 if a user asks for lower fees / more pairs.

  Context: 2026-04-25 chat thread "can the bots trade crypto?" —
  user confirmed they want this as the post-deploy follow-up.

- **30-day paper-trading accumulation → ML Tier 3 unlock.** Currently at
  4/4 wins logged (2026-04-20: BTDR, KEY, GROY×2 — see
  `AI_PREDICTION_WINS.md`). Need 100/500/1000 labeled predictions for
  Tier 1/2/3 gates. Automated labeler is running hourly.

---

## ⏸ Parked — waiting on user action

- **Drop `_CANONICAL_OWNER_PASSWORD` override** in
  `backend/routes/auth.py`. *(2026-02-08: user chose to rotate the
  password via UI first and verify login works before the override
  is removed. Do not drop until user confirms the new password has
  been tested post-rotation.)*

- **Flip `ML_ADAPTATION_SHADOW_MODE` → live.** *(2026-02-08: parked
  for ~2 weeks of shadow observation. Calibration endpoint
  currently reports 0 observations. Revisit when
  `/api/admin/adaptations/calibration` returns a stable p25
  recommendation with ≥20 observations.)*

  Flip procedure when ready:
  1. `GET /api/admin/adaptations/calibration?window_days=30` → read
     `recommendation.suggested_effect_size`.
  2. Set `ML_AUTO_REVERT_EFFECT_SIZE=<p25>` in `backend/.env`.
  3. Set `ML_ADAPTATION_AUTO_REVERT_ENABLED=true` in `backend/.env`.
  4. `sudo supervisorctl restart backend`.
  5. Watch the admin `ModelAdaptationsPanel` for the first live
     soften row (no longer tagged `shadow_`).

---

## P2 — Future

- **`/api/crypto/sltp-expectancy` analytics endpoint** — Read-only
  aggregation over `crypto_trade_memory` to answer "are our crypto
  exits optimal or just reasonable?". Do NOT change the current
  -2% / +4% defaults until the data is in.

  **Trigger:** at least 100+ closed crypto trades accumulated.

  **Buckets to compare:**
  - -1.5% / +3% (1:2 R:R, tight)
  - -2.0% / +4% (1:2 R:R, current default)
  - -2.5% / +5% (1:2 R:R, loose)

  **Ranking metric:**
  ```
  expectancy = win_rate × avg_win_R − loss_rate × avg_loss_R
  ```

  **Also return per bucket:**
  - total trades
  - win_rate
  - avg R-multiple
  - median R-multiple
  - max drawdown proxy (worst single-trade R)
  - close_reason distribution (stop_loss / take_profit / hold_window_expired)

  **Implementation hint:** synthetic SL/TP simulation against the
  actual entry/exit/peak/trough is the right approach — replay each
  closed trade against alternative bracket pairs and recompute
  outcome. Schema is already there (`crypto_trade_memory.r_multiple`
  + `entry_price` + `exit_price` + `close_reason`).

  Do NOT auto-tune the defaults from this endpoint's output —
  surface the recommendation in the admin dashboard as a "data
  suggests…" tile and require manual approval before changing
  `build_stop_take_profit()`.

- **Paper trader duplicate-insert race** — On 2026-04-16 at
  19:18:20 and 19:18:32, two `paper_trades` rows for AAPL/`down`
  were inserted with **identical** entry_price (266.43), shares
  (38.17), and position_size_usd (10170.35) just 12 seconds apart.
  The 19:15:33 row (different shares: 46.85) is legit; the two
  3-min-later twins look like a retry bug or duplicate signal
  fanout in `services/ml_paper_trader.py`.

  **Proposed fix** (locked in 2026-04-25, chat thread "duplicated
  AAPL trades"): enforce an idempotency key at insert time on
  `(symbol, direction, entry_price, strategy_id, time_bucket)`
  where `time_bucket` is `floor(opened_at / 60s)` — i.e. one trade
  per symbol+direction+price+strategy per minute, max. Two paths:
  1. Mongo unique index on the composite key (DB-enforced — best).
  2. Pre-insert `find_one` lookup with the same shape (app-enforced,
     racy under concurrency but simpler to deploy).

  Path 1 is the right answer; path 2 is the kill-switch fallback.

  Forensic snapshot preserved: the 5 orphan trades from the
  2026-04-25 audit are tagged with `force_closed: true` +
  `force_close_reason: 'manual_recovery_2026-04-25_orphan_no_prediction_id'`
  — query for those flags to recover the duplicate twins for
  reproduction.
- **Backtest/Live data labeling (Option B)** — add `data_source:
  "backtest" | "live"` derived at API response time based on row
  timestamp vs `PUBLIC_DATA_FLOOR_DATE` (default 2026-04-23,
  Patent #1 filing). UI shows a small "Backtest" badge on
  pre-filing rows so pre-formation dates read as "5-year backtest
  depth" instead of suspect failures. Read-side only — **no DB
  writes, no migration, no Tier 3 collection changes**. Feature-
  flagged for instant rollback. Est. ~1.5 hr. Context: chat
  thread 2026-04-24 "spike failures dated 1-2 years before IP".
- **QuantConnect ↔ QuiverQuant bridge** (user's QC algo pending).
  Replaces flaky Quiver REST with QC Cloud pipeline for Lobbying +
  Insider Trading datasets.
- **Data consolidation:** prototype **Polygon.io** adapter to replace
  Finnhub (cleaner API, better options coverage). A/B behind a feature
  flag for a week before switching. Future consolidation candidate:
  **Financial Modeling Prep** for fundamentals + insider data — could
  retire the QuiverQuant direct dep if quality acceptable. See chat
  thread "Which app? This one?" for full analysis.
- **Pro Max tier UI wiring.** Backend checkout path is live
  (`tier=pro_max` → $99). Frontend Pricing card still POSTs
  `plan=monthly` — need to add the `tier` field to the Pro Max
  `Subscribe` button handler.

---

## P3 — Vision / strategic projects

Sourced from user-supplied PDFs (2026-04-20 drop):

### 🧭 RISEDUAL Navigator — *AI-native trading browser*
**Source:** `risedual_navigator_design.pdf`

A Netscape-Communicator-inspired desktop app that makes calibrated
signals a first-class browsing primitive.

- **Core metaphor:** *"A window onto calibrated trading signals."*
- Tabs → tickers. Bookmarks → watchlists. Persistent AI sidebar with
  tool-calling. Dedicated Signal + Pattern panel showing calibrated
  probabilities, regime, and detected patterns.
- Visible autonomy-tier guardrails (Alerts → Paper → Live).
- Cost transparency via a session ledger.
- **Architecture:** 3-tier — Presentation (UI) · Application (FastAPI) ·
  Core (SignalModel, RegimeModel, `patterns.py`, CalibrationGate).
- **Tech hint:** Tauri wrapper so we can reuse the React web app code.
- **Success metrics:**
  - LLM tool-call latency: **<400ms** cached signal fetch, **<1.5s**
    chat turn.
  - Milestones gated on calibration thresholds:
    - Tier 1: 100 labeled predictions
    - Tier 2: 500 labeled predictions
    - Tier 3 (live execution): 1000 labeled predictions

> **Dropped (2026-02-20):** "Adversitao Everywhere" cross-surface
> brand rollout (React Native mobile + Tauri Navigator desktop as
> a unified `@adversitao/ui` design system). No longer on the
> backlog at user request. RISEDUAL Navigator above stands on its
> own as a P3 desktop vision, independent of any mobile track.

---

## Backlog / parking lot

- API Usage Dashboard (tracks hit rate per external API)
- Caching decorator layer for Finnhub / OpenFIGI / QuiverQuant
- Cloudflare R2 / Backblaze B2 cold storage for options chains, tick
  data, news archives (decide post API-usage audit)
- Annual-billing toggle on Pricing section (proven +15-25% conversion
  uplift for SaaS)
