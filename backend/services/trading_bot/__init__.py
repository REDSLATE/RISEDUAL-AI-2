"""Trading-bot service package.

Strangler-split target: ``services/trading_bot_service.py`` (1575
lines, core-governance). Splitting in micro-phases per Step 4 plan:

  * 4A — pure models/types/constants  (this commit)
  * 4B — telemetry/receipts            (later)
  * 4C — data access                   (later)
  * 4D — pure decision rules           (later)
  * 4E — broker calls (LAST)           (later)

``services.trading_bot_service`` remains the public API and orchestrator
shim throughout. External callers import from there, not from this
package directly.
"""
