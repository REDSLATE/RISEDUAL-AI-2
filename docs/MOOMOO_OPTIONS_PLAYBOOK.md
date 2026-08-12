# MooMoo Options Execution Playbook — V1

**Status:** V1 draft, 2026-02. Read-only in production. This document
codifies the policy that must be in place before we flip
``MOOMOO_OPTIONS_ENABLED=1`` and let Alpha route live options orders
through MooMoo. Public.com's options coverage is limited enough that we
want a policy layer we can flip on with confidence — not a hopeful
default.

Until this playbook says otherwise:

    MOOMOO_OPTIONS_ENABLED=0     # autonomous options remain OFF
    submit_option()              # returns options_disabled

The only live production surface at V1 is the read-only preview
endpoint (`GET /api/admin/moomoo/options/preview/{symbol}`), which
reports *what Alpha would pick right now* without submitting an order.

---

## 1. Instrument selection

| Rule                | V1 default            | Env var                          |
|---------------------|-----------------------|----------------------------------|
| Delta band          | 0.30 – 0.45           | `MOOMOO_OPT_DELTA_MIN` / `MAX`   |
| DTE window          | 7 – 21 days           | `MOOMOO_OPT_DTE_MIN` / `MAX`     |
| Open interest floor | ≥ 1000                | `MOOMOO_OPT_MIN_OI`              |
| Daily volume floor  | ≥ 500                 | `MOOMOO_OPT_MIN_VOL`             |
| Spread cap          | ≤ 8% of option mid    | `MOOMOO_OPT_MAX_SPREAD_PCT`      |
| Stale quote max age | ≤ 30 seconds          | `MOOMOO_OPT_STALE_QUOTE_SEC`     |
| Max contracts       | 1 initially           | `MOOMOO_OPT_MAX_CONTRACTS`       |
| Earnings blackout   | ON (short-DTE)        | `MOOMOO_OPT_EARNINGS_BLACKOUT`   |

**Why these numbers:**

* 0.30–0.45 delta keeps us in the region where directional edge
  actually matters; ≤0.20 becomes a lottery ticket, ≥0.60 is essentially
  a stock proxy with worse liquidity.
* 7–21 DTE avoids weeklies (gamma bomb) and monthly LEAPs (theta drift
  hides the underlying signal). It also concentrates measurement risk
  in a bounded window Alpha can actually validate.
* OI 1000 + volume 500 is a soft liquidity floor; below that the
  spread cap will hit first anyway, but the explicit floor catches
  spoofed quotes.
* Spread cap is enforced as a **percentage of mid**, not in cents.
  A $0.10 spread on a $0.50 contract is 20% (unusable). The same
  $0.10 spread on a $5.00 contract is 2% (fine). The percentage
  test is what actually corresponds to bid/ask damage.
* 30s stale-quote max prevents the classic "chain was live at open,
  the market moved 40 bps, but our snapshot is unchanged" bug.

## 2. Order type — never MARKET

Options MARKET orders are unsafe. Even on liquid names a fleeting
one-lot ask can print at 3× mid before Alpha can react. V1 uses only
**marketable LIMIT** orders:

* BUY: limit = mid × (1 + slippage_offset), capped at ask.
* SELL: limit = mid × (1 − slippage_offset), floored at bid.
* Time in force: DAY (never IOC — IOC on options routinely dies
  mid-cross to a hidden ISO order).

## 3. Sizing

    contracts = min(policy.max_contracts, floor(risk_$ / (entry_debit × 100 × stop_R)))

where `risk_$` is the same per-trade risk budget the equity path
already uses, `entry_debit` is the mid at submit time, and `stop_R`
is the underlying stop distance in R (default 1.0).

**V1 cap:** `max_contracts = 1`. This is not because the sizing model
lacks confidence — it's because MooMoo's option execution surface has
never fired in this stack. Prove the plumbing on one contract before
we scale.

## 4. Risk guards (checked pre-submit)

* One position at a time on the underlying (equity **or** option).
* Max concurrent option contracts open across the account: 3.
* Earnings blackout: no *new* short-DTE (< 15 DTE) positions inside
  the 24h window before an announcement.
* Max theta per day (portfolio-level): tuned once real fills exist —
  V1 disables the theta budget check because we have no baseline.

## 5. Kill switches

