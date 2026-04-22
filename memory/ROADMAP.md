# RISEDUAL AI — Roadmap

Prioritized backlog. P0 = blocking · P1 = next sprint · P2 = future · P3 = vision.

---

## 🟢 Ready to ship (queued in DEPLOYMENT_NOTES.md)

See `/app/memory/DEPLOYMENT_NOTES.md` → `🟡 Queued for next deploy` for the
current live deploy queue.

---

## P0 — Imminent

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

- **Wire R-multiple filter + weighting into ML retrain** ← now the
  single remaining blocker for the R-weighted training path.
  Schema + writer + backfill landed Feb 2026. Next step: in
  `services/ml_retrain_service._severity_weights` (or a parallel
  `_r_weights`), when a training row has all 4 execution fields
  present (`schema_version >= 4`), route to
  `compute_sample_weight_from_trade` after dropping rows via
  `should_skip_row_by_r`. Fall back to `compute_signed_weight` on
  magnitude for legacy/unenriched rows.

- **30-day paper-trading accumulation → ML Tier 3 unlock.** Currently at
  4/4 wins logged (2026-04-20: BTDR, KEY, GROY×2 — see
  `AI_PREDICTION_WINS.md`). Need 100/500/1000 labeled predictions for
  Tier 1/2/3 gates. Automated labeler is running hourly.
- Drop `_CANONICAL_OWNER_PASSWORD` override in `backend/routes/auth.py`
  once user confirms password rotated via UI post-deploy. *(Held per
  user — do not touch without explicit confirmation.)*

---

## P2 — Future

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
