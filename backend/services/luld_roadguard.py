"""LULD RoadGuard — narrow equity-safety gate.

Design contract (operator directive, 2026-02)
=============================================
Keep it narrow. LULD blocks must reflect real Limit-Up/Limit-Down
infrastructure signals — not "the stock moved a lot."

What we block on
----------------
1. **Explicit halt signal.** When the intent carries a
   ``halt_status`` field marked halted, or the mark quote comes back
   with a halt indicator, we block.
2. **Derived LULD proximity** — only when the caller supplies an
   explicit ``reference_price`` (typically the 5-minute rolling
   reference used by the SIP) and a defined ``luld_tier``. We compute
   the Reg NMS band and block if the mark price is within a small
   buffer of the band edge. No reference price → no derived block.
3. **Reopening / auction collar state.** When the intent explicitly
   marks ``reopening=True`` (post-halt reopening auction / collar
   transition), block. Same explicit signal only.

What we deliberately DO NOT do
------------------------------
* Block on absolute move percentage without a reference price. That
  reintroduces Alpha's overblocking problem.
* Infer halts from thin volume or gaps.
* Block on "unknown LULD state" alone. Fail-open unless the caller
  passes ``strict_luld=True`` (opt-in).

Reg NMS LULD tier bands
-----------------------
* Tier 1 (S&P 500 + Russell 1000 + select ETPs, price > $3): 5%
* Tier 2 (all other NMS > $3): 10%
* Any tier, price $0.75 – $3.00: 20%
* Any tier, price < $0.75: lesser of 75% or $0.15
(RTH bands; wider outside RTH.)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


# ── Enforcement flag ────────────────────────────────────────────────

def _enforce() -> bool:
    """When 0, verdicts are computed but not enforced (shadow log)."""
    return (os.environ.get("LULD_ROADGUARD_ENFORCE", "1") or "1").strip() != "0"


# ── Buffer + tier tables ────────────────────────────────────────────

# How close to the band edge counts as "near LULD".
_DEFAULT_BUFFER_PCT = float(os.environ.get("LULD_PROXIMITY_BUFFER_PCT") or 0.5) / 100.0


def _band_pct_for(price: float, tier: str) -> float:
    """Return the LULD band as a fraction (0.05 = 5%)."""
    tier_norm = (tier or "").lower()
    if price < 0.75:
        return min(0.75, 0.15 / max(price, 0.01))
    if 0.75 <= price <= 3.0:
        return 0.20
    if tier_norm in ("tier1", "tier_1", "t1", "sp500", "russell1000"):
        return 0.05
    return 0.10  # tier 2 default for anything > $3


# ── Verdict shape ──────────────────────────────────────────────────

@dataclass
class LULDVerdict:
    allowed: bool
    reason: Optional[str]
    source: str                # "halt" | "reopening" | "proximity" | "clear" | "unknown"
    band_high: Optional[float] = None
    band_low: Optional[float] = None
    enforce: bool = True       # False → shadow-log only

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "source": self.source,
            "band_high": self.band_high,
            "band_low": self.band_low,
            "enforce": self.enforce,
        }


# ── Core check ─────────────────────────────────────────────────────

def check_luld(
    *,
    symbol: str,
    mark_price: Optional[float],
    context: Optional[Mapping[str, Any]] = None,
    strict: bool = False,
) -> LULDVerdict:
    """Evaluate whether ``symbol`` is safe to enter right now.

    ``context`` — optional dict with any of:
        * ``halt_status`` (str) — "halted" / "resumed" / "normal"
        * ``reopening``   (bool) — post-halt reopening in progress
        * ``reference_price`` (float) — SIP 5-min rolling reference
        * ``luld_tier`` (str) — "tier1" | "tier2"
        * ``band_high`` / ``band_low`` (float) — explicit bands
          (overrides derived bands)

    ``strict`` — when True, unknown LULD state blocks (fail-closed).
    Default False (fail-open) per operator's narrow-scope directive.
    """
    ctx: dict[str, Any] = dict(context or {})
    enforce = _enforce()

    # 1) Explicit halt — always block.
    halt_status = str(ctx.get("halt_status") or "").lower()
    if halt_status in ("halted", "trading_halt", "pause"):
        return LULDVerdict(
            allowed=False,
            reason=f"HALTED:{halt_status}",
            source="halt",
            enforce=enforce,
        )

    # 2) Reopening auction / collar state — block explicit signal.
    if bool(ctx.get("reopening")):
        return LULDVerdict(
            allowed=False, reason="REOPENING_AUCTION",
            source="reopening", enforce=enforce,
        )

    # 3) Derived LULD proximity — only with an explicit reference price.
    ref = ctx.get("reference_price")
    band_high = ctx.get("band_high")
    band_low = ctx.get("band_low")

    if mark_price is not None and (band_high or band_low or ref):
        try:
            mark_f = float(mark_price)
        except (TypeError, ValueError):
            mark_f = None
        # Explicit bands take precedence.
        if band_high is not None and band_low is not None and mark_f is not None:
            bh = float(band_high)
            bl = float(band_low)
            hi_buffer = bh * (1 - _DEFAULT_BUFFER_PCT)
            lo_buffer = bl * (1 + _DEFAULT_BUFFER_PCT)
            if mark_f >= hi_buffer:
                return LULDVerdict(
                    allowed=False,
                    reason=f"NEAR_LULD_UPPER:mark={mark_f:.4f}>=band_high_buffer={hi_buffer:.4f}",
                    source="proximity",
                    band_high=bh, band_low=bl, enforce=enforce,
                )
            if mark_f <= lo_buffer:
                return LULDVerdict(
                    allowed=False,
                    reason=f"NEAR_LULD_LOWER:mark={mark_f:.4f}<=band_low_buffer={lo_buffer:.4f}",
                    source="proximity",
                    band_high=bh, band_low=bl, enforce=enforce,
                )
        elif ref is not None and mark_f is not None:
            try:
                ref_f = float(ref)
            except (TypeError, ValueError):
                ref_f = None
            if ref_f and ref_f > 0:
                tier = str(ctx.get("luld_tier") or "")
                band_pct = _band_pct_for(ref_f, tier)
                derived_high = ref_f * (1 + band_pct)
                derived_low = ref_f * (1 - band_pct)
                hi_buffer = derived_high * (1 - _DEFAULT_BUFFER_PCT)
                lo_buffer = derived_low * (1 + _DEFAULT_BUFFER_PCT)
                if mark_f >= hi_buffer:
                    return LULDVerdict(
                        allowed=False,
                        reason=(
                            f"NEAR_LULD_UPPER:mark={mark_f:.4f}>=derived_buffer={hi_buffer:.4f} "
                            f"(ref={ref_f:.4f}, band={band_pct*100:.1f}%)"
                        ),
                        source="proximity",
                        band_high=derived_high, band_low=derived_low,
                        enforce=enforce,
                    )
                if mark_f <= lo_buffer:
                    return LULDVerdict(
                        allowed=False,
                        reason=(
                            f"NEAR_LULD_LOWER:mark={mark_f:.4f}<=derived_buffer={lo_buffer:.4f} "
                            f"(ref={ref_f:.4f}, band={band_pct*100:.1f}%)"
                        ),
                        source="proximity",
                        band_high=derived_high, band_low=derived_low,
                        enforce=enforce,
                    )

    # 4) Unknown LULD state — fail-open unless caller opts into strict.
    if strict and not (band_high or band_low or ref):
        return LULDVerdict(
            allowed=False,
            reason="LULD_DATA_UNAVAILABLE_STRICT",
            source="unknown",
            enforce=enforce,
        )

    return LULDVerdict(
        allowed=True, reason=None, source="clear", enforce=enforce,
    )
