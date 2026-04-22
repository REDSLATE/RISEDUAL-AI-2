"""OCC-standard option-symbol builder + parser.

The OCC 21-character format is:

    RRRRRRYYMMDDTSSSSSSSS

    R × 6   — underlying root, right-padded with spaces to 6 chars
    YYMMDD  — expiration date (2-digit year)
    T       — 'C' (call) or 'P' (put)
    S × 8   — strike × 1000, zero-padded to 8 digits

Examples:
    AAPL  261218C00200000   → AAPL $200 call expiring 2026-12-18
    SPY   260116P00400000   → SPY  $400 put  expiring 2026-01-16
    GOOGL 261218C02500000   → GOOGL $2500 call …

This file is critical correctness — a mis-built symbol is rejected
by every broker (not just Alpaca) and there's no graceful degrade.
Hence the dedicated test file and the deliberate no-scipy, no-third-
party-dep implementation.

Rules worth calling out:

* Roots ≤ 6 chars. Longer tickers (e.g. `BRK.B`) are vanishingly rare
  in US equity options; a sanity check raises `ValueError` if anyone
  passes one so we fail fast instead of producing a silently-rejected
  order.
* Strikes are encoded with a x1000 multiplier. A $200 strike becomes
  `00200000`. A $0.50 strike → `00000500`. Max encodable strike is
  $99,999.999 — plenty of headroom for everything actually listed.
* Times are UTC-naive in the OCC format (there's no tz slot). Caller
  provides an ISO-date string; we convert, never the other way.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import TypedDict


OCC_LENGTH = 21
ROOT_LENGTH = 6
STRIKE_DIGITS = 8
STRIKE_MULTIPLIER = 1000


class OCCParsed(TypedDict):
    underlying: str
    expiration: str       # ISO YYYY-MM-DD
    option_type: str      # "call" | "put"
    strike: float
    occ_symbol: str


def build_occ_symbol(
    underlying: str,
    expiration: str | date | datetime,
    option_type: str,
    strike: float,
) -> str:
    """Build an OCC 21-char symbol. Raises `ValueError` on malformed
    input — callers rely on this for early rejection before the
    order ever hits a broker."""
    if not underlying or not isinstance(underlying, str):
        raise ValueError("underlying must be a non-empty string")

    root = underlying.upper().strip()
    if len(root) > ROOT_LENGTH:
        raise ValueError(
            f"underlying '{root}' is {len(root)} chars; OCC format "
            f"allows up to {ROOT_LENGTH}"
        )
    root = root.ljust(ROOT_LENGTH)

    # Expiration → YYMMDD.
    if isinstance(expiration, str):
        expiration = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
    if isinstance(expiration, datetime):
        expiration = expiration.date()
    date_str = expiration.strftime("%y%m%d")

    ot = option_type.lower().strip()
    if ot not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got '{option_type}'")
    type_char = "C" if ot == "call" else "P"

    strike_val = float(strike)
    if strike_val <= 0:
        raise ValueError(f"strike must be > 0, got {strike_val}")
    strike_int = round(strike_val * STRIKE_MULTIPLIER)
    if strike_int >= 10 ** STRIKE_DIGITS:
        raise ValueError(
            f"strike {strike_val} exceeds OCC-encodable max "
            f"(${10 ** STRIKE_DIGITS / STRIKE_MULTIPLIER - 0.001:.3f})"
        )
    strike_str = str(strike_int).zfill(STRIKE_DIGITS)

    symbol = f"{root}{date_str}{type_char}{strike_str}"
    assert len(symbol) == OCC_LENGTH, (
        f"internal OCC builder bug: produced {len(symbol)}-char symbol"
    )
    return symbol


def parse_occ_symbol(symbol: str) -> OCCParsed:
    """Inverse of `build_occ_symbol`. Raises `ValueError` on any
    structural malformation. Useful for turning broker-reported
    position symbols back into the UI-friendly tuple."""
    if not symbol or len(symbol) != OCC_LENGTH:
        raise ValueError(
            f"OCC symbol must be {OCC_LENGTH} chars, got {len(symbol) if symbol else 0}"
        )

    root = symbol[:ROOT_LENGTH].rstrip()
    date_str = symbol[ROOT_LENGTH:ROOT_LENGTH + 6]
    type_char = symbol[ROOT_LENGTH + 6]
    strike_str = symbol[ROOT_LENGTH + 7:]

    try:
        exp_dt = datetime.strptime(date_str, "%y%m%d").date()
    except ValueError as e:
        raise ValueError(f"invalid OCC date segment '{date_str}'") from e

    if type_char not in ("C", "P"):
        raise ValueError(f"invalid OCC type char '{type_char}' (expected C|P)")

    if not strike_str.isdigit() or len(strike_str) != STRIKE_DIGITS:
        raise ValueError(f"invalid OCC strike segment '{strike_str}'")

    return OCCParsed(
        underlying=root,
        expiration=exp_dt.isoformat(),
        option_type="call" if type_char == "C" else "put",
        strike=int(strike_str) / STRIKE_MULTIPLIER,
        occ_symbol=symbol,
    )
