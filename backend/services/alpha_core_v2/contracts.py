"""Alpha Core v2 — shared vocabulary (pure dataclasses, no I/O)."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional


class Outcome(str, Enum):
    """The ONLY three terminal states a candidate may end in."""
    TRADED = "TRADED"     # submitted AND acknowledged by the broker
    BLOCKED = "BLOCKED"   # Alpha chose not to submit (never reached broker)
    FAILED = "FAILED"     # submission attempted but errored / rejected


class Stage(str, Enum):
    FIND = "FIND"
    DECIDE = "DECIDE"
    RANK = "RANK"
    RISK = "RISK"
    ACCOUNT = "ACCOUNT"
    POSITION = "POSITION"
    QUOTE = "QUOTE"
    SIZE = "SIZE"
    ORDER = "ORDER"
    CONFIRM = "CONFIRM"
    RECONCILE = "RECONCILE"


@dataclass
class Snapshot:
    symbol: str
    mark: float
    prev_close: float = 0.0
    pct_change: float = 0.0
    rvol: float = 1.0
    ts_ns: int = field(default_factory=time.time_ns)
    usable: bool = True
    reason: str = ""


@dataclass
class Candidate:
    symbol: str
    mark: float          # DISCOVERY mark — answers "is this interesting?"
    score: float
    pattern: str
    confidence: float
    reason: str = ""
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionQuote:
    """Small, purpose-built EXECUTION quote — answers "what price am I about
    to buy at?" A different data contract from the discovery mark. Public is
    the source of truth. No freshness subsystem: exists + positive + age."""
    symbol: str
    price: Decimal
    timestamp: Optional[datetime]
    source: str

    def age_seconds(self, now: Optional[datetime] = None) -> float:
        # No parseable timestamp ⇒ freshness UNKNOWN ⇒ infinite age ⇒ caller
        # BLOCKS. Never assume a fresh quote we cannot actually date.
        if self.timestamp is None:
            return float("inf")
        now = now or datetime.now(timezone.utc)
        ts = self.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (now - ts).total_seconds())


@dataclass
class AccountState:
    equity: float
    buying_power: float
    cash: float
    ok: bool = True
    error: str = ""


@dataclass
class PositionState:
    symbol: str
    qty: float
    side: str = "long"


@dataclass
class SizePlan:
    """First-class sizing provenance — account-percentage semantics.

    The portfolio rule is: allocate ``allocation_pct`` of the currently
    available/spendable broker buying power to each new trade, bounded by
    the absolute per-trade risk cap and by affordability/reserve. Every step
    is explicit so '$229.28 spendable → 3% → $6.88 → 0.xxxx shares' is never
    a mystery. Existing positions constrain Alpha ONLY through remaining
    buying power — never through a position count."""
    spendable_balance: float        # broker-authoritative available buying power
    allocation_pct: float           # fraction of spendable per new trade (e.g. 0.03)
    allocation_notional: float      # spendable_balance * allocation_pct
    risk_cap_notional: float        # absolute per-trade ceiling (existing risk cap)
    final_notional: float           # actual $ committed by the sized order
    execution_price: float          # broker execution-time mark used to size
    quantity: float                 # fractional shares
    remaining_buying_power: float   # buying_power - final_notional (post-trade)
    resize_reason: Optional[str] = None  # what bound below the 3% allocation
    resized: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrderResult:
    ok: bool
    order_id: Optional[str] = None
    status: str = ""
    filled_qty: float = 0.0
    fill_price: float = 0.0
    requested_qty: float = 0.0
    error: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Receipt:
    """Compact, structured record of a single candidate's full lifecycle."""
    receipt_id: str
    cycle_id: str
    symbol: str
    created_ns: int
    outcome: Outcome
    stage_reached: Stage
    reason: str = ""
    action: str = "open"                   # open | close
    # DECIDE
    pattern: str = ""
    score: float = 0.0
    confidence: float = 0.0
    mark: float = 0.0                       # discovery mark
    # ACCOUNT (broker truth)
    equity: float = 0.0
    buying_power: float = 0.0
    # POSITION (broker truth)
    broker_held: Optional[bool] = None
    broker_qty: float = 0.0
    reconciled_phantom: bool = False
    # QUOTE (broker truth, execution-time)
    execution_price: float = 0.0
    execution_quote_source: str = ""
    execution_quote_age_s: float = 0.0
    # SIZE (provenance)
    sizing: dict = field(default_factory=dict)
    # ORDER (broker fact #1 — the order was acknowledged / filled)
    order_id: Optional[str] = None
    order_status: str = ""
    requested_qty: float = 0.0
    order_acknowledged: bool = False
    broker_reported_fill_qty: float = 0.0
    fill_price: float = 0.0
    # RECONCILE (broker fact #2 — the resulting account position, verified later)
    position_reconciled: bool = False
    reconciled_position_qty: float = 0.0
    position_status: str = ""  # open | pending | reconciled_flat

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        d["stage_reached"] = self.stage_reached.value
        return d


@dataclass
class CycleResult:
    cycle_id: str
    candidates_in: int
    traded: int
    blocked: int
    failed: int
    receipts: list = field(default_factory=list)

    @property
    def balanced(self) -> bool:
        """The core accounting invariant — nothing vanishes."""
        return self.candidates_in == (self.traded + self.blocked + self.failed)

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "candidates_in": self.candidates_in,
            "traded": self.traded,
            "blocked": self.blocked,
            "failed": self.failed,
            "balanced": self.balanced,
            "receipts": [r.to_dict() for r in self.receipts],
        }


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
