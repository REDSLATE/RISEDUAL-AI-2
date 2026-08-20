from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .ledger import AtlasLedger
from .models import TerminalResult, TraceEvent


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    stage: str
    at_ms: float
    outcome: str = "observed"
    reason_code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReplayCase:
    case_id: str
    stack: str
    lane: str | None
    symbol: str | None
    events: tuple[ReplayEvent, ...]
    actual: Mapping[str, Any]
    expected: Mapping[str, Any]
    budgets_ms: Mapping[str, float]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReplayCase":
        events = tuple(
            ReplayEvent(
                stage=str(item["stage"]),
                at_ms=float(item["at_ms"]),
                outcome=str(item.get("outcome", "observed")),
                reason_code=item.get("reason_code"),
                details=item.get("details", {}),
            )
            for item in raw.get("events", ())
        )
        return cls(
            case_id=str(raw["case_id"]),
            stack=str(raw.get("stack", "mission_control")),
            lane=str(raw["lane"]) if raw.get("lane") is not None else None,
            symbol=str(raw["symbol"]) if raw.get("symbol") is not None else None,
            events=events,
            actual=raw.get("actual", {}),
            expected=raw.get("expected", {}),
            budgets_ms={
                str(name): float(value)
                for name, value in raw.get("budgets_ms", {}).items()
            },
        )

    @classmethod
    def load(cls, path: str | Path) -> "ReplayCase":
        with Path(path).open(encoding="utf-8") as stream:
            return cls.from_dict(json.load(stream))


@dataclass(frozen=True, slots=True)
class ReplayIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReplayResult:
    case_id: str
    passed: bool
    issues: tuple[ReplayIssue, ...]
    measurements_ms: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "issues": [
                {"code": issue.code, "message": issue.message} for issue in self.issues
            ],
            "measurements_ms": dict(self.measurements_ms),
        }


