"""ai_core — canonical closed-loop trading primitives.

This package is the single source of truth for:
  * `simulator` — deterministic TP/SL path evaluation over future candles
  * `execution` — `ExecutionClient` abstraction (paper / live broker)
  * `learning_engine` — MongoDB-persisted trade outcome accumulator
  * `pipeline` — `run_full_pipeline` orchestrator

Everything downstream (backtester, signal-bot dispatcher, smart-order
monitor, eventually live broker hooks) should flow through these four
modules. Parallel learning loops outside ai_core are explicitly
deprecated — if you find yourself adding stats tracking somewhere else,
call into `LearningEngine` instead.

Design notes:
  * The classic in-memory `LearningEngine.stats` dict is replaced with a
    Mongo-backed async implementation. This codebase is a long-lived
    FastAPI process, not a notebook — in-memory state dies on every
    supervisor restart, which would poison multi-day learning windows.
  * Data objects are lightweight dataclasses rather than bare dicts so
    the simulator's `signal.entry`-style attribute access works
    without ceremony. Callers that already speak dicts (routes, bot
    configs) convert on the boundary via `Signal.from_dict(...)`.
  * Sync `get_trade_result_from_df` preserves the user-supplied
    drop-in spec verbatim so backtests remain fast and loop-free.
"""

from .models import Signal, Trade, TradeResult, ExecutionResult  # noqa: F401
from .simulator import get_trade_result_from_df  # noqa: F401
from .execution import ExecutionClient  # noqa: F401
from .learning_engine import LearningEngine  # noqa: F401
from .pipeline import (  # noqa: F401
    run_full_pipeline,
    run_full_pipeline_live,
    run_full_pipeline_backtest,
)
