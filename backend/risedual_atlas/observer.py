from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from .ledger import AtlasLedger
from .models import TerminalResult, TraceEvent


@dataclass(slots=True)
class CycleTrace:
    """Small helper that guarantees one compact terminal result per decision cycle."""

    ledger: AtlasLedger
    stack: str
    lane: str | None = None
    symbol: str | None = None
    correlation_id: str | None = None
    build_sha: str | None = None
    trace_id: str = field(default_factory=lambda: f"cycle:{uuid.uuid4().hex}")
    _started: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)

    def __enter__(self) -> "CycleTrace":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        if not self._finished:
            reason = "UNHANDLED_EXCEPTION" if exc is not None else "UNFINALIZED_CYCLE"
            self.finish(TerminalResult.ERROR, reason_code=reason)
        return False

    def start(self) -> None:
        if self._started:
            return
        self.ledger.start_trace(
            self.trace_id,
            stack=self.stack,
            lane=self.lane,
            symbol=self.symbol,
            correlation_id=self.correlation_id,
            build_sha=self.build_sha,
            started_ns=time.time_ns(),
        )
        self._started = True

    def mark(
        self,
        stage: str,
        *,
        outcome: str = "observed",
        reason_code: str | None = None,
        details: Mapping[str, Any] | None = None,
        timestamp_ns: int | None = None,
    ) -> int:
        if not self._started:
            self.start()
        if self._finished:
            raise RuntimeError("cannot append to a finalized cycle")
        return self.ledger.append_trace_event(
            self.trace_id,
            TraceEvent(
                stage=stage,
                timestamp_ns=timestamp_ns if timestamp_ns is not None else time.time_ns(),
                outcome=outcome,
                reason_code=reason_code,
                details=details or {},
            ),
        )

    def finish(
        self,
        terminal_result: TerminalResult | str,
        *,
        reason_code: str | None = None,
        intent_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._started:
            self.start()
        result = self.ledger.finalize_trace(
            self.trace_id,
            terminal_result,
            reason_code=reason_code,
            intent_id=intent_id,
        )
        self._finished = True
        return result