| Trigger                                          | Action                       |
|--------------------------------------------------|------------------------------|
| 2 consecutive rejections from `place_order`      | Halt options for 15 minutes  |
| Realized daily options PnL ≤ −2R                 | Halt options for the day     |
| Spread on the position widens > 15%              | Alert, close on next tick    |
| Fill latency > 3s repeatedly                     | Alert + operator review      |

The equity kill switches remain independent; an options halt never
takes down equity execution and vice versa.

## 6. Preview endpoint contract

    GET /api/admin/moomoo/options/preview/{symbol}?direction=call&dte_max=45

Returns:

    {
      "available": true,
      "symbol": "AAPL",
      "direction": "call",
      "chain_size": 128,
      "selected": {
        "symbol": "US.AAPL250314C210000",
        "underlying": "AAPL",
        "opt_type": "call",
        "strike": 210.0,
        "expiry": "2025-03-14",
        "dte": 14,
        "delta": 0.38,
        "bid": 2.10,
        "ask": 2.18,
        "mid": 2.14,
        "spread_pct": 3.7,
        "open_interest": 4212,
        "volume": 913,
        "estimated_debit": 2.14,
        "estimated_max_risk": 214.0,
        "contracts": 1,
        "why_selected": "delta 0.38 in band [0.3-0.45], DTE 14d in window [7-21], spread 3.7% ≤ 8%, OI 4212, volume 913."
      },
      "rejected": [
        {"symbol": "US.AAPL250314C215000", "delta": 0.25, "dte": 14, "rejection": "delta_out_of_band(0.25)"},
        {"symbol": "US.AAPL250207C205000", "delta": 0.42, "dte": 4,  "rejection": "dte_out_of_range(4)"}
      ],
      "rule_hits": {"delta_out_of_band": 12, "dte_out_of_range": 8, "spread_over_cap": 3},
      "candidates_evaluated": 128,
      "policy_used": { ... }
    }

The preview endpoint **must never submit an order**. It exists so we
can validate contract selection against real live chains without any
live-execution risk.

## 7. Flip checklist for `MOOMOO_OPTIONS_ENABLED=1`

Before enabling autonomous options execution:

1. **OpenD sanity:** `GET /api/admin/moomoo/status` shows
   `connected=true` and `acc_id_configured=true`.
2. **Entitlements:** `GET /api/admin/moomoo/entitlements` shows an
   OPRA options entitlement in `sub_list`.
3. **Preview drills:** Run the preview endpoint against 5 liquid
   underlyings during RTH. Each should return a `selected` contract
   with `spread_pct ≤ 8%` and `dte` inside 7–21.
4. **Policy inspection:** `GET /api/admin/moomoo/options/policy`
   returns the exact policy you expect (delta 0.30–0.45, DTE 7–21,
   `enabled=true` once the env flag is set).
5. **Kill-switch harness:** Verify equity halt does not disable
   options and vice versa (unit test).
6. **Broker Comparison health:** Public.com equity fills continue to
   record into `broker_comparison`. No regressions in the equity
   ack-latency p50.
7. **First-fire canary:** With `MOOMOO_OPTIONS_ENABLED=1` and
   `MOOMOO_OPT_MAX_CONTRACTS=1`, wait for a real Alpha long-call
   signal on a name from the allowlist. Watch the fill in the
   Broker Comparison panel. Halt after the first fill and reconcile.

Only after step 7 succeeds do we consider incrementing
`MOOMOO_OPT_MAX_CONTRACTS` or expanding the underlyings allowlist.

## 8. Deliberately not in V1

* Multi-leg spreads / verticals / iron condors — chain preview is
  single-leg only.
* Delta-hedging with underlying stock.
* Rolling contracts. When a position needs to be extended past DTE
  we close-then-open explicitly, not roll.
* Automated theta budgeting. Comes in V2 after real fills exist.

## 9. Contact points in the code

| Concern                     | File                                                    |
|-----------------------------|---------------------------------------------------------|
| Policy config + selection   | `backend/services/moomoo_options_policy.py`             |
| Live chain fetch            | `backend/services/moomoo_market_data_adapter.py` → `option_chain()` |
| Submit stub (still gated)   | `backend/services/moomoo_broker_adapter.py` → `submit_option()`     |
| Preview endpoint            | `backend/routes/admin_moomoo.py` → `/options/preview/{symbol}`      |
| Policy endpoint             | `backend/routes/admin_moomoo.py` → `/options/policy`                |
