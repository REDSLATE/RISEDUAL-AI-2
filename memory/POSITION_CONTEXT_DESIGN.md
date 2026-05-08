# `position_context` — Design Contract for Terminal Phase T2

**Status**: DESIGN ONLY. Implementation blocked until operator signs off on the
rules captured here. This doc is the single source of truth for Phase T2
(`GET /api/terminal/top-actions`).

**Why this doc exists**: Phase T2 was intentionally held back in the prior
session because a "top opportunities" list that ignores the user's existing
book is a retail-blowup generator. Correlated opportunities would pile on
the same directional risk; a user already long QQQ would see AAPL + MSFT +
NVDA all recommended simultaneously and stack beta into one trade dressed
up as three. This doc pins down how we dedup, rank, and time-anchor those
lists before any code ships.

---

## 1. `PositionContext` — canonical shape

A single in-memory object constructed once per `/top-actions` request.
Immutable, JSON-serialisable, no Mongo handles leak through.

```python
@dataclass(frozen=True)
class PositionContext:
    user_id: str

    # Open equity positions (paper + live), normalized to a common shape.
    # Sourced from `paper_positions` + broker positions API.
    equity: list[EquityPosition]

    # Open options positions (single-leg + multi-leg spreads).
    # Sourced from `option_positions` / `option_orders`.
    options: list[OptionPosition]

    # Open crypto positions. Sourced from `crypto_positions`.
    crypto: list[CryptoPosition]

    # Derived aggregates — computed once, cheap to reuse downstream.
    gross_equity_usd: float        # sum of abs(position_size_usd)
    net_equity_usd: float          # signed: long − short
    symbols_long: frozenset[str]   # fast membership test
    symbols_short: frozenset[str]
    sectors_exposed: dict[str, float]  # {sector: exposure_usd}
    beta_weighted_long: float      # Σ(position_usd × beta)
    beta_weighted_short: float

    # Data-freshness flags. If any upstream read degraded, the caller
    # may choose to downgrade confidence on the emitted top-actions
    # list (never to suppress it outright — degrading to "no actions"
    # is a correctness regression).
    stale_broker_positions: bool
    stale_sector_map: bool
    as_of: datetime
```

```python
@dataclass(frozen=True)
class EquityPosition:
    symbol: str
    direction: str                 # "LONG" | "SHORT"
    quantity: float
    entry_price: float
    mark_price: float | None
    position_size_usd: float       # signed: + for long, − for short
    sector: str | None             # pulled from universe snapshot
    beta: float | None             # 60-day, SPY-anchored
    source: str                    # "paper" | "alpaca" | "kraken" | "ibkr"
    opened_at: datetime
```

```python
@dataclass(frozen=True)
class OptionPosition:
    underlying: str
    legs: list[OCCLeg]             # 1 leg = single; >1 = spread
    structure: str                 # "long_call" | "short_put" | "vertical" | ...
    delta_equivalent: float        # Σ(leg_delta × contracts × 100) — shares-equivalent
    premium_at_risk_usd: float
    dte_days: int                  # shortest leg
```

```python
@dataclass(frozen=True)
class CryptoPosition:
    symbol: str                    # "BTCUSDT" / "ETHUSDT"
    direction: str
    quantity: float
    entry_price: float
    position_size_usd: float
    venue: str                     # "kraken" / "paper"
```

---

## 2. Data sources

| Source                          | Driver                                                               | Refresh | Fail-safe                                                              |
| ------------------------------- | -------------------------------------------------------------------- | ------- | ---------------------------------------------------------------------- |
| `paper_positions` (Mongo)       | Local writer (`ml_paper_trader`, `smart_order_service`)              | live    | Treat missing collection as empty list; `stale=False`                  |
| Alpaca live positions           | `brokers.alpaca_adapter.get_positions()`                             | on-req  | Cache last success 60s; flag `stale_broker_positions=True` on fall-back |
| Kraken live positions           | `brokers.kraken_adapter.get_positions()`                             | on-req  | Same 60s fallback                                                      |
| IBKR positions (future)         | Not wired yet                                                        | —       | N/A                                                                    |
| `option_positions`              | `options_trading` route + reconciler                                 | live    | Treat missing as empty                                                 |
| `crypto_positions`              | `crypto_paper_trader` / `crypto_broker`                              | live    | Treat missing as empty                                                 |
| **Beta**: `symbol_beta_cache`   | 60-day SPY-regressed; refreshed nightly                              | nightly | `beta=None` on miss → symbol excluded from beta-dedup                  |
| **Sector**: `top_universe`      | Cached snapshot keyed by symbol                                      | 5-min   | `sector=None` → symbol excluded from sector-dedup                      |

**One builder function**: `services/position_context.build(db, user_id) -> PositionContext`.
Pure async read. Never writes. Never calls external APIs beyond the broker
adapters (which already have their own cache + circuit breaker).

---

## 3. Correlation dedup rules

Dedup runs on the **candidate list from Terminal aggregator**, BEFORE
ranking. The goal: don't emit two candidates that would stack risk on
top of an existing position or on top of each other.

**Rule set (applied in order, first match wins)**:

### Rule 1 — Exact overlap
If a candidate `symbol` is already in `symbols_long` / `symbols_short`
with the same direction → **DROP** with `reason="already_held_same_dir"`.

