"""Every silent-return in maybe_route_live now logs a specific skip
reason. This locks in the operator's fail-visible invariant so an
intent can never disappear as the meta ``executor_rejected``.
"""
from __future__ import annotations

import inspect

import services.public_equity_live_executor as executor_mod


def test_no_bare_return_none_left_in_maybe_route_live():
    """Every ``return None`` inside maybe_route_live is now preceded
    by a call to _log_skip (or is inside an early-guard block that
    already logged). Belt-and-suspenders source scan."""
    src = inspect.getsource(executor_mod.maybe_route_live)
    lines = src.splitlines()

    # Collect line numbers with ``return None``.
    bare_returns: list[int] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "return None":
            bare_returns.append(i)

    # Each of those returns MUST have a ``_log_skip`` (or the
    # existing helpers ``_log_skip_intent``/``atlas.transition_async``
    # + a specific reason) within the preceding 30 lines.
    for idx in bare_returns:
        window = "\n".join(lines[max(0, idx - 30):idx])
        assert "_log_skip" in window, (
            f"return None at maybe_route_live line {idx} has no _log_skip "
            f"within 30 preceding lines — this is a silent-skip regression.\n"
            f"Context:\n{window[-800:]}"
        )


def test_new_specific_skip_reasons_appear_in_source():
    """The 8 previously-silent gates now emit these specific reason codes."""
    src = inspect.getsource(executor_mod.maybe_route_live)
    expected = [
        "dup_open_row",
        "sell_no_position",
        "no_mark_price",
        "qty_zero",
        "client_init_failed",
        "broker_watchdog_frozen",
        "place_order_exception",
        "broker_empty_response",
    ]
    missing = [r for r in expected if r not in src]
    assert not missing, (
        f"Silent-return patches missing reason codes: {missing}. "
        "Every previously-silent gate must emit a specific reason."
    )
