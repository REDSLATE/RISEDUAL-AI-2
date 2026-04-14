"""Key Rotator — manages multiple API keys per provider with automatic failover.

Usage:
    rotator = KeyRotator("FRED_API_KEY")  # reads comma-separated keys from env
    key = rotator.get()                    # returns next healthy key
    rotator.mark_failed(key)               # marks key as failed with cooldown
    rotator.mark_success(key)              # resets failure state
"""
import os
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 120


class KeyRotator:
    def __init__(self, env_var: str, cooldown: int = COOLDOWN_SECONDS):
        raw = os.environ.get(env_var, "")
        self.env_var = env_var
        self.cooldown = cooldown
        self.keys = [k.strip() for k in raw.split(",") if k.strip()]
        self._index = 0
        self._failures: dict[str, float] = {}

    @property
    def available(self) -> bool:
        return len(self.keys) > 0

    @property
    def count(self) -> int:
        return len(self.keys)

    def _is_healthy(self, key: str) -> bool:
        failed_at = self._failures.get(key)
        if failed_at is None:
            return True
        return (time.time() - failed_at) > self.cooldown

    def get(self) -> Optional[str]:
        """Return the next healthy key via round-robin. None if all exhausted."""
        if not self.keys:
            return None
        tried = 0
        while tried < len(self.keys):
            key = self.keys[self._index % len(self.keys)]
            self._index += 1
            if self._is_healthy(key):
                return key
            tried += 1
        # All keys in cooldown — return the least-recently-failed one
        oldest = min(self.keys, key=lambda k: self._failures.get(k, 0))
        logger.warning(f"[KeyRotator:{self.env_var}] All {len(self.keys)} keys in cooldown, forcing {oldest[:8]}...")
        return oldest

    def mark_failed(self, key: str):
        self._failures[key] = time.time()
        healthy = sum(1 for k in self.keys if self._is_healthy(k))
        logger.warning(f"[KeyRotator:{self.env_var}] Key {key[:8]}... failed. {healthy}/{len(self.keys)} healthy.")

    def mark_success(self, key: str):
        self._failures.pop(key, None)

    def status(self) -> dict:
        return {
            "provider": self.env_var,
            "total_keys": len(self.keys),
            "healthy_keys": sum(1 for k in self.keys if self._is_healthy(k)),
            "configured": self.available,
        }
