"""Late-bound admin auth gate for the admin_ml route package.

Resolves ``_require_admin`` through the legacy
``routes.admin_ml_v2`` shim at call time so existing tests that
``monkeypatch.setattr(admin_ml_v2, "_require_admin", _ok)`` keep
working unchanged after the file split.

Behaviour is identical to importing ``_require_admin`` directly
from :mod:`routes.admin` — the indirection only matters when a
test has patched the shim.
"""
from __future__ import annotations

import sys
from typing import Any


async def require_admin(request: Any) -> Any:
    shim = sys.modules.get("routes.admin_ml_v2")
    if shim is not None:
        impl = getattr(shim, "_require_admin", None)
        if impl is not None:
            return await impl(request)
    from routes.admin import _require_admin as _impl
    return await _impl(request)
