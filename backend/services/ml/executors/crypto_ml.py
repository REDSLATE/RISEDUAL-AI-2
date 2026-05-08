"""Crypto Executor ML — 24/7 lane (separate ML from equity)."""
from services.ml.executors._base import _BaseExecutorML


class CryptoExecutorML(_BaseExecutorML):
    artifact_env = "CRYPTO_EXECUTOR_ARTIFACT"

    def __init__(self):
        super().__init__(lane="crypto")
