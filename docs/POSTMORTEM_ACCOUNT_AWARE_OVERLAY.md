# Post-Mortem — Account-Aware Decision Layer (Alpha Overlay)

**Date of incident:** Feb 2026
**Severity:** SEV-1 — full production trading halt
**Status:** Resolved by removing the overlay entirely
**Author:** RISEDUAL engineering
**Audience:** Any future agent or engineer tempted to build a "decision layer" / "safety overlay" / "risk gate" over Alpha.

> **TL;DR — Read the [Rules for Future Gates](#rules-for-future-decision-gates) and the [Pre-Flight Checklist](#pre-flight-checklist-must-tick-before-shipping-any-decision-gate) before you write a single line of gate code. The overlay was well-intentioned, silently self-promoted from SHADOW to HARD_GATE on a timer, and halted live trading for days. Do not rebuild this pattern.**

---

## 1. What happened

We shipped an "Account-Aware Decision Layer" over Alpha Day Trader. Its job was to consult the connected Public.com account state (existing positions, buying power, concentration) before letting an intent reach the executor. It shipped in `SHADOW` mode — observe & log only, never block.

The overlay contained a **time-based auto-promotion**: after 7 days in shadow with "clean" telemetry, it silently transitioned to `HARD_GATE` mode and began blocking intents whose `account_context = EXISTING_POSITION`.

The user's Public.com account already held long positions in the exact symbols Alpha wanted to scale into. Every subsequent intent was blocked. Alpha appeared "broken." Live trading stopped for multiple days on production before the cause was identified.

## 2. Impact

- **Production**: Zero live equity fills for multiple sessions across `algo-trader-ai-1.emergent.host`.
- **User experience**: User assumed Alpha was broken. Investigation window burned on chasing regime / broker / connectivity red herrings.
- **Trust**: Silent auto-enforcement made the system feel non-deterministic.

## 3. Root cause

Three compounding design defects:

1. **Silent time-based promotion.** `SHADOW → HARD_GATE` happened on a `days_in_shadow >= 7` counter with no operator confirmation, no email, no admin banner, no dashboard flag. The gate flipped itself on.
2. **`EXISTING_POSITION` was treated as a BLOCK.** Scaling into an existing name is a normal, desired Alpha behavior. Modeling "I already own this" as a risk-block was the wrong domain assumption.
3. **No global kill-switch.** Once the gate promoted itself, there was no single env var / feature flag to force it back to shadow without a code deploy. Emergency endpoints had to be shipped mid-outage.

## 4. Resolution

The overlay was **removed in its entirety** — service, endpoints, DB collections, UI badges, tests. `git log` will show a single deletion commit. 57/57 pytest suite green after removal. Live trading resumed on the next deploy.

Do **not** attempt to resurrect the same files. If a similar capability is ever needed, it must be built as a **new** module that obeys every rule below.

---

## 5. Rules for Future Decision Gates

Any component that can prevent Alpha (or any bot) from firing an intent must satisfy **all** of the following. These are non-negotiable.

1. **No self-promotion.** A gate never changes its own enforcement level. Shadow → Advisory → Enforce transitions require an **explicit operator toggle** (admin endpoint + UI confirmation). No timers, no auto-thresholds, no "if telemetry looks good for N days."
2. **Ship behind a global kill-switch env var, default OFF.** Every new gate needs a `RISEDUAL_<GATE_NAME>_ENABLED` env var. Missing / `0` / `false` → gate is a no-op that logs and returns PASS. This must be checked as the very first line of the gate function.
3. **`EXISTING_POSITION` is never a BLOCK.** In this codebase, holding the symbol is a scale-in signal, not a risk signal. Model it as `REDUCE_SIZE`, `PASS`, or `SCALE`. If a gate ever returns BLOCK for `EXISTING_POSITION`, it is broken by definition.
4. **Every block decision is operator-visible and overridable.** A block must (a) write a structured skip log entry with a human-readable reason, (b) surface in an admin endpoint / UI card within one deploy cycle, and (c) expose a one-click "override this decision" admin action for the current session.
5. **Shadow → Enforce is a human decision, not a calendar.** Shadow-mode metrics must be reviewed by the operator before any enforcement is turned on. The system may *recommend* promotion ("N days, X% false-positive rate — consider enforcing"), but the flip is manual.
6. **No hidden state.** Gate current mode, last transition timestamp, last transition actor, and last N decisions must be queryable via a single admin endpoint. If it isn't in the admin API, it doesn't exist.
7. **Reversible without a code deploy.** Toggling any gate off (globally or per-symbol) must be doable via env var flip or admin endpoint, not by shipping code. This session's outage required a code deploy to escape.

---

## 6. Pre-Flight Checklist (must tick before shipping any decision gate)

Copy this into the PR description. Every box must be checked and reviewed. **Do not merge otherwise.**

- [ ] **Kill-switch env var exists** (`RISEDUAL_<NAME>_ENABLED`), defaults to OFF, and is the first line of the gate function.
- [ ] **No timer / counter / auto-promotion code path exists** anywhere in the module. Search the diff for `days_`, `hours_since`, `auto_promote`, `if elapsed`, `>= threshold_days`. If any match relates to enforcement level, remove it.
- [ ] **Enforcement level is only mutable via an admin endpoint** that requires the admin role and logs the actor + timestamp.
- [ ] **`EXISTING_POSITION` path returns PASS/SCALE/REDUCE**, never BLOCK. Unit test explicitly asserts this.
- [ ] **Every BLOCK return** writes to a skip-log collection with `{gate_name, symbol, reason, intent, timestamp, override_url}`.
- [ ] **Admin endpoint `GET /api/admin/<gate>/state`** returns `{mode, enabled, last_transition_at, last_transition_by, recent_decisions[]}`.
- [ ] **Admin endpoint `POST /api/admin/<gate>/override`** exists and can force-PASS the next N intents for a symbol.
- [ ] **Admin endpoint `POST /api/admin/<gate>/mode`** exists to flip mode with body `{mode: "shadow"|"advisory"|"enforce"}`, requires admin role, logs actor.
- [ ] **UI surfaces the mode** — an always-visible badge on `AlphaDayTraderPanel` (or equivalent) showing current mode + last transition. If the gate is enforcing, this badge is amber/red, not tucked in a sub-tab.
- [ ] **Pytest suite includes**: (a) kill-switch off → PASS, (b) EXISTING_POSITION → not BLOCK, (c) mode flip is admin-only, (d) override endpoint works, (e) no auto-promotion (assert `mode` unchanged after simulated 30-day elapse).
- [ ] **Rollback plan documented in the PR**: exact env var to flip and endpoint to hit if the gate misbehaves in prod. Do not merge without it.
- [ ] **The post-mortem doc (`/app/docs/POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md`) is linked in the PR description.** Reviewer must confirm this checklist was read.

---

## 7. What made this hard to detect

- The gate did exactly what it was designed to do — the *design* was wrong, not the code.
- Symptom (Alpha not trading) had multiple plausible causes (choppy regime, broker creds, market hours, cooldowns). Overlay was investigated last.
- Because it shipped in SHADOW, the initial reviewer signed it off as "safe — it can't block anything." The 7-day promotion clause was buried in a helper and not called out.

**Takeaway for reviewers:** if a PR mentions "shadow mode," search the diff for any promotion/transition logic. Ask: *what turns this on?* If the answer is anything other than "an operator clicking a button," reject the PR.

---

## 8. References

- Removal commit: see `git log` around Feb 2026 (overlay files deletion)
- Related design doc: `/app/memory/POSITION_CONTEXT_DESIGN.md` (pre-dates this incident — treat as historical, not spec)
- PRD entry: `/app/memory/PRD.md` — Latest Update, Feb 2026, "Account-Aware Overlay removed"
- Changelog entry: `/app/memory/CHANGELOG.md` — Feb 2026 section
