"""Equity Executor ML — US-equity lane (separate ML from crypto)."""
from services.ml.executors._base import _BaseExecutorML


class EquityExecutorML(_BaseExecutorML):
    artifact_env = "EQUITY_EXECUTOR_ARTIFACT"

    def __init__(self):
        super().__init__(lane="equity")
