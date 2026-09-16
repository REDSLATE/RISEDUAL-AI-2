"""Alpha Core v2 — a lean, broker-authoritative execution engine.

Milestone 1 objective (operator, 2026-09):
    Alpha Core v2 must independently discover a valid opportunity, decide
    whether to trade it, dynamically size it from the actual Public account,
    submit it fractionally, confirm the broker result, reconcile it, and
    account for every candidate exactly once — with no dependency on
    Legacy's runtime state.

Design rules
------------
* Flag-gated OFF (``ALPHA_CORE_V2=0`` default). Never alters Legacy's tick
  loop, ledger, env vars, or execution behavior.
* Broker = truth about money, positions, orders, fills.
  Alpha (SQLite receipts) = truth about why/what happened/what was learned.
  SQLite NEVER determines whether a live position exists — Public does.
* Every candidate ends in EXACTLY one terminal outcome: TRADED | BLOCKED |
  FAILED. RESIZED is an execution transformation recorded on the receipt,
  never a terminal state. Invariant: candidates_in == traded+blocked+failed.
* No feature is added unless it helps FIND, DECIDE, SIZE, EXECUTE, PROTECT,
  EXIT, or LEARN from a trade.
"""
from services.alpha_core_v2.contracts import Outcome, Stage  # noqa: F401
