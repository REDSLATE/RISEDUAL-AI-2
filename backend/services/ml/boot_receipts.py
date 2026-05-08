"""Boot-receipts registry.

Single source of truth for which ML lanes are ``ready`` at process
boot. The admin endpoint /api/admin/ml/boot-receipts reads from
:func:`get_all_receipts`. Tests assert the expected shape.

Boot order: any module that owns a layer registers its receipt at
import time via :func:`register`. Re-registration overwrites — a
process restart wipes the registry.
"""
from __future__ import annotations

from threading import RLock
from typing import Dict, List

from services.ml.contracts import ModelBootReceipt

_lock = RLock()
_receipts: Dict[str, ModelBootReceipt] = {}


def _key(layer: str, lane: str | None) -> str:
    return f"{layer}::{lane or '-'}"


def register(receipt: ModelBootReceipt) -> ModelBootReceipt:
    """Register / overwrite a boot receipt. Returns the receipt for
    chained-call convenience inside layer ``__init__``."""
    with _lock:
        _receipts[_key(receipt.layer, receipt.lane)] = receipt
    return receipt


def get(layer: str, lane: str | None = None) -> ModelBootReceipt | None:
    with _lock:
        return _receipts.get(_key(layer, lane))


def get_all_receipts() -> List[ModelBootReceipt]:
    with _lock:
        return list(_receipts.values())


def clear() -> None:
    """Test helper. Wipes the registry."""
    with _lock:
        _receipts.clear()
