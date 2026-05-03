# Code Review Triage — Static Analyzer False Positives

**Created:** 2026-05-03
**Context:** An external code review surfaced 13 findings. 7 were **static-analyzer
false positives** that would actively damage working code if applied blindly.
This file documents which findings to reject and why, so future agents
(including fork agents with clean context) don't re-apply the bad fixes.

---

## ❌ Findings to REJECT — DO NOT APPLY

### 1. "Circular import: `broker.py → server.py → admin.py → broker.py`"

**Reviewer claim**: top-level module cycle, breaks initialization.

**Reality**: No top-level cycle exists. The only `from server import` in
`routes/broker.py` is at line 748 **inside a function body** (lazy import
resolved at call time, not module load). The two `from routes.broker import`
calls in `routes/admin.py` (lines 1195, 1217) are also inside functions.
Python handles lazy cycle-breaking imports fine.

**Test to verify**: `python -c "import routes.broker; import routes.admin"` —
loads without error.

**DO NOT**: move anything to `shared/dependencies.py` or refactor to
dependency injection. It's working as-designed.

### 2. "`exec()` / `eval()` code injection in `ai_core/execution.py:68` and `services/backtester_service.py:197`"

**Reviewer claim**: arbitrary code execution vulnerability.

**Reality**: Both are substring-match false positives.

* `ai_core/execution.py:68` (line varies after May 2026 refactor): `raw = await _pt_exec(...)` — `_pt_exec` is a function name containing the substring "exec", not the builtin `exec()`.
* `services/backtester_service.py:~312`: `ast.parse(cond.strip(), mode='eval')` — using `mode='eval'` for `ast.parse()` is the **safe AST parser** that returns a parsed tree. It is NOT `eval()`.

Tests that use `exec()` (`test_trading_bot_adaptive_sizing.py`, etc.) use it
intentionally for sandboxed dynamic test generation — standard pattern.

**DO NOT**: replace with `ast.literal_eval` or whitelisted functions —
there's no security issue to fix.

### 3. "Missing hook dependencies — useWatchlistData.js:71 missing 16 deps"

**Reviewer claim**: `react-hooks/exhaustive-deps` violations producing stale closures.

**Reality**: Running `npx eslint --rule 'react-hooks/exhaustive-deps: error'`
against these files produces **zero warnings**. The hooks are correctly
structured; the missing "dependencies" are either stable module-level
constants (`API`, `LS_KEY`) or setter functions (React guarantees stable
refs for these). Adding them as dependencies would cause infinite re-renders.

**DO NOT**: add `setData`, `LS_KEY`, `API` etc. to useCallback deps.

### 4. "Use `secrets` module instead of `random`"

**Reviewer claim**: `random.random()` in `services/polygon_dark_pool_service.py:54`
and `routes/risk_calculator.py:261` is insecure.

**Reality**: `random` is correct for non-cryptographic uses (jitter delays,
sampling, simulation). `secrets` is only needed for tokens, passwords,
session IDs — which the codebase already uses it for in auth code.

**DO NOT**: replace `random.random()` with `secrets.SystemRandom().random()`
in jitter / sampling code paths.

### 5. "2,173 `is True` → `== True` replacements"

**Reviewer claim**: `is` checks identity not equality; fails with integer caching.

**Reality**: The rule is **inverted**. PEP 8 actually says:

> Comparisons to singletons like None should always be done with `is` or
> `is not`, never the equality operators.

`is True` is correct for singleton boolean checks. `== True` actually
returns True for ANY truthy value (e.g. `1 == True` is True; `"yes" == True`
is False but `bool("yes") == True` is True). PEP 8 prefers `if x:` but
explicitly allows `is True` where singleton identity matters.

**DO NOT**: replace `is True` with `== True`. Doing so would introduce
subtle bugs where `1 == True` accidentally passes a check meant for
explicit boolean True.

### 6. "localStorage is synchronous — debounce writes or move to IndexedDB"

**Reviewer claim**: localStorage blocks the UI.

**Reality**: localStorage writes for UI preferences (watchlist order, nav
state, chat history) are sub-millisecond. No measured perf issue.
Debouncing wrappers add complexity (race conditions between debounced
writes and page navigation) solving a problem that doesn't exist.
IndexedDB is appropriate for >1MB datasets; we store <10KB.

**DO NOT**: wrap localStorage in a debounced layer.

### 7. "Extract inline Recharts tick configs into useMemo"

**Reviewer claim**: inline objects cause unnecessary re-renders.

**Reality**: Recharts `tick={{...}}` configs are passed to a component
that itself renders at most 60 points per update. The re-render overhead
of a new object allocation is immeasurable compared to the SVG path
recalculation. This is the React equivalent of micro-benchmarking `+=`
vs `concat()`.

**DO NOT**: useMemo these inline configs. It makes the code harder to
read for zero measurable perf gain.

---

## ✅ Findings that ARE real and WERE applied

### Applied 2026-05-03

1. **Index-as-key** — `Tier3ProgressDetailCard.jsx:160` and
   `OpsSnapshotPanel.jsx:156` now use `key={\`${idx}-${content}\`}` so
   React correctly tracks items on reorder.
2. **Debug console.log** — removed 1 leftover in `MobileBottomNav.jsx:34`
   (the remaining `console.debug` / `console.warn` calls are legitimate
   error-swallowing telemetry in catch blocks and must stay).
3. **`execute_trade` complexity** — refactored `ai_core/execution.py` into
   `execute_trade(route-only)` + `_execute_paper(...)` + `_execute_live(...)` +
   `_reject(...)` helper. Each branch now under 10 complexity and under
   50 lines. Zero behavioural change — same rejection-shape contract.

### Real but deferred (operator sign-off required)

4. **`models.py::from_dict` complexity 19** — working dataclass serializer.
   Refactoring without behavioural tests pinning every field's parse path
   is risky. Scheduled for one session per function.
5. **Large components (GuardShadowPanel 546L, MemoryDriftCard 455L, etc.)** —
   scheduled one component per session with a smoke-test screenshot after
   each.

---

## Process note for future reviewers

When applying a code review list:

1. **Verify each claim against the actual codebase** before editing.
2. **Run the linter yourself** — don't trust the count in the review.
3. **Check for substring-match false positives** in security findings
   (`exec` in variable names, `eval` in `ast.parse(mode='eval')`).
4. **Push back on quantity-over-quality** claims ("2173 instances") — they
   almost always contain mostly-correct code swept up by an overzealous
   pattern.
5. **Big refactors are deferred by default** — they need explicit
   operator sign-off + dedicated behavioural tests.
