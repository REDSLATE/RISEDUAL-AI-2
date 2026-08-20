from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from .ledger import AtlasLedger
from .models import ClaimRequest, ClaimResult, IntentFingerprint


class IntentIdentityError(ValueError):
    pass


def _first(payload: Mapping[str, Any], *names: str, required: bool = True) -> Any:
    for name in names:
        value = payload.get(name)
        if value is not None and value != "":
            return value
    if required:
        raise IntentIdentityError(f"missing required field; expected one of: {', '.join(names)}")
    return None


def fingerprint_from_intent(payload: Mapping[str, Any]) -> IntentFingerprint:
    """Map common RISEDUAL field aliases to the durable identity contract."""

    opportunity_id = _first(
        payload, "opportunity_id", "setup_id", "signal_event_id", required=False
    )
    if opportunity_id is None:
        raise IntentIdentityError(
            "intent needs a stable opportunity_id, setup_id, or signal_event_id; "
            "a timestamp-only identity would allow the same setup to trade again"
        )
    return IntentFingerprint(
        stack=str(_first(payload, "stack", "runtime_stack")),
        lane=str(_first(payload, "lane", "asset_lane", "asset_class")),
        broker=str(_first(payload, "broker", "broker_name")),
        broker_account_ref=str(
            _first(payload, "broker_account_ref", "account_ref", "account_id")
        ),
        symbol=str(_first(payload, "symbol", "ticker", "pair")),
        side=str(_first(payload, "side", "action")),
        strategy=str(_first(payload, "strategy", "brain", "signal_source")),
        opportunity_id=str(opportunity_id),
    )


def claim_intent_mapping(ledger: AtlasLedger, payload: Mapping[str, Any]) -> ClaimResult:
    fingerprint = fingerprint_from_intent(payload)
    request = ClaimRequest(
        intent_id=str(_first(payload, "intent_id", "id")),
        fingerprint=fingerprint,
        client_order_id=_first(payload, "client_order_id", required=False),
        payload=payload,
        supplied_idempotency_key=_first(payload, "idempotency_key", required=False),
    )
    return ledger.claim_intent(request)


@dataclass(frozen=True, slots=True)
class ContractFinding:
    code: str
    severity: str
    message: str


def audit_runtime_contract(
    observation: Mapping[str, Any],
    *,
    now: datetime | None = None,
    heartbeat_max_age_seconds: int = 60,
) -> list[ContractFinding]:
    """Return diagnostics for known MC invariants without blocking execution."""

    findings: list[ContractFinding] = []
    stack = str(observation.get("stack", "")).lower()
    lane = str(observation.get("lane", "")).lower()
    broker = str(observation.get("broker", "")).lower()
    mode = str(observation.get("mode", "")).lower()

    if stack and stack != "mission_control":
        findings.append(
            ContractFinding(
                "SUBMIT_OWNER_MISMATCH",
                "critical",
                "Mission Control is the sole broker-submit owner.",
            )
        )
    if mode and mode not in {"live", "auto_execute", "toehold"}:
        findings.append(
            ContractFinding(
                "NON_LIVE_MODE",
                "critical",
                f"Observed execution mode '{mode}' conflicts with the live-only runtime.",
            )
        )
    expected_broker = {"equity": "webull", "crypto": "kraken"}.get(lane)
    if expected_broker and broker and broker != expected_broker:
        findings.append(
            ContractFinding(
                "BROKER_LANE_MISMATCH",
                "warning",
                f"{lane} is expected on {expected_broker}; observed {broker}.",
            )
        )

    heartbeat = observation.get("heartbeat_at")
    if heartbeat:
        now = now or datetime.now(UTC)
        try:
            if isinstance(heartbeat, (int, float)):
                heartbeat_at = datetime.fromtimestamp(float(heartbeat), UTC)
            else:
                heartbeat_at = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
                if heartbeat_at.tzinfo is None:
                    heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
            age = (now - heartbeat_at).total_seconds()
            if age > heartbeat_max_age_seconds:
                findings.append(
                    ContractFinding(
                        "STALE_HEARTBEAT",
                        "critical",
                        f"Engine heartbeat is {age:.1f}s old; expected <= {heartbeat_max_age_seconds}s.",
                    )
                )
        except (TypeError, ValueError, OverflowError):
            findings.append(
                ContractFinding(
                    "INVALID_HEARTBEAT",
                    "warning",
                    "heartbeat_at could not be parsed.",
                )
            )
    return findings