class ReplayValidator:
    """Validate recorded system decisions without simulating or submitting orders."""

    def validate(self, case: ReplayCase) -> ReplayResult:
        issues: list[ReplayIssue] = []
        measurements: dict[str, float] = {}
        self._validate_events(case, issues)
        self._validate_expected_result(case, issues)
        self._validate_intent_attempts(case, issues)
        self._validate_budgets(case, issues, measurements)
        return ReplayResult(case.case_id, not issues, tuple(issues), measurements)

    @staticmethod
    def _validate_events(case: ReplayCase, issues: list[ReplayIssue]) -> None:
        if not case.events:
            issues.append(ReplayIssue("NO_EVENTS", "Replay case has no recorded events."))
            return
        for previous, current in zip(case.events, case.events[1:], strict=False):
            if current.at_ms < previous.at_ms:
                issues.append(
                    ReplayIssue(
                        "NON_MONOTONIC_TIME",
                        f"{current.stage} at {current.at_ms}ms precedes {previous.stage} at {previous.at_ms}ms.",
                    )
                )
        required = [str(item) for item in case.expected.get("required_stage_sequence", [])]
        if required:
            actual = [event.stage for event in case.events]
            cursor = 0
            for stage in actual:
                if cursor < len(required) and stage == required[cursor]:
                    cursor += 1
            if cursor != len(required):
                issues.append(
                    ReplayIssue(
                        "STAGE_SEQUENCE_MISMATCH",
                        f"Expected ordered stages {required}; observed {actual}.",
                    )
                )

    @staticmethod
    def _validate_expected_result(case: ReplayCase, issues: list[ReplayIssue]) -> None:
        actual_terminal = case.actual.get("terminal_result")
        expected_terminal = case.expected.get("terminal_result")
        if actual_terminal is None:
            issues.append(ReplayIssue("NO_TERMINAL_RESULT", "No terminal result was recorded."))
        else:
            try:
                TerminalResult(str(actual_terminal))
            except ValueError:
                issues.append(
                    ReplayIssue(
                        "UNKNOWN_TERMINAL_RESULT",
                        f"Unsupported terminal result: {actual_terminal}",
                    )
                )
        if expected_terminal is not None and actual_terminal != expected_terminal:
            issues.append(
                ReplayIssue(
                    "TERMINAL_RESULT_MISMATCH",
                    f"Expected {expected_terminal}; observed {actual_terminal}.",
                )
            )
        if "reason_code" in case.expected:
            actual_reason = case.actual.get("reason_code")
            expected_reason = case.expected.get("reason_code")
            if actual_reason != expected_reason:
                issues.append(
                    ReplayIssue(
                        "REASON_CODE_MISMATCH",
                        f"Expected reason {expected_reason}; observed {actual_reason}.",
                    )
                )

    @staticmethod
    def _validate_intent_attempts(case: ReplayCase, issues: list[ReplayIssue]) -> None:
        attempts = list(case.actual.get("intent_attempts", []))
        accepted = [attempt for attempt in attempts if bool(attempt.get("accepted"))]
        expected_count = case.expected.get("accepted_intent_count")
        if expected_count is not None and len(accepted) != int(expected_count):
            issues.append(
                ReplayIssue(
                    "ACCEPTED_INTENT_COUNT_MISMATCH",
                    f"Expected {expected_count} accepted intent(s); observed {len(accepted)}.",
                )
            )
        accepted_per_key: dict[str, int] = {}
        for attempt in accepted:
            key = str(attempt.get("idempotency_key", ""))
            if not key:
                issues.append(
                    ReplayIssue(
                        "ACCEPTED_INTENT_WITHOUT_KEY",
                        "An accepted intent attempt has no idempotency key.",
                    )
                )
                continue
            accepted_per_key[key] = accepted_per_key.get(key, 0) + 1
        duplicates = {key: count for key, count in accepted_per_key.items() if count > 1}
        if duplicates:
            issues.append(
                ReplayIssue(
                    "DUPLICATE_ACCEPTED",
                    f"The same opportunity was accepted more than once: {duplicates}",
                )
            )

    @staticmethod
    def _validate_budgets(
        case: ReplayCase,
        issues: list[ReplayIssue],
        measurements: dict[str, float],
    ) -> None:
        if not case.events:
            return
        for name, budget in case.budgets_ms.items():
            if name == "end_to_end":
                elapsed = case.events[-1].at_ms - case.events[0].at_ms
            elif "->" in name:
                start_stage, end_stage = name.split("->", 1)
                start_index = next(
                    (index for index, event in enumerate(case.events) if event.stage == start_stage),
                    None,
                )
                if start_index is None:
                    issues.append(
                        ReplayIssue(
                            "BUDGET_STAGE_MISSING",
                            f"Budget {name} cannot be measured because {start_stage} is absent.",
                        )
                    )
                    continue
                end_event = next(
                    (
                        event
                        for event in case.events[start_index + 1 :]
                        if event.stage == end_stage
                    ),
                    None,
                )
                if end_event is None:
                    issues.append(
                        ReplayIssue(
                            "BUDGET_STAGE_MISSING",
                            f"Budget {name} cannot be measured because {end_stage} is absent after {start_stage}.",
                        )
                    )
                    continue
                elapsed = end_event.at_ms - case.events[start_index].at_ms
            else:
                issues.append(
                    ReplayIssue(
                        "INVALID_BUDGET_NAME",
                        f"Budget '{name}' must be end_to_end or stage_a->stage_b.",
                    )
                )
                continue
            measurements[name] = elapsed
            if elapsed > budget:
                issues.append(
                    ReplayIssue(
                        "TIMING_BUDGET_EXCEEDED",
                        f"{name} took {elapsed:.3f}ms; budget is {budget:.3f}ms.",
                    )
                )

    def record(self, ledger: AtlasLedger, case: ReplayCase, result: ReplayResult) -> str:
        trace_id = f"replay:{case.case_id}:{uuid.uuid4().hex[:12]}"
        base_ns = time.time_ns()
        ledger.start_trace(
            trace_id,
            stack=case.stack,
            lane=case.lane,
            symbol=case.symbol,
            correlation_id=case.case_id,
            build_sha="recorded-replay",
            started_ns=base_ns,
        )
        for event in case.events:
            ledger.append_trace_event(
                trace_id,
                TraceEvent(
                    stage=event.stage,
                    timestamp_ns=base_ns + int(event.at_ms * 1_000_000),
                    outcome=event.outcome,
                    reason_code=event.reason_code,
                    details=event.details,
                ),
            )
        terminal = case.actual.get("terminal_result", TerminalResult.ERROR.value)
        try:
            terminal = TerminalResult(str(terminal))
        except ValueError:
            terminal = TerminalResult.ERROR
        ledger.finalize_trace(
            trace_id,
            terminal,
            reason_code=case.actual.get("reason_code")
            or (result.issues[0].code if result.issues else None),
        )
        return trace_id
