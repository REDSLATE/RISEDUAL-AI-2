"""
Alpha prompt helper.

Use this immediately before the existing LLM decision call. The broker facts
are INPUT EVIDENCE only; the model should never receive credentials and should
never call broker methods directly.
"""
from __future__ import annotations

import json
from typing import Any


ACCOUNT_POLICY = """
ACCOUNT-AWARE DECISION POLICY:
- Keep market quality/conviction separate from account fit.
- Never invent balances, positions, buying power, or orders.
- Do not create a duplicate BUY when a same-side open order already exists.
- A low account-fit score may reduce/hold/block execution without lowering
  the underlying market-conviction score.
- Exits are not blocked merely because buying power is low.
- Return the normal Alpha decision schema. Include `account_reason` when
  account state materially changes the action or size.
""".strip()


def append_account_context(existing_prompt: str, payload: dict[str, Any]) -> str:
    compact = {
        "account_context": payload.get("account_context", {}),
        "account_fit": payload.get("account_fit", {}),
        "broker_effective_notional": payload.get("broker_effective_notional"),
    }
    return (
        existing_prompt.rstrip()
        + "\n\n"
        + ACCOUNT_POLICY
        + "\n\nBROKER ACCOUNT SNAPSHOT (read-only evidence):\n"
        + json.dumps(compact, separators=(",", ":"), ensure_ascii=False)
    )
