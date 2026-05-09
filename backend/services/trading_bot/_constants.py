"""Pure constants for the trading-bot service.

Step 4A extraction (2026-05-09). No behaviour changes — every value
is byte-equivalent to what previously lived inline in
``services/trading_bot_service.py``. The shim re-exports these names
at the original module path so:

  * ``trading_bot_service.MAX_PORTFOLIO_EXPOSURE`` etc. remain
    accessible to callers.
  * ``monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", True)``
    in tests still mutates the binding the live code reads from
    (bare-name lookups inside ``trading_bot_service`` resolve via
    that module's globals, which the ``from ... import`` shim
    populates).
"""
import os


# Adaptive-sizing feature flag. Signal-bot qty gets scaled by the
# Tier 3 readiness snapshot (readiness multiplier × confidence
# multiplier) when enabled. Defaults OFF so rollout is a one-line
# env change; bots run at their configured ``qty`` otherwise.
ADAPTIVE_SIZING_ENABLED: bool = (
    os.getenv("RISEDUAL_ADAPTIVE_SIZING", "0") == "1"
)

# Hard floor on the scaled qty so a 0.05× multiplier on a ``qty=1``
# config doesn't round down to zero (which silently blocks trades).
_MIN_SCALED_QTY: float = 0.01

# Hard cap per trade for the USD-notional execution path.
# Independent of the per-bot ``qty`` config — protects against a
# misconfigured base_size or a runaway readiness multiplier ever
# sending more than $2000 of notional at a single bot.
MAX_POSITION_USD: float = 2000.0

# Portfolio-level risk caps. These gate the USD-notional
# ``execute_signal`` path — a single bot can fire up to MAX_POSITION_USD
# per trade, but across all open positions the combined notional is
# capped at MAX_PORTFOLIO_EXPOSURE and the count at
# MAX_CONCURRENT_TRADES. When either limit is hit we refuse NEW
# trades; existing positions are untouched.
MAX_PORTFOLIO_EXPOSURE: float = 3000.0
MAX_CONCURRENT_TRADES: int = 5

# Sector concentration cap. Prevents the classic "stack 3 tech
# longs at the top" failure mode — if a signal's sector already
# represents more than this share of total exposure, refuse NEW
# trades in that sector. Compared case-insensitively; signals
# without a sector tag bypass the check.
MAX_SECTOR_EXPOSURE_PCT: float = 0.50

# Permitted bot type strings. Used by ``create_bot`` validation.
BOT_TYPES = {"grid", "signal", "webhook"}
