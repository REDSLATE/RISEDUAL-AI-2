"""ML feature builders.

Authority boundary
------------------
Modules here ONLY produce structured feature dicts. They do NOT:

  * place orders
  * call the broker
  * write to ``alpha_decision_log`` / ``roadguard_*_decisions`` /
    ``phase5b_intents`` / ``paper_trades``
  * invoke the executor / pipeline / RoadGuard / FastVeto
  * branch on values to issue BUY / SELL / NO_TRADE verdicts

That separation is the whole point of the canonical pipeline:

    market data
      → feature builders (this package)
      → perception MLs
      → strategist
      → auditor
      → fast_veto
      → roadguard
      → executor

A feature builder's job is to surface signals (raw values + binary
flags). The ML layers downstream **learn how much each signal
matters**. We never hardwire ``if pe_ratio < 20: BUY`` into
execution authority — that's the failure mode the ML stack was
built to prevent.

Every public coroutine in this package MUST:

  1. Return a ``dict`` (empty on failure — never ``None``).
  2. NEVER raise — every fetch is wrapped in try/except, missing
     fields fall through.
  3. Honour lane firewalls (e.g. fundamentals returns ``{}`` for
     crypto symbols — they have no P/E).
"""
