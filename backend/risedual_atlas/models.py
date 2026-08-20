from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class IntentStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    TERMINAL = "terminal"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_INTENT_STATUSES = frozenset(
    {IntentStatus.TERMINAL, IntentStatus.REJECTED, IntentStatus.EXPIRED}
)


class TerminalResult(StrEnum):
    NO_SETUP = "NO_SETUP"
    BRAIN_HOLD = "BRAIN_HOLD"
    INTENT_CREATED = "INTENT_CREATED"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    GATE_BLOCKED = "GATE_BLOCKED"
    BROKER_SUBMITTED = "BROKER_SUBMITTED"
    BROKER_REJECTED = "BROKER_REJECTED"
    BROKER_ACKNOWLEDGED = "BROKER_ACKNOWLEDGED"
    ERROR = "ERROR"


class Stage(StrEnum):
    MARKET_EVENT = "market_event"
    SCANNER_DECISION = "scanner_decision"
    BRAIN_DECISION = "brain_decision"
    INTENT_CREATED = "intent_created"
    GATE_DECISION = "gate_decision"
    BROKER_SUBMIT = "broker_submit"
    BROKER_ACK = "broker_ack"
    OUTCOME = "outcome"


_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9_.:@/+-]+")


def _clean_token(value: str, *, upper: bool = False, max_length: int = 160) -> str:
    cleaned = _SAFE_TOKEN.sub("_", str(value).strip())[:max_length]
    if not cleaned:
        raise ValueError("identity values must not be empty")
    return cleaned.upper() if upper else cleaned.lower()


def _opaque_token(value: str, *, max_length: int = 240) -> str:
    cleaned = _SAFE_TOKEN.sub("_", str(value).strip())[:max_length]
    if not cleaned:
        raise ValueError("identity values must not be empty")
    return cleaned


@dataclass(frozen=True, slots=True)
class IntentFingerprint:
    """Stable identity for one trade opportunity in one broker account.

    ``opportunity_id`` must remain stable while scanners re-observe the same setup.
    A genuinely new setup must receive a new value. This is what prevents repeated
    cycles from creating repeated orders without blocking later, independent setups.
    """

    stack: str
    lane: str
    broker: str
    broker_account_ref: str
    symbol: str
    side: str
    strategy: str
    opportunity_id: str
    version: int = 1

    def canonical(self) -> dict[str, str | int]:
        lane = _clean_token(self.lane)
        if lane not in {"equity", "crypto", "options"}:
            raise ValueError(f"unsupported lane: {lane}")
        side = _clean_token(self.side, upper=True)
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported side: {side}")
        if self.version != 1:
            raise ValueError(f"unsupported fingerprint version: {self.version}")
        return {
            "version": self.version,
            "stack": _clean_token(self.stack),
            "lane": lane,
            "broker": _clean_token(self.broker),
            "broker_account_ref": _clean_token(self.broker_account_ref),
            "symbol": _clean_token(self.symbol, upper=True),
            "side": side,
            "strategy": _clean_token(self.strategy),
            "opportunity_id": _clean_token(self.opportunity_id, max_length=240),
        }

    def idempotency_key(self) -> str:
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]
        return f"rda:v{self.version}:{digest}"


@dataclass(frozen=True, slots=True)
class ClaimRequest:
    intent_id: str
    fingerprint: IntentFingerprint
    client_order_id: str | None = None
    payload: Mapping[str, Any] | None = None
    supplied_idempotency_key: str | None = None

    def normalized_intent_id(self) -> str:
        return _opaque_token(self.intent_id)

    def idempotency_key(self) -> str:
        return self.fingerprint.idempotency_key()

    def source_idempotency_key(self) -> str | None:
        if not self.supplied_idempotency_key:
            return None
        return _opaque_token(str(self.supplied_idempotency_key))

    def payload_sha256(self) -> str:
        if self.payload is None:
            return ""
        encoded = json.dumps(
            self.payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ClaimResult:
    accepted: bool
    intent_id: str
    idempotency_key: str
    status: IntentStatus
    duplicate_by: str | None = None
    existing_intent_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntentTransitionResult:
    intent_id: str
    previous_status: IntentStatus
    status: IntentStatus
    changed: bool


@dataclass(frozen=True, slots=True)
class TraceEvent:
    stage: str
    timestamp_ns: int
    outcome: str = "observed"
    reason_code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