If the candidate direction is the *opposite* of a held position → **TAG**
`action_type="CLOSE_OR_REVERSE"` and keep, but rank as a hedge not a
new entry. (Operator intent: "the model sees my AAPL long is now
vulnerable" is valuable; we don't want to hide that.)

### Rule 2 — Sector saturation
For each sector, count existing open exposure + already-admitted
candidates. Saturation triggers when:

| Metric                              | Threshold | Rationale                                |
| ----------------------------------- | --------- | ---------------------------------------- |
| sector gross exposure / total gross | > 25%     | one sector dominates the book            |
| candidate count in sector           | > 3       | already emitting 3 names, 4th is noise   |

Saturated sector → **DROP** remaining candidates in that sector with
`reason="sector_saturated:<sector>"`.

### Rule 3 — Beta similarity (only applies to bulk long / bulk short)
When the book is net-long ≥ $X gross, drop new LONG candidates whose
beta is within `0.15` of an existing position's beta **AND** in the
same sector. Pure beta-similarity alone is NOT enough to drop (lots of
mega-caps have β≈1.0 but uncorrelated drivers); we require sector
overlap too.

`reason="beta_cluster:<existing_symbol>"`.

### Rule 4 — Options delta-equivalent overlap
If an options position exists with `delta_equivalent ≥ 500 shares` on
the same underlying as a candidate — in the same direction — **DROP**
with `reason="options_delta_overlap"`.

### Rule 5 — Crypto pair clustering
BTC/ETH are treated as one cluster for correlation purposes. A user
already holding BTC long + ETH long gets `crypto_cluster_saturated` on
any additional majors (SOL, AVAX, etc.) unless the aggregator tags the
candidate as a clear decorrelation pick.

---

## 4. Time horizon anchoring

Every candidate emitted by T2 carries **one** of:

```
horizon: "intraday" | "swing" | "multi_day"
```

**Mapping**:
- `intraday` — target close ≤ end of current RTH session. Triggered by
  momentum / order-flow / options-IV moves.
- `swing` — target close 1-5 trading days out. Triggered by pattern
  completion, earnings-related setups.
- `multi_day` — target close 5-20 days out. Triggered by smart-money
  flow shifts, sector rotation signals.

**T2 contract** returns a single list but every row is explicitly
horizon-tagged. The Terminal UI is free to group or filter by horizon;
the backend does NOT split into separate lists (prevents accidental
cross-horizon double-ranking).

**One candidate, one horizon**. A setup that qualifies for both intraday
and swing horizons is emitted ONCE at the tighter horizon (intraday >
swing > multi_day). Rationale: emitting the same `AAPL-LONG` at two
horizons is the "stack same trade three times" failure mode in disguise.

---

## 5. Ranking

After dedup, candidates rank by:

```
score = 0.40 × signal_strength
      + 0.25 × liquidity_quality   (from options/equity spread telemetry)
      + 0.20 × smart_money_alignment  (if available, else 0.5 neutral)
      + 0.10 × regime_fit
      + 0.05 × recency_boost       (freshly discovered > restated)
```

Top 5 returned; no pagination (this is a focus-list surface, not a
browse list).

---

## 6. Endpoint contract

```
GET /api/terminal/top-actions?horizon=<optional filter>&limit=<default 5, max 10>

→ 200:
{
  "as_of": ISO UTC,
  "position_context_summary": {
    "equity_count": int, "options_count": int, "crypto_count": int,
    "gross_equity_usd": float, "sectors": {sector: exposure_usd}
  },
  "candidates": [
    {
      "symbol": str,
      "action": "LONG" | "SHORT" | "CLOSE_OR_REVERSE",
      "horizon": "intraday" | "swing" | "multi_day",
      "score": float 0-100,
      "rationale": str,               # 1-line, operator-readable
      "signal_sources": [str, ...],   # {"orderflow","smart_money","regime",...}
      "risk_tags": [str, ...],        # {"sector_concentration_watch", ...}
      "dedup_survivors_of": [str],    # candidates this one outranked
    },
    ...
  ],
  "dropped": [                         # transparency — what got deduped
    {"symbol": str, "reason": str, "rule": "1" | "2" | "3" | "4" | "5"}
  ]
}
```

---

## 7. Safety invariants (will be pinned by tests)

1. **Never generates signals** — the route is a view aggregator over
   existing state, matching the Terminal Aggregator discipline.
2. **Degrades to empty, never to stale** — a broken upstream returns an
   empty `candidates: []` with `stale_*` flags set; never a cached list
   from an earlier hour.
3. **Correlation rules MUST fire before ranking** — two correlated
   candidates can't both make the top 5 just because they're both
   highly-scored; Rule 2/3 run first.
4. **Position context is read-only** — building it must not fire writes
   to any collection.
5. **One horizon per candidate** — duplicates across horizon buckets
   are the single largest failure mode this design guards against.

---

## 8. Open questions for operator sign-off

Before implementation kicks off, please confirm or amend:

1. **Position sources**: paper-only to start, or include Alpaca/Kraken
   live from day 1? (Leaning: include live from day 1 — the whole point
   of T2 is position-aware, and a paper-only context would recommend
   against only paper positions.)
2. **Beta threshold**: `0.15` (this doc) vs `0.20` (tighter dedup).
3. **Sector saturation percentage**: `25%` of gross vs `30%`.
4. **Crypto cluster**: is "BTC + ETH = one cluster" too aggressive?
   Should they be treated as decorrelated for this purpose?
5. **Horizon override**: should the UI be allowed to force a horizon
   (`?horizon=intraday`), filtering the candidate set? This doc says
   yes but the default is all-horizons.
6. **Empty-context behaviour**: when a user has zero positions, T2
   should behave exactly like a naive top-5 list — no dedup can apply,
   just rank. Confirm?

Answers here drive implementation. No code will ship until each of
these has an operator-signed value.
