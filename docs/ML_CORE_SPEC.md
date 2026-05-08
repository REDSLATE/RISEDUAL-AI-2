# RISEDUAL AI — ML Core & Integration Specification

_Last updated: 2026-02-18 · Maintainer: internal_

This document is the single source of truth for the `risedual_core` machine-learning
engine and the third-party integrations that feed it. Keep it up to date when any
feature set, hyperparameter, gate threshold, or external provider changes.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Feature engineering](#2-feature-engineering-featurespy-222-loc)
3. [Regime model](#3-regime-model-regime_modelpy-503-loc)
4. [Signal model](#4-signal-model-signal_modelpy-525-loc)
5. [Pattern detectors](#5-pattern-detectors-patternspy-708-loc)
6. [Calibration & tier gates](#6-calibration--tier-gates-calibrationpy-648-loc)
7. [Training pipeline — `is_correct` target](#7-training-pipeline--is_correct-target)
8. [Inference pipeline (live-path)](#8-inference-pipeline-live-path)
9. [Admin observability](#9-admin-observability)
10. [API key manifest (no values)](#10-api-key-manifest-no-values)

---

## 1. Architecture overview

```
                      ┌───────────────┐
                      │  Price feed   │  Polygon / Finnhub / Alpha Vantage
                      │  OHLCV bars   │
                      └───────┬───────┘
                              ▼
                   ┌──────────────────────┐
   ┌──────────────►│  RegimeModel (HMM)   │ ── "bull" | "bear" | "sideways"
   │               └──────────────────────┘
   │                          ▼
   │               ┌──────────────────────┐
   │     features  │  Pattern detectors   │ ── 8 boolean flags + confidence
   │    snapshot   │  patterns.py (pure)  │
   │               └──────────────────────┘
   │                          ▼
   │               ┌──────────────────────────┐
   │               │  SignalModel (XGBoost +  │ ── P(is_correct) in [0, 1]
   │               │  Platt CalibratedCV)     │    + PredictionDirection
   │               └──────────────────────────┘
   │                          ▼
   │               ┌──────────────────────┐
   │               │  Calibration Gate    │ ── reject if below tier threshold
   │               │  (Tier 1/2/3)        │
   │               └──────────────────────┘
   │                          ▼
   │                ┌─────────────────────┐
   │                │  Adversarial LLM    │ ── Strategist ↔ Auditor loop
   │                │  dual-signal agent  │    (Anthropic · OpenAI · Emergent)
   │                └─────────────────────┘
   │                          ▼
   │                ┌─────────────────────┐
   └────────────────┤  UI / War Room      │  + Published Sharpe, accuracy, DD
                    └─────────────────────┘
```

**Every module is dependency-isolated**: `risedual_core.ml` never imports
`xgboost`, `sklearn`, `numpy`, or `pandas` at module level. They're lazy-imported
inside the function that needs them, so the core package imports cleanly in
environments where these packages are missing.

---

## 2. Feature engineering (`features.py` · 222 LOC)

### Ordered feature columns (17 total)

| # | Column                    | Type    | Source                    |
|---|---------------------------|---------|---------------------------|
| 1 | `rsi_14`                  | float   | computed from close bars  |
| 2 | `macd`                    | float   | computed                  |
| 3 | `macd_signal`             | float   | computed                  |
| 4 | `sma_20`                  | float   | computed                  |
| 5 | `sma_50`                  | float   | computed                  |
| 6 | `volume_ratio`            | float   | current vol / 20d avg vol |
| 7 | `sentiment_score`         | float   | `services/sentiment_service.py` (news + social) |
| 8 | `insider_activity`        | float   | 13F + SEC EDGAR feed      |
| 9 | `sector_momentum`         | float   | relative sector rotation  |
|10 | `pattern_double_bottom`   | bool    | patterns.py               |
|11 | `pattern_bullish_engulfing` | bool  | patterns.py               |
|12 | `pattern_bearish_engulfing` | bool  | patterns.py               |
|13 | `pattern_bull_flag`       | bool    | patterns.py               |
|14 | `pattern_rsi_divergence`  | bool    | patterns.py               |
|15 | `pattern_macd_crossover`  | bool    | patterns.py               |
|16 | `pattern_volume_surge`    | bool    | patterns.py               |
|17 | `pattern_head_and_shoulders` | bool | patterns.py              |

**Contextual fields (not model features, but stored on the snapshot)**:
- `regime_label` → ordinally encoded (`bull=+1, sideways=0, bear=-1`)
- `is_correct` → the binary training target (see §7)

### Imputation rules
- **Numeric columns**: training fits per-column medians, inference uses those medians.
- **Pattern columns**: all `None/NaN` values → `False` (0.0). Pre-Phase-2 snapshots have
  `None` for pattern columns; treated as "pattern not detected".

### Why column order is frozen
Saved `.joblib` artefacts encode the expected column vector by position.
Reordering `FEATURE_COLUMNS` silently breaks every model produced before the
change. Phase 2 pattern columns are appended _after_ Phase 1 numerics so old
artefacts remain loadable.

---

## 3. Regime model (`regime_model.py` · 503 LOC)

**Purpose**: label the overall market state before any signal is consulted.
Signals from bull-biased strategies get penalised during bear regimes, and
vice versa.

| Property | Value |
|---|---|
| **Backbone** | Hidden Markov Model (`hmmlearn.GaussianHMM`) with KMeans fallback |
| **Regimes** | 3 — `bull` · `bear` · `sideways` |
| **Derived features (3)** | `rolling_vol` (20d std of log returns) · `trend` (normalised dist from 60d SMA) · `momentum` (20d rolling return) |
| **Label assignment** | Sort cluster centroids by mean `momentum`. Highest → `bull`, lowest → `bear`, middle → `sideways`. |
| **Input** | `pd.Series` of daily close prices with `DatetimeIndex` |
| **Output** | `predict()` → current label string · `predict_series()` → historical label series |
| **Persistence** | `.joblib` file, versioned |

### Windows
- `_VOL_WINDOW = 20`
- `_TREND_WINDOW = 60`
- `_MOM_WINDOW = 20`

### Fallback path
If `hmmlearn` isn't installed or fails to converge, `RegimeConfig.method="kmeans"`
runs a straight `sklearn.cluster.KMeans(n_clusters=3)` on the same 3 features.

---

## 4. Signal model (`signal_model.py` · 525 LOC)

**Purpose**: predict `P(is_correct = 1)` for each new feature snapshot.

### Architecture
- **Base**: `xgboost.XGBClassifier` (gradient-boosted trees)
- **Calibration**: `sklearn.calibration.CalibratedClassifierCV(method="sigmoid", cv=5)`
  — Platt scaling so confidence scores reflect real empirical frequencies.

### Hyperparameters (`SignalModelConfig`)

| Field | Default | Meaning |
|---|---|---|
| `n_estimators` | `300` | boosting rounds |
| `max_depth` | `4` | max tree depth |
| `learning_rate` | `0.05` | boosting eta |
| `subsample` | `0.8` | fraction of samples per tree |
| `colsample_bytree` | `0.8` | fraction of features per tree |
| `scale_pos_weight` | `1.0` | bump for imbalanced targets |
| `random_state` | `42` | reproducibility seed |
| `feature_columns` | `FEATURE_COLUMNS` | frozen order (see §2) |
| `model_version` | `"0.1.0"` | semver tag on artefact |

### Training flow (`SignalModel.fit`)

```
1. Select & order columns    →  X_feat = X[feature_columns]
2. Impute NaN → medians       →  X_imputed, cached medians stored
3. Fit base XGBClassifier on imputed matrix
4. Wrap with CalibratedClassifierCV(sigmoid, cv=5)
5. Extract feature_importances_ from fold-0 base estimator
6. Persist: model, medians, importances, calibration_stats → .joblib
```

### Inference flow (`SignalModel.predict`)

```
1. snapshot_to_vector(FeaturesSnapshot)   →  dict[str, float]  (NaN for None)
2. Apply training-time medians
3. calibrated.predict_proba(row)          →  P(is_correct=1)
4. Map probability → PredictionDirection:
     P ≥ 0.5  →  long   (confidence = P)
     P <  0.5  →  short  (confidence = 1 - P)
   Hold is surfaced at the calibration-gate step, not here.
5. Return SignalResult(direction, confidence, contributing_features)
```

### `CalibrationStats` (cached on every artefact)

```python
{
  "accuracy": float,        # fraction of correct directional calls
  "brier_score": float,     # lower = better
  "ece": float,             # expected calibration error, lower = better
  "n_predictions": int,     # labelled predictions used
  "model_version": str,
  "evaluated_at": datetime, # UTC
}
```

---

## 5. Pattern detectors (`patterns.py` · 708 LOC)

### Contract
- All detectors are **pure functions**: `(pd.DataFrame OHLCV) → PatternResult`.
- No I/O, no async, no side effects.
- Minimum input: **20 bars** — fewer returns `detected=False`, does not raise.
- DataFrame must have columns: `open, high, low, close, volume`, **chronological
  order (oldest first, last row is most-recent bar)**.

### Concurrency
`detect_all_patterns(ohlcv)` offloads the CPU-bound detector loop to a thread
pool via `asyncio.to_thread` so it doesn't block the FastAPI event loop.

### Pattern list (8)

| Pattern                      | Type        | Notes |
|------------------------------|-------------|-------|
| `double_bottom`              | reversal    | 17-complexity `detect_double_bottom()` — ~87 LOC, pivot-finding + symmetry constraints |
| `bullish_engulfing`          | candlestick | 2-bar reversal, body-size comparison |
| `bearish_engulfing`          | candlestick | mirror of bullish |
| `bull_flag`                  | continuation | consolidation range after strong up-leg |
| `rsi_divergence`             | divergence  | bullish/bearish, compares price pivots vs RSI pivots |
| `macd_crossover`             | crossover   | signal line cross + histogram polarity |
| `volume_surge`               | confirmation | volume > 2× 20d avg |
| `head_and_shoulders`         | reversal    | H&S and inverse H&S both detected |

### Confidence scoring
**Heuristic**, not trained. Each detector returns `confidence ∈ [0, 1]` reflecting
how cleanly the geometry matches the pattern (e.g. symmetry of the double bottom,
depth of RSI divergence, shoulder height variance). Always `0.0` when
`detected=False`.

---

## 6. Calibration & tier gates (`calibration.py` · 648 LOC)

### Core metrics

| Function | Formula |
|---|---|
| `brier_score(y_true, y_prob)` | `mean((y_prob - y_true)²)` — range `[0,1]`, lower = better |
| `expected_calibration_error(y_true, y_prob, n_bins=10)` | Σ per bin: `(bin_size/n) × \|acc_in_bin − avg_prob_in_bin\|` |
| `calibration_curve_data(y_true, y_prob, n_bins=10)` | Per-bin data points for the admin reliability diagram |

### Tier gating (the 3-stage unlock system)

Thresholds are the single source of truth in `calibration.py`. If you change one,
change this table in the same commit.

#### Tier 1 — Alerts (read-only predictions on UI)
| Gate | Threshold |
|---|---|
| Min accuracy | **0.55** |
| Min predictions | **100** |
| Max ECE | **0.15** |

#### Tier 2 — Paper trading
| Gate | Threshold |
|---|---|
| Tier 1 unlocked | required |
| Min accuracy | **0.60** |
| Min Sharpe (backtest) | **1.0** |
| Max drawdown | **15%** |

#### Tier 3 — Live execution (ML bot autonomously places real orders)
| Gate | Threshold |
|---|---|
| Tier 2 unlocked | required |
| Min accuracy | **0.62** |
| Min Sharpe (backtest) | **1.2** |
| Max drawdown | **12%** |
| Min live paper-trading days | **30** |
| User opt-in flag | required |

> **Note**: human owners (role=`owner`) can manually execute broker orders at any
> tier — the gate only governs **autonomous ML execution**. See
> `routes/broker.py:_is_execution_allowed`.

### Published performance targets
These are what the marketing site claims; must be validated against live stats
before any release:

| Metric | Target |
|---|---|
| Sharpe | **1.56** |
| Max DD | **11.2%** |
| Prediction accuracy | **62%** |
| Training samples | **276,000+** |

---

## 7. Training pipeline — `is_correct` target

### How a `chip_event` becomes a label

1. **Prediction emitted** — user opens War Room, `SignalModel.predict` runs on
   the live feature snapshot. Result stored in `predictions` collection with
   `(ticker, direction, confidence, features_snapshot, timestamp_emitted)`.
2. **Forward window opens** — scheduler waits `N` bars (default: 20 trading
   hours for intraday, 5 trading days for swing).
3. **Outcome evaluated** — at window close, compute realised price move.
   - `direction == "long"`: `is_correct = (close_end > close_emit)`
   - `direction == "short"`: `is_correct = (close_end < close_emit)`
   - `direction == "hold"`: excluded from training set entirely.
4. **Label written back** — `predictions.update_one({_id}, {"is_correct": bool})`.
5. **Aggregation** — nightly job aggregates labelled predictions into the
   training `DataFrame`.

### Nightly retrain job
- Scheduled by APScheduler (`services/scheduler_service.py`)
- Pulls last **N days** of labelled predictions (default 180).
- Re-fits `SignalModel` via the flow in §4.
- Evaluates new model on held-out window, emits fresh `CalibrationStats`.
- **Rejection rule**: if new Brier score is **worse than the currently-deployed
  model by > 5%**, the new artefact is archived but NOT promoted. Old model keeps
  serving. Failsafe against bad training data / regime shifts.

### Retraining triggers
- Cadence: every **00:00 UTC** (off-hours for US markets)
- Also triggered manually from Admin Panel → **Tools** tab

---

## 8. Inference pipeline (live path)

```
User opens War Room for TICKER
        │
        ▼
backend/routes/ai_war_room.py (FastAPI)
        │
        ▼
services/price_provider.py.get_quote() + get_ohlcv()
        │                            (multi-provider with failover)
        ▼
services/signal_service.py.build_snapshot()
   ├── computes: rsi_14, macd, sma_20/50, volume_ratio
   ├── fetches: sentiment_score, insider_activity, sector_momentum
   └── runs:    detect_all_patterns(ohlcv) → 8 booleans
        │
        ▼
RegimeModel.predict(price_series) → regime_label
        │
        ▼
SignalModel.predict(FeaturesSnapshot) → SignalResult
        │
        ▼
Calibration gate (§6) — reject if below threshold for user's tier
        │
        ▼
Dual-agent LLM pass (Strategist + Auditor)
   ├── Strategist: "why should we take this signal?"
   ├── Auditor:    "why should we NOT?"
   └── Router:     emergentintegrations.chat() with model rotation
        │
        ▼
Response streamed to the WarRoomHub UI
```

---

## 9. Admin observability

| Surface | What it shows |
|---|---|
| Admin Panel → **Cache** tab | MongoDB cache tiers, TTLs, hit rates |
| Admin Panel → **Providers** tab | Which market-data provider is active, last health-check, cost per call |
| Admin Panel → **Chip CTR** tab | L1/L2 chat-chip click-through rates |
| Admin Panel → **Help Search** tab | Unanswered help-search queries (roadmap input) |
| CalibrationChart (in-app) | Reliability diagram from `calibration_curve_data` |
| MLPaperPnL component | Live paper-trading P&L — feeds the Tier 3 30-day clock |
| `/api/ready` | 200 OK if DB connected + routes registered |

---

## 10. API key manifest (no values)

All keys live in `backend/.env` — never commit values to git. This table lists
every key name, what it does, whether it's active, and where to get a
replacement if you need to rotate.

### Core platform

| Env var | Required? | Purpose | Rotate via |
|---|---|---|---|
| `MONGO_URL` | **yes** | Primary DB connection string | Atlas dashboard |
| `DB_NAME` | **yes** | Mongo database name | Atlas dashboard |
| `JWT_SECRET` | **yes** | Signs httpOnly auth cookies + derives Fernet key for broker secrets. **Rotating will invalidate all active sessions AND make saved broker creds undecryptable.** | `python -c "import secrets;print(secrets.token_hex(32))"` |
| `CORS_ORIGINS` | **yes** | CSV of allowed origins (currently `*`) | n/a |
| `FRONTEND_URL` | **yes** | Public origin for email links + OAuth redirect | n/a |
| `VAULT_SECRET` | **yes** | Server-side admin KeyVault passphrase | `python -c "import secrets;print(secrets.token_hex(32))"` |

### Owner seed (hardcoded override in `routes/auth.py` since Feb 2026)

| Env var | Required? | Purpose | Notes |
|---|---|---|---|
| `OWNER_EMAIL` | no (fallback) | Seeded owner account email | Forced to `admin@risedual.ai` in code regardless of env |
| `OWNER_PASSWORD` | no (fallback) | Seeded owner password | Forced to canonical in code; rotate inside the app via **Settings → Change Password** |

### Emergent-managed LLM

| Env var | Required? | Purpose | Rotate via |
|---|---|---|---|
| `EMERGENT_LLM_KEY` | **yes** | Unified key for OpenAI + Anthropic + Gemini via `emergentintegrations` | Profile → Universal Key in Emergent chat |
| `EMERGENTLLMKEY` | duplicate | Legacy alias, same value | same |

### Direct-provider LLM (optional fallback)

| Env var | Status on prod | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | **empty** — using Emergent | https://platform.openai.com/api-keys |
| `ANTHROPIC_API_KEY` | **empty** — using Emergent | https://console.anthropic.com/settings/keys |
| `OPENROUTER_API_KEY` | **populated** — fallback for GPT-5.2 | https://openrouter.ai/keys |
| `OPENROUTER_API_KEYS` | **empty** — CSV for multi-key rotation | same |
| `GROQ_API_KEYS` | **empty** | https://console.groq.com/keys |

### Market data

| Env var | Status | Purpose | Rotate via |
|---|---|---|---|
| `POLYGON_API_KEY` | **populated** | Primary OHLCV + real-time quotes | https://polygon.io/dashboard/keys |
| `FINNHUB_API_KEY` | **populated** | Secondary quotes, company news, earnings | https://finnhub.io/dashboard |
| `ALPHA_VANTAGE_API_KEY` | **populated** | Fundamentals + FX backup | https://www.alphavantage.co/support/#api-key |
| `ALPHA_VANTAGE_API_KEY_2` | **populated** | Second key for rate-limit bursting | same |
| `ALPHAVANTAGEAPIKEY` | duplicate alias | legacy name, same value | same |
| `TWELVEDATA_API_KEY` | **empty** | Unused — can be deleted | https://twelvedata.com/account/api-keys |
| `FRED_API_KEYS` | **populated** | Macroeconomic series (Fed data) | https://fred.stlouisfed.org/docs/api/api_key.html |
| `QUIVER_API_KEY` | **populated** | Government insider / Congress trades / lobbying | https://api.quiverquant.com/ |
| `STOCKFIT_API_KEY` | **populated** | 13F / insider-activity stream | internal vendor |
| `OPENFIGI_API_KEY` | **populated** | CUSIP → ticker resolution for SEC / 13F | https://www.openfigi.com/api |
| `TAVILY_API_KEY` | **populated** | Web search for the chat assistant | https://tavily.com/#api |

### Brokers

| Env var | Status | Purpose | Rotate via |
|---|---|---|---|
| `ALPACA_API_KEY` | **populated** | Stocks/options broker (paper now) | https://app.alpaca.markets/paper/dashboard/overview |
| `ALPACA_SECRET_KEY` | **populated** | Alpaca secret | same |
| `ALPACA_BASE_URL` | **populated** | `https://paper-api.alpaca.markets` currently; flip to live URL when approved | same |
| `KRAKEN_API_KEY` | **populated** | Crypto broker (LIVE) | https://www.kraken.com/u/security/api |
| `KRAKEN_API_SECRET` | **populated** | Kraken private key | same |

### Payments

| Env var | Status | Purpose | Rotate via |
|---|---|---|---|
| `STRIPE_API_KEY` | **populated** | Live secret key (server) | https://dashboard.stripe.com/apikeys |
| `STRIPE_SECRET_KEY` | **populated** — _test_ key | Stripe test key for sandbox | same, toggle test/live |
| `STRIPE_PUBLISHABLE_KEY` | **populated** | Publishable key for Stripe Elements | same |
| `STRIPE_WEBHOOK_SECRET` | **populated** | Validates Stripe webhook signatures | Stripe → Webhooks → endpoint details |
| `STRIPE_PRICE_STARTER` | **populated** | Price ID for Starter plan | Stripe → Products |
| `STRIPE_PRICE_PRO` | **populated** | Price ID for Pro plan | same |
| `STRIPE_PRICE_PRO_MAX` | **populated** | Price ID for Pro Max | same |
| `STRIPE_PRICE_TOPUP_1000` | **populated** | 1,000-credit top-up | same |
| `STRIPE_PRICE_TOPUP_2000` | **populated** | 2,000-credit top-up | same |
| `STRIPE_PRICE_TOPUP_5000` | **populated** | 5,000-credit top-up | same |
| `STRIPE_PRICE_TOPUP_10000` | **populated** | 10,000-credit top-up | same |
| `PAYPAL_CLIENT_ID` | placeholder (`your_paypal_client_id`) — not in use | PayPal integration stub | https://developer.paypal.com/dashboard/applications |
| `PAYPAL_CLIENT_SECRET` | placeholder — not in use | same | same |
| `PAYPAL_MODE` | `sandbox` | Flip to `live` if PayPal is ever wired | n/a |

### Email / Notifications

| Env var | Status | Purpose | Rotate via |
|---|---|---|---|
| `RESEND_API_KEY` | **populated** | Transactional email (digest, password reset, security alerts) | https://resend.com/api-keys |
| `SENDER_EMAIL` | **populated** — `support@risedual.ai` | From-address for all outbound email | DNS / Resend domain verification |
| `SENDGRID_API_KEY` | **empty** — fallback provider, unused | https://app.sendgrid.com/settings/api_keys |
| `VAPID_PUBLIC_KEY` | **populated** | Web-push public key (in service worker) | generate new pair with `pywebpush` |
| `VAPID_PRIVATE_KEY` | **populated** | Web-push private key | same |
| `REACT_APP_VAPID_PUBLIC_KEY` | **populated** | Same VAPID public key, exposed to frontend | same |

### Frontend-only (non-secret, just wiring)

| Env var | Purpose |
|---|---|
| `REACT_APP_BACKEND_URL` | Public backend origin — must match the deployment |
| `WDS_SOCKET_PORT` | Dev-server hot-reload port (preview pod only) |

### Deployment platform

| Env var | Purpose |
|---|---|
| `ENABLE_HEALTH_CHECK` | `true` — Emergent deploy verifier calls `/api/ready` |

---

## Appendix A — Filing a key rotation

When rotating ANY of the above:

1. Generate new key/secret at the provider dashboard (URLs in manifest).
2. Paste into `backend/.env` locally (preview pod) for testing.
3. Restart backend: `sudo supervisorctl restart backend`.
4. Confirm via provider-specific probe (e.g. `/api/broker/execution-status` for
   broker keys, `/api/stocks/quote/AAPL` for market-data keys).
5. Add/update the secret in the **Emergent deployment settings → Environment
   Variables** panel.
6. Deploy.
7. Revoke the old key at the provider.

**Never paste values into chat, commit them to git, or log them to stdout.**

---

## Appendix B — Dependency isolation

Why the ML modules guard their imports:

```python
# Inside every function that needs pandas/xgboost/sklearn:
try:
    import pandas as pd
except ImportError as exc:
    raise ImportError(
        "pandas is required for X. Install with: pip install pandas"
    ) from exc
```

This means:
- `from risedual_core.ml import SignalModel` **always works** (just imports the class).
- `SignalModel().fit(X, y)` **requires** xgboost + sklearn + numpy + pandas —
  raises a clear `ImportError` if any are missing.
- CI and tests can import the package without the heavy ML stack.
- Cold starts in lightweight adapters (e.g. CLI-only commands) are fast.

---

## Appendix C — Hot rules / invariants

1. `FEATURE_COLUMNS` order is **frozen**. Append new features only.
2. Pattern columns default to `False` at inference — never train without them.
3. Nightly retrain **must** reject models that worsen Brier by >5% vs prior.
4. Tier 3 (live ML execution) requires `RISEDUAL_LIVE_EXECUTION=1` env flag
   (not stored in `.env` by default — only flipped intentionally).
5. Broker credentials are Fernet-encrypted with a key derived from `JWT_SECRET`.
   Rotating `JWT_SECRET` invalidates saved broker creds → users must reconnect.

---

_End of spec._
