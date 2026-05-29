# Federation Phase 4 — Outcome Backfill Loop Activated

**Audience**: External brain sidecar authors (Alpha, Camaro, Chevelle, RedEye)
**Status**: Trading-app codebase deployed; outcome-stamping wire is live
**Severity**: FYI / opportunity — your sidecar's local mirror can now do the same

---

## 1. What changed on the trading-app side

The 5-Shelly Federation moved from "scribe-only" to **closed-loop learning**:

- Each LocalShelly node (Alpha / Camaro / Chevelle / RedEye / MC) emits a memory at decision time with an **empty `outcome` field** — same as before.
- **NEW**: When a paper trade closes, `services/paper_trade_closer` invokes `shelly.outcome_backfill.backfill_outcome_for_trade(...)`. This stamps `outcome.pnl_pct` + `outcome.outcome_label` back onto every Shelly memory that matched the trade's `(symbol, direction, opened_at±10min)` window — across all 5 LocalShellys AND the MC shared collection.
- The next reasoning call (`LocalShelly.reason()` / `MCShelly.reason_across_shellys()`) now picks up the resolved outcomes naturally — they only count memories with `outcome.pnl_pct` present.

Without this, the federation's reasoning paths would permanently report *"Not enough Shelly memory yet"* no matter how many decisions accumulated — they filter on `outcome.pnl_pct exists`.

---

## 2. Doctrine pin (unchanged)

- Every outcome write touches **only** the `outcome` sub-document. The top-level `authority: memory_reasoning_only` stamp is never modified.
- Per-node `try/except` — a Mongo failure on one collection NEVER blocks the calling close path.
- **Idempotent** — only stamps memories whose `outcome.pnl_pct` has not been written yet. Re-running the backfill is a no-op.

---

## 3. New API surface (read-only)

### `GET /api/admin/shelly-federation/consensus?symbol=AAPL&direction=LONG`

Returns the federation's current rolled-up verdict for one `(symbol, direction)`. Runs `reason()` on every node + the MC cross-brain aggregator and applies the conservative-priority rule (`warn > neutral > support`).

Response shape:

```json
{
  "authority": "memory_reasoning_only",
  "query": {"symbol": "AAPL", "direction": "LONG"},
  "per_node": {
    "Alpha":    {"recommendation": "neutral", "confidence_delta": 0.0, ...},
    "Camaro":   {...},
    "Chevelle": {...},
    "RedEye":   {...},
    "MC":       {...}
  },
  "mc_cross_brain": {"recommendation": "neutral", "by_brain": {...}, ...},
  "rolled_up": {
    "recommendation": "neutral",
    "confidence_delta": 0.0,
    "rule": "warn>neutral>support; most-conservative wins"
  }
}
```

Auth: owner JWT (X-Auth or `Authorization: Bearer`).

---

## 4. Symmetric pattern your sidecar can adopt

If your sidecar maintains its own LocalShelly mirror (per the previously-shared 5-Shelly federation contract), the symmetric backfill on YOUR side looks like this:

```python
# When you observe a closed paper_trade for symbol+direction,
# stamp the outcome onto every matching memory in your local
# Mongo (or equivalent durable store):

await db["shelly_<brain>_memories"].update_many(
    {
        "symbol": symbol_upper,
        "direction": canonical_dir,  # LONG/SHORT/HOLD
        "created_at": {"$gte": lo_iso, "$lte": hi_iso},
        "$or": [
            {"outcome": None},
            {"outcome.pnl_pct": {"$exists": False}},
        ],
    },
    {"$set": {
        "outcome.pnl_pct": float(pnl_pct),
        "outcome.outcome_label": outcome_label,
        "outcome.trade_id": trade_id,
        "outcome.backfilled_at": <iso utc now>,
    }},
)
```

Window: ±10 minutes around `opened_at` (configurable). The window binds outcomes to a single decision cluster (hypothesis fan-out + execution emit all land within seconds of each other).

---

## 5. What does NOT require coordination

- **You do NOT need to mirror the trading-app's outcome.** The trading-app's outcome write is purely local to its own Shelly collections. Your sidecar's local Shelly is independent.
- **You do NOT need to call any new API.** Your existing opinion / contribution surfaces are unchanged.
- **You do NOT need to wait for MC.** The MC verifier path is unaffected by Phase 4. Promotion gates, seat-roster, and the `/api/ingest/opinion` contract are all stable.

The trading-app team is sharing this only because the federation works best when all 5 nodes are reasoning over resolved outcomes — if your brain's local mirror has 10k decisions with empty outcomes, your local reasoning will be inert until you wire the symmetric stamp.

---

## 6. Verification on the trading-app side

After redeploy, the operator can confirm Phase 4 is live by:

```bash
# (1) consensus endpoint responds with 5 nodes + MC
curl -sH "Authorization: Bearer $OWNER_TOKEN" \
  "$RISEDUAL_BASE/api/admin/shelly-federation/consensus?symbol=AAPL&direction=LONG" \
  | jq '.per_node | keys, .rolled_up.recommendation'

# (2) state endpoint shows non-zero memory counts climbing
curl -sH "Authorization: Bearer $OWNER_TOKEN" \
  "$RISEDUAL_BASE/api/admin/shelly-federation/state" \
  | jq '.nodes | to_entries[] | {node: .key, memories: .value.memories_total}'

# (3) after a paper trade closes, the matching memory's outcome
#     field is populated:
#     db.shelly_alpha_memories.findOne(
#       {symbol: "AAPL", "outcome.pnl_pct": {$exists: true}}
#     )
```

Definition of done (operator-side): every closed paper trade produces at least one Shelly memory with `outcome.pnl_pct` filled in across the matching `(symbol, direction)` window.

---

## 7. Honest flags

1. **No breaking change.** The Phase 1–3 wire is unchanged — same `record_brain_event` entry point, same dataclass schema, same `authority: memory_reasoning_only` stamp.
2. **Backwards-compatible.** Existing memories without an `outcome` field stay as-is forever — Phase 4's stamper only writes to memories within the lookback window of a NEW close.
3. **Reasoning thresholds unchanged.** `LOCAL_MIN_SAMPLES=5`, `LOCAL_LOSS_RATE_WARN=0.60`, `MC_MIN_SAMPLES=10`, `MC_LOSS_RATE_WARN=0.60`, `MC_LOSS_RATE_SUPPORT=0.35`. The federation will start producing non-neutral recommendations once at least 5 resolved memories accumulate for a given `(symbol, direction)` pair — at current paper-trade volume that's ~1–3 trading days for active tickers.
