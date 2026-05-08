"""
Sovereign AI — Narrator.

Translates a Sovereign AI decision + its 6 model votes + advisory-vote delta
into a 3-bullet plain-English explanation operators can actually read during
a trade review. Uses GPT-5.2 via the Emergent LLM universal key.

Caching discipline:
* 24-hour per-decision cache in ``sovereign_narrations`` — identical
  renders are a single paid LLM call per decision.
* No retry loops on provider failure (graceful degrade to a deterministic
  fallback narrative built from the model_votes dict — still useful, just
  less natural-sounding).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

NARRATION_CACHE_HOURS: int = 24
NARRATION_MODEL: str = "gpt-5.2"


def _deterministic_fallback(decision: dict[str, Any]) -> list[str]:
    """Build a bullet list from model_votes when the LLM is unavailable.

    Never raises — the fallback is always safe because the sovereign
    decision shape is guaranteed by ``sovereign_ai_core``.
    """
    bullets: list[str] = []
    mv = decision.get("model_votes") or {}
    strat = mv.get("strategist") or {}
    regime = mv.get("regime") or {}
    catalyst = mv.get("catalyst") or {}

    tier = decision.get("conviction_tier", "low")
    action = decision.get("action", "HOLD")
    conf = decision.get("confidence", 0.0)

    bullets.append(
        f"Sovereign is {tier}-conviction {action} on {decision.get('symbol','?')} "
        f"at {conf:.0%} (strategist vote bull={strat.get('bull',0):.2f} / "
        f"bear={strat.get('bear',0):.2f})."
    )

    reg_reason = regime.get("reason", "unknown")
    reg_gate = regime.get("gate", 1.0)
    bullets.append(
        f"Regime factor {reg_reason} applies a {reg_gate:.0%} size multiplier."
    )

    cat_state = catalyst.get("state", "normal")
    cat_risk = catalyst.get("event_risk", "normal")
    if decision.get("vetoes"):
        bullets.append(
            f"Hard veto(es) fired: {', '.join(decision['vetoes'])} — "
            f"forced HOLD + zero size."
        )
    else:
        bullets.append(
            f"Catalyst layer shows news_shock={cat_state}, "
            f"event_risk={cat_risk}."
        )
    return bullets


async def narrate_sovereign_decision(
    db: Any,
    decision_id: str,
) -> dict[str, Any]:
    """Render a plain-English 3-bullet explanation of a sovereign decision.

    Returns ``{decision_id, bullets: [str], cached: bool, model: str | null,
    generated_at: datetime}``.
    """
    if db is None:
        return {"error": "no_db", "bullets": []}

    # Cache lookup
    now = datetime.now(timezone.utc)
    cache_floor = now - timedelta(hours=NARRATION_CACHE_HOURS)
    try:
        cached = await db["sovereign_narrations"].find_one(
            {"decision_id": decision_id, "generated_at": {"$gte": cache_floor}},
            {"_id": 0},
        )
    except Exception:  # noqa: BLE001
        cached = None
    if cached:
        cached["cached"] = True
        return cached

    # Fetch the decision row
    try:
        decision = await db["sovereign_decisions"].find_one(
            {"decision_id": decision_id}, {"_id": 0},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[narrator] decision fetch failed: %s", exc)
        decision = None

    if not decision:
        return {"error": "decision_not_found", "decision_id": decision_id, "bullets": []}

    api_key = os.environ.get("EMERGENT_LLM_KEY")
    bullets: list[str]
    model_used: str | None = None

    if not api_key:
        bullets = _deterministic_fallback(decision)
    else:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage

            chat = LlmChat(
                api_key=api_key,
                session_id=f"sovereign_narrate_{decision_id}",
                system_message=(
                    "You are an internal trading-risk analyst translating a "
                    "deterministic trading model's decision into 3 concise, "
                    "operator-friendly bullets. Be factual, specific to the "
                    "numbers provided, and avoid generic hedging language. "
                    "Write exactly 3 bullets. Return ONLY the bullets, one "
                    "per line, each starting with '- '."
                ),
            ).with_model("openai", NARRATION_MODEL)

            mv = decision.get("model_votes") or {}
            advisory = decision.get("advisory_votes") or {}
            prompt = (
                f"Symbol: {decision.get('symbol')} "
                f"({decision.get('asset_type')})\n"
                f"Sovereign action: {decision.get('action')} "
                f"@ confidence {decision.get('confidence'):.2%} "
                f"(tier: {decision.get('conviction_tier')}, "
                f"size multiplier: {decision.get('size_multiplier'):.2f})\n"
                f"Reasons: {', '.join(decision.get('reasons') or [])}\n"
                f"Vetoes: {', '.join(decision.get('vetoes') or []) or 'none'}\n"
                f"Model votes: {mv}\n"
                f"Advisory votes (other AIs): {advisory}\n"
                f"Calibration score: {decision.get('calibration_score')}\n"
                "\nExplain to a human operator in 3 bullets:\n"
                "1. What Sovereign decided and at what conviction, citing the single "
                "strongest driving signal from the model votes.\n"
                "2. How the other AIs (Strategist / Commander advisory) voted and "
                "whether Sovereign agreed, disagreed, or was in a contribution lane.\n"
                "3. The main risk or veto factor operators should watch."
            )
            resp = await chat.send_message(UserMessage(text=prompt))
            text = str(resp or "").strip()
            # Parse lines that start with - or *
            bullets = [
                ln.lstrip("-* ").strip()
                for ln in text.splitlines()
                if ln.strip().startswith(("-", "*"))
            ][:3]
            if len(bullets) < 3:
                # LLM returned unstructured text — fall back
                bullets = _deterministic_fallback(decision)
            else:
                model_used = NARRATION_MODEL
        except Exception as exc:  # noqa: BLE001
            logger.warning("[narrator] LLM call failed, using fallback: %s", exc)
            bullets = _deterministic_fallback(decision)

    payload: dict[str, Any] = {
        "decision_id": decision_id,
        "symbol": decision.get("symbol"),
        "asset_type": decision.get("asset_type"),
        "action": decision.get("action"),
        "bullets": bullets,
        "model": model_used,
        "generated_at": now,
        "cached": False,
    }
    try:
        await db["sovereign_narrations"].insert_one(
            {**payload, "generated_at": now},
        )
    except Exception:  # noqa: BLE001
        pass  # cache miss is non-critical
    return payload
