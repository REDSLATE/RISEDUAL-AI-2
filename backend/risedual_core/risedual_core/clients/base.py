"""Abstract base class for all market data API clients.

All concrete clients (Finnhub, Alpha Vantage, FRED, FMP) must subclass
``BaseMarketClient`` and implement the abstract ``base_url`` property.
The shared ``_get`` method centralises HTTP logic including:

- Shared vs. transient httpx client lifecycle
- Proactive rate limiting (token bucket per provider)
- 429 / 503 back-off with jitter
- Alpha Vantage-style "200 OK but error payload" detection
- Uniform error logging to stderr
"""
from __future__ import annotations

import asyncio
import sys
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Token bucket rate limiter
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Thread-safe async token bucket for rate limiting.

    Parameters
    ----------
    rate:
        Tokens added per second.
    capacity:
        Maximum tokens the bucket can hold (= max burst size).
    """

    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                # Calculate how long until one token is available
                wait = (1 - self._tokens) / self._rate
            # Release the lock while sleeping so other coroutines can proceed
            await asyncio.sleep(wait)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now


# ---------------------------------------------------------------------------
# Known error-payload patterns (200 OK but error body)
# ---------------------------------------------------------------------------

# Keys that signal an error in an otherwise-200 response body.
# Alpha Vantage returns {"Note": "Thank you for..."} on rate limit,
# {"Error Message": "..."} on bad symbol, {"Information": "..."} on quota.
_ERROR_PAYLOAD_KEYS: frozenset[str] = frozenset({
    "Error Message",
    "Note",
    "Information",
})


def _extract_error_payload(data: Any, url: str) -> str | None:
    """Return a warning message if *data* contains an error payload, else None."""
    if not isinstance(data, dict):
        return None
    for key in _ERROR_PAYLOAD_KEYS:
        if key in data:
            return f"[WARNING] API error payload from {url} ({key!r}): {data[key]}"
    return None


# ---------------------------------------------------------------------------
# BaseMarketClient
# ---------------------------------------------------------------------------

class BaseMarketClient(ABC):
    """Abstract base class providing shared HTTP infrastructure for market clients.

    Concrete subclasses must implement:
    - :attr:`base_url` — the provider's base URL
    - :attr:`_rate_limiter` — a :class:`_TokenBucket` sized to the provider's limits

    All HTTP GET requests should go through :meth:`_get`, which handles the
    full reliability stack: rate limiting, shared client lifecycle, 429 back-off
    with jitter, error-payload detection, and uniform stderr logging.

    Parameters
    ----------
    api_key:
        API key or token for the data provider.
    http_client:
        Optional shared :class:`httpx.AsyncClient`. When supplied the caller
        is responsible for its lifecycle. When ``None`` (default) a new
        transient client is created and closed per request.
    """

    # Subclasses override this to set provider-specific rate limits.
    # Default: 60 calls/min (1/s), burst of 5.
    _default_rate: float = 1.0
    _default_capacity: float = 5.0

    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._http_client = http_client
        self._rate_limiter = _TokenBucket(
            rate=self._default_rate,
            capacity=self._default_capacity,
        )

    # ── Abstract interface ────────────────────────────────────────────────────

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Base URL for the data provider API (no trailing slash)."""
        ...

    # ── Shared HTTP layer ─────────────────────────────────────────────────────

    async def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: float = 20.0,
        _retry: int = 0,
    ) -> Any:
        """Perform a rate-limited async GET and return the decoded JSON body.

        Reliability stack (in order):
        1. Acquire a rate-limit token before sending the request
        2. Use the shared ``http_client`` when provided, else a transient one
        3. On 429 / 503: exponential back-off with jitter (up to 2 retries)
        4. Detect Alpha Vantage-style 200-OK error payloads and log them
        5. Return ``None`` on any unrecoverable failure (never raises)

        Parameters
        ----------
        url:
            Fully-qualified URL to request.
        params:
            Optional query string parameters dict.
        timeout:
            Request timeout in seconds (default 20.0).
        _retry:
            Internal retry counter — do not pass externally.
        """
        # Step 1: rate limit
        await self._rate_limiter.acquire()

        try:
            if self._http_client is not None:
                response = await self._http_client.get(url, params=params)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(url, params=params)

            # Step 3: handle retryable HTTP errors
            if response.status_code in (429, 503) and _retry < 2:
                import random  # noqa: PLC0415
                wait = (2 ** _retry) + random.uniform(0.5, 1.5)
                print(
                    f"[WARNING] HTTP {response.status_code} from {url}. "
                    f"Retrying in {wait:.1f}s (attempt {_retry + 1}/2).",
                    file=sys.stderr,
                )
                await asyncio.sleep(wait)
                return await self._get(url, params=params, timeout=timeout, _retry=_retry + 1)

            response.raise_for_status()
            data = response.json()

            # Step 4: detect 200-OK error payloads
            err = _extract_error_payload(data, url)
            if err:
                print(err, file=sys.stderr)
                return None

            return data

        except httpx.HTTPStatusError as exc:
            print(
                f"[WARNING] HTTP {exc.response.status_code} for {url}: {exc}",
                file=sys.stderr,
            )
            return None
        except httpx.RequestError as exc:
            print(f"[WARNING] Request error for {url}: {exc}", file=sys.stderr)
            return None
        except Exception as exc:  # noqa: BLE001
            print(f"[WARNING] Unexpected error for {url}: {exc}", file=sys.stderr)
            return None
