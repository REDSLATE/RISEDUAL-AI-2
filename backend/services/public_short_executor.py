"""Public.com REST short executor (P1-A, 2026-02).

Public's current REST API (April 2026) supports explicit short entries
via ``openCloseIndicator: OPEN`` and covers via ``openCloseIndicator:
CLOSE`` — but the installed Python SDK model is behind that contract
and doesn't expose the field. Rather than force it through the SDK's
``OrderRequest``, this module speaks REST directly for short paths only.

Doctrine
--------
* **Whole-share qty only.** Public rejects fractional shorts. Sizing
  helper returns integers; callers must not pass floats.
* **REST-only, no SDK OrderRequest.** We reuse the SDK client's auth
  headers + base URL, but the payload is built fresh so we can carry
  the newer fields (``openCloseIndicator``, ``useMargin``).
* **Long path untouched.** Long entries and long closes continue to
  flow through ``PublicTradingService.place_order`` unchanged. This
  module is invoked ONLY when ``intent_kind`` is ``open_short`` or
  ``close_short``.
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


def compute_whole_share_qty(*, notional_usd: float, mark_price: float) -> int:
    """Public requires whole-share quantities for shorts. Return the
    largest integer share count whose notional does not exceed the
    requested budget.

    Returns 0 when notional or price makes the trade infeasible — the
    caller must treat this as a skip (there's no way to short less
    than 1 share).
    """
    if notional_usd <= 0 or mark_price <= 0:
        return 0
    q = int(math.floor(notional_usd / mark_price))
    return max(q, 0)


def submit_short_order(
    client, *,
    symbol: str,
    qty: int,
    side: str,            # "SELL" for open, "BUY" for cover
    open_close: str,      # "OPEN" for entry, "CLOSE" for cover
    use_margin: bool = True,
    client_order_id: Optional[str] = None,
    time_in_force: str = "DAY",
    order_type: str = "MARKET",
) -> Optional[dict]:
    """Submit a short-side order via Public's REST endpoint.

    Returns ``{"id": ..., "status": ..., "raw": ...}`` on success or
    ``None`` on any failure. All errors are logged with Public's exact
    rejection body so operators can see WHY a fill was rejected —
    same doctrine as the long path.
    """
    try:
        headers = client._auth_headers()  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.warning("[public-short-rest] auth header failed: %s", exc)
        return None
    if headers is None:
        logger.warning("[public-short-rest] auth headers unavailable")
        return None
    headers = dict(headers)
    # Public's own user-agent hint (matches the SDK's convention).
    headers.setdefault("User-Agent", "public-dev-docs")

    side_u = (side or "").upper()
    if side_u not in ("SELL", "BUY"):
        logger.warning("[public-short-rest] rejected bad side=%r", side)
        return None
    open_close_u = (open_close or "").upper()
    if open_close_u not in ("OPEN", "CLOSE"):
        logger.warning("[public-short-rest] rejected bad openCloseIndicator=%r", open_close)
        return None
    if int(qty) <= 0:
        logger.warning("[public-short-rest] rejected non-positive qty=%r", qty)
        return None

    cid = client_order_id or str(uuid.uuid4())
    body: dict[str, Any] = {
        "instrument": {"symbol": symbol.upper(), "type": "EQUITY"},
        "orderSide": side_u,
        "orderType": (order_type or "MARKET").upper(),
        "quantity": str(int(qty)),
        "expiration": {"timeInForce": (time_in_force or "DAY").upper()},
        "orderId": cid,
        # Newer Public REST contract — SDK's OrderRequest does not
        # know about these fields yet, which is why we route shorts
        # through this direct-REST path instead of place_order.
        "openCloseIndicator": open_close_u,
        "useMargin": bool(use_margin),
    }
    url = f"{client.base_url}/trading/{client.account_id}/order"
    try:
        r = requests.post(url, headers=headers, json=body, timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[public-short-rest] POST %s failed: %s (%s)",
            url, exc, type(exc).__name__,
        )
        return None
    if r.status_code >= 400:
        # Log Public's exact body so we can classify borrow / margin /
        # market-closed rejections in the taxonomy dashboard.
        body_text = (r.text or "")[:400]
        logger.warning(
            "[public-short-rest] %s %s qty=%d openClose=%s rejected "
            "%d: %s",
            symbol, side_u, qty, open_close_u, r.status_code, body_text,
        )
        return None
    try:
        result = r.json() if r.content else {}
    except Exception:  # noqa: BLE001
        result = {}
    return {
        "id": result.get("orderId") or cid,
        "status": (result.get("status") or "submitted"),
        "symbol": symbol.upper(),
        "raw": result,
        # Echo the semantic fields back so the caller can persist them
        # without re-parsing the body.
        "openCloseIndicator": open_close_u,
        "useMargin": bool(use_margin),
    }


__all__ = ["compute_whole_share_qty", "submit_short_order"]
