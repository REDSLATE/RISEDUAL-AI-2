"""Multi-model hypothesis service — runs the 4 RISEDUAL brain
personas individually or as a 4-way consensus.

Personas are defined in ``services.brain_persona_service``. Each brain
has its own underlying LLM provider for genuine consensus diversity
(Alpha→GPT-5.2, Camaro→Claude Sonnet 4.5, Chevelle→Gemini 2.5 Flash,
RedEye→GPT-5.2 with contrarian prompt).

Doctrine V3 / 2026-05-14: when a brain produces a hypothesis we ALSO
fold in any recent matching opinion that brain has posted on Mission
Control, so the brain stays consistent with its operator-side voice.
"""
import json
import asyncio
import logging
import os
from typing import Any


from emergentintegrations.llm.chat import LlmChat, UserMessage

from services.brain_persona_service import BRAINS, brain_runtime
from services.confidence_weighting import (
    HIGH_CONVICTION_OVERRIDE,
    apply_disagreement_penalty,
    BrainWeightState,
)

logger = logging.getLogger(__name__)

# Backwards-compat alias — older code paths read ``MODELS``. Now points
# at the brain registry, which carries the same shape (label/weight/
# provider/model).
MODELS: dict[str, dict[str, Any]] = BRAINS

SYSTEM_MESSAGE = """You are an elite Wall Street quantitative analyst and AI research engine.
When given a ticker symbol (stock or crypto), produce a comprehensive investment hypothesis using ALL provided data.

Your hypothesis MUST include:
1. VERDICT: BUY / SELL / HOLD
2. CONFIDENCE: 0-100%
3. PRICE TARGET: Near-term (1-2 weeks) and medium-term (1-3 months) targets
4. THESIS: 2-3 paragraph investment thesis explaining the reasoning
5. KEY CATALYSTS: Top 3-5 positive catalysts
6. KEY RISKS: Top 3-5 risk factors
7. CONGRESSIONAL ACTIVITY: Any relevant congressional trades in this ticker or sector
8. SECTOR IMPACT: How world events and macro conditions affect this specific ticker
9. TECHNICAL OUTLOOK: Brief technical assessment based on available data

Be specific, cite actual data points from the provided information, and always acknowledge uncertainty.
Output in JSON format:
{
  "verdict": "BUY/SELL/HOLD",
  "confidence": 0-100,
  "price_target_short": "...",
  "price_target_medium": "...",
  "thesis": "...",
  "catalysts": ["...", "..."],
  "risks": ["...", "..."],
  "congressional_activity": "...",
  "sector_impact": "...",
  "technical_outlook": "...",
  "summary": "One-line summary"
}"""


def _build_prompt(symbol: str, data: dict) -> str:
    """Build the analysis prompt with all macro data."""
    sections = []

    news = data.get("news", [])
    if news:
        items = [f"- [{n.get('source','N/A')}] {n.get('title','N/A')}" for n in news[:8]]
        sections.append("FINANCIAL NEWS:\n" + "\n".join(items))
    else:
        sections.append("FINANCIAL NEWS:\nNo news data available")

    events = data.get("world_events", {})
    if events:
        items = []
        for e in events.get("high_impact_events", [])[:5]:
            sectors = ", ".join([s["sector"] for s in e.get("affected_sectors", [])[:3]])
            items.append(f"- {e.get('title','N/A')} (Sectors: {sectors})")
        sections.append("WORLD EVENTS:\n" + ("\n".join(items) or "No high-impact events"))
    else:
        sections.append("WORLD EVENTS:\nNo world events data")

    markets = data.get("foreign_markets", {})
    if markets:
        items = []
        for region in ["asia", "europe", "americas"]:
            for m in markets.get(region, [])[:3]:
                chg = m.get("change_percent", 0)
                arrow = "+" if chg >= 0 else ""
                items.append(f"- {m.get('name','N/A')}: {arrow}{chg}%")
        for c in markets.get("commodities", [])[:3]:
            items.append(f"- {c.get('name','N/A')}: ${c.get('price',0)} ({c.get('change_percent',0)}%)")
        sections.append("FOREIGN MARKETS:\n" + ("\n".join(items) or "No market data"))
    else:
        sections.append("FOREIGN MARKETS:\nNo foreign market data")

    gov = data.get("gov_filings", {})
    if gov:
        congress = [f"- {t.get('description','N/A')}" for t in gov.get("congressional_trades", [])[:8]]
        sections.append("CONGRESSIONAL TRADES:\n" + ("\n".join(congress) or "No congressional trades"))

        # Insider trades (from Finnhub SEC Form 4 filings)
        insider_list = gov.get("insider_trades", [])
        if not insider_list:
            insider_list = [{"description": f"{t.get('name','?')} {t.get('action','?')} {t.get('shares',0)} shares @ ${t.get('price','?')}"} for t in gov.get("insider_transactions", [])[:8]]
        insiders = [f"- {t.get('description','N/A')}" for t in insider_list[:8]]
        sections.append("INSIDER TRADES (SEC Form 4):\n" + ("\n".join(insiders) or "No insider filings"))

        fed = [f"- {a.get('title','N/A')}" for a in gov.get("fed_announcements", [])[:3]]
        sections.append("FED ANNOUNCEMENTS:\n" + ("\n".join(fed) or "No Fed announcements"))

        # Upcoming earnings from Finnhub
        earnings = gov.get("upcoming_earnings", [])
        if earnings:
            items = [f"- {e.get('symbol','?')} on {e.get('date','?')} (EPS est: {e.get('eps_estimate','N/A')})" for e in earnings[:10]]
            sections.append("UPCOMING EARNINGS (next 14 days):\n" + "\n".join(items))

        # Company news from Finnhub
        finnhub_news = gov.get("company_news_finnhub", [])
        if finnhub_news:
            items = [f"- [{a.get('source','?')}] {a.get('headline','N/A')}" for a in finnhub_news[:5]]
            sections.append("COMPANY-SPECIFIC NEWS (Finnhub):\n" + "\n".join(items))
    else:
        sections.append("CONGRESSIONAL TRADES:\nNo data")

    social = data.get("social", [])
    if social:
        items = [f"- {s.get('title','N/A')} (Score: {s.get('score',0)})" for s in social[:5]]
        sections.append("SOCIAL SENTIMENT:\n" + "\n".join(items))

    crypto = data.get("crypto", [])
    if crypto:
        items = [f"- {c.get('symbol','N/A')}: {c.get('sentiment', c.get('price','N/A'))}" for c in crypto[:5]]
        sections.append("CRYPTO DATA:\n" + "\n".join(items))

    body = "\n\n".join(sections)
    return f"Generate an investment hypothesis for **{symbol.upper()}**.\n\nAVAILABLE DATA:\n\n{body}\n\nProvide your hypothesis for {symbol.upper()} in JSON format."


def _parse_json_response(text: str) -> dict:
    """Extract JSON from LLM response text."""
    raw = text.strip() if isinstance(text, str) else text.text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    return json.loads(raw)


async def _fetch_recent_mc_opinion(model_key: str, symbol: str) -> str:
    """Best-effort lookup of this brain's recent MC opinions on ``symbol``.

    Returns a short text snippet to inject into the system prompt so the
    brain stays consistent with what it's been saying publicly. Always
    returns a string (empty if MC unreachable / brain has no take).
    Never raises.
    """
    runtime = brain_runtime(model_key)
    if not runtime:
        return ""
    try:
        from services import risedual_monorepo_client as mc
        resp = await mc.read_opinions(runtime=runtime, symbol=symbol, limit=3)
    except Exception as e:  # noqa: BLE001
        logger.debug("[brain_persona] MC lookup failed for %s: %s", runtime, e)
        return ""
    if not isinstance(resp, dict) or resp.get("error"):
        return ""
    items = resp.get("items") or []
    if not items:
        return ""
    bullets: list[str] = []
    for op in items[:3]:
        stance = (op.get("stance") or "").strip()
        body = (op.get("body") or "").strip()
        if body:
            bullets.append(f"- ({stance}) {body[:240]}")
    if not bullets:
        return ""
    return (
        "\n\nYour recent operator-side opinions on this symbol (for "
        "internal consistency — do not quote them verbatim):\n"
        + "\n".join(bullets)
    )


async def _run_single_model(api_key: str, model_key: str, symbol: str, prompt: str) -> dict:
    """Run hypothesis generation on a single brain persona.

    Uses the brain's ``system_prompt`` so each persona has a genuinely
    different voice. Folds in the brain's recent MC opinions on the
    symbol when available.
    """
    cfg = MODELS[model_key]
    raw_text = ""
    try:
        system = cfg.get("system_prompt") or SYSTEM_MESSAGE
        mc_note = await _fetch_recent_mc_opinion(model_key, symbol)
        # The shared SYSTEM_MESSAGE (output schema) is appended so every
        # brain still returns the same structured JSON the UI expects.
        full_system = f"{system}\n\n{SYSTEM_MESSAGE}{mc_note}"

        chat = LlmChat(
            api_key=api_key,
            session_id=f"hypothesis_{model_key}_{symbol}",
            system_message=full_system,
        ).with_model(cfg["provider"], cfg["model"])

        response = await chat.send_message(UserMessage(text=prompt))
        raw_text = response if isinstance(response, str) else response.text
        hypothesis = _parse_json_response(raw_text)
        hypothesis["model"] = cfg["label"]
        hypothesis["model_key"] = model_key
        hypothesis["symbol"] = symbol.upper()
        return hypothesis

    except json.JSONDecodeError:
        # 2026-05-15: do NOT mask a parse failure as a NEUTRAL/50 vote.
        # Previously this returned {"verdict": "NEUTRAL", "confidence": 50}
        # which the consensus then treated as a real opinion, flattening
        # the result towards HOLD. Mark it as ERROR so the consensus
        # filter drops it — an honest "this brain went silent" instead
        # of "this brain mildly disagreed."
        return {
            "model": cfg["label"],
            "model_key": model_key,
            "symbol": symbol.upper(),
            "verdict": "ERROR",
            "confidence": 0,
            "thesis": raw_text[:500] or "Analysis completed but structured parsing failed",
            "summary": f"{cfg['label']} response parse failed — dropped from consensus",
            "catalysts": [],
            "risks": [],
            "error": True,
            "error_kind": "json_parse_failed",
        }
    except Exception as e:
        logger.error(f"{cfg['label']} hypothesis error for {symbol}: {e}")
        return {
            "model": cfg["label"],
            "model_key": model_key,
            "symbol": symbol.upper(),
            "verdict": "ERROR",
            "confidence": 0,
            "thesis": f"Model encountered an error: {str(e)[:100]}",
            "summary": f"{cfg['label']} failed",
            "catalysts": [],
            "risks": [],
            "error": True,
        }


def _weighted_consensus(
    results: list[dict],
    *,
    brain_weights: BrainWeightState | None = None,
) -> dict:
    """Compute weighted voting consensus from multiple model results.

    Doctrine (2026-05-15 — Camaro hold-trap RCA):
      * Disagreement is a **bounded penalty**, never a flatten to 0.50.
      * The receipt is **honest** — market judgment is kept separate
        from execution restraint. A blocked-feeling HOLD will surface
        ``would_have_traded_without_gates=True`` and the original
        directional ``raw_action`` so the brain stays auditable.
      * HOLD does not get to win by passive accumulation: when at
        least one brain emits a directional verdict above the
        ``DIRECTIONAL_FLOOR`` confidence (55 %), HOLD is treated as an
        abstention, not a competing direction.
    """
    valid = [r for r in results if r.get("verdict") not in ("ERROR", None)]
    if not valid:
        return {
            "verdict": "NEUTRAL",
            "confidence": 0,
            "summary": "All models failed — unable to generate consensus",
            "market_decision": "HOLD",
            "execution_decision": "OBSERVE_ONLY",
            "display_action": "HOLD",
            "raw_action": "HOLD",
            "raw_confidence": 0,
            "final_action": "HOLD",
            "final_confidence": 0,
            "hold_reason": "ALL_BRAINS_FAILED",
            "blocked_by": ["ALL_BRAINS_FAILED"],
            "would_have_traded_without_gates": False,
            "pre_weight_confidence": 0,
            "post_weight_confidence": 0,
            "council_penalty": 0,
            "disagreement_kind": "ALL_HOLD",
            "individual_weights": {},
            "override_reason": None,
            "override_brain": None,
            "override_confidence": None,
        }

    weights = brain_weights or BrainWeightState()
    weight_map = weights.as_mapping()

    DIRECTIONAL_FLOOR = 55  # confidence pct below which a directional vote is treated as abstention

    verdict_weight = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0, "NEUTRAL": 0.0}
    directional_weight = {"BUY": 0.0, "SELL": 0.0}
    total_weight = 0.0
    weighted_confidence = 0.0
    verdicts_for_disagreement: list[str] = []

    for r in valid:
        key = r["model_key"]
        base_w = MODELS.get(key, {}).get("weight", 0.33)
        dyn_w = weight_map.get(key, 1.0)
        w = base_w * dyn_w
        verdict = (r.get("verdict") or "NEUTRAL").upper()
        if verdict not in verdict_weight:
            verdict = "NEUTRAL"
        confidence = float(r.get("confidence", 50) or 50)
        verdict_weight[verdict] += w
        if verdict in ("BUY", "SELL") and confidence >= DIRECTIONAL_FLOOR:
            directional_weight[verdict] += w
        verdicts_for_disagreement.append(verdict)
        weighted_confidence += confidence * w
        total_weight += w

    # Pre-weight confidence: pure weighted mean across brains.
    pre_weight_conf_pct = round(weighted_confidence / total_weight) if total_weight else 50

    # Decide market_decision (raw, un-penalised market judgment).
    #
    # 2026-05-16 — HIGH-CONVICTION OVERRIDE (A-pattern):
    # If any single brain emits a directional verdict at
    # ≥ HIGH_CONVICTION_OVERRIDE confidence, that brain wins the
    # market_decision regardless of council split. The disagreement
    # penalty still bites ``post_weight_confidence`` so the receipt
    # remains honest about dissent — what we change is direction,
    # not headline confidence. This unblocks the trapped strong
    # signal without softening the penalty on noisy splits.
    override_reason: str | None = None
    override_brain: str | None = None
    override_verdict: str | None = None
    override_confidence: int | None = None
    for r in valid:
        v = (r.get("verdict") or "").upper()
        c = float(r.get("confidence", 0) or 0)
        if v in ("BUY", "SELL") and c >= HIGH_CONVICTION_OVERRIDE:
            # First brain to clear the override bar wins. Ties
            # extremely unlikely (need two brains both ≥80% with
            # the same verdict — that's just unanimous high
            # conviction, the override picks either).
            override_brain = r["model_key"]
            override_verdict = v
            override_confidence = int(c)
            override_reason = (
                f"HIGH_CONVICTION_{override_brain.upper()}_"
                f"AT_{override_confidence}PCT"
            )
            break

    if override_verdict is not None:
        market_decision = override_verdict
    else:
        # HOLD-trap fix: if ANY directional vote cleared the floor,
        # the higher-weighted side wins, even if absolute HOLD weight
        # exceeds each directional weight individually. HOLD only
        # wins when no directional vote crossed the floor.
        raw_directional = max(directional_weight, key=directional_weight.get)
        if directional_weight[raw_directional] > 0:
            market_decision = raw_directional
        else:
            # No directional signal cleared the floor — HOLD is honest.
            market_decision = "HOLD"

    # Apply bounded disagreement penalty to confidence.
    pre_conf_unit = pre_weight_conf_pct / 100.0
    disagreement = apply_disagreement_penalty(
        pre_confidence=pre_conf_unit, verdicts=verdicts_for_disagreement,
    )
    post_weight_conf_pct = round(disagreement.post_penalty * 100)
    council_penalty_pct = round(disagreement.delta * 100, 1)

    # Display + execution layering.
    # RISEDUAL is a headless brain (Doctrine V3) — execution lives on
    # Mission Control. We never *gate* trades locally, but we still
    # expose the would-have-traded signal so MC and operators can see
    # the brain's honest market judgment vs. its display action.
    execution_decision = "OBSERVE_ONLY"
    blocked_by: list[str] = []
    hold_reason: str | None = None
    would_have_traded = market_decision in ("BUY", "SELL")
    if market_decision == "HOLD":
        if disagreement.kind == "HARD_CONFLICT":
            hold_reason = "COUNCIL_HARD_CONFLICT"
        elif disagreement.kind == "ALL_HOLD":
            hold_reason = "NO_DIRECTIONAL_SIGNAL"
        else:
            hold_reason = "DIRECTIONAL_FLOOR_NOT_CLEARED"
            blocked_by.append("DIRECTIONAL_FLOOR")

    # Catalysts / risks dedup (unchanged from prior behaviour).
    all_catalysts: list[str] = []
    all_risks: list[str] = []
    for r in valid:
        all_catalysts.extend(r.get("catalysts", [])[:3])
        all_risks.extend(r.get("risks", [])[:3])

    seen_cat: set[str] = set()
    unique_catalysts: list[str] = []
    for c in all_catalysts:
        short = c[:50].lower()
        if short not in seen_cat:
            seen_cat.add(short)
            unique_catalysts.append(c)
        if len(unique_catalysts) >= 6:
            break

    seen_risk: set[str] = set()
    unique_risks: list[str] = []
    for r in all_risks:
        short = r[:50].lower()
        if short not in seen_risk:
            seen_risk.add(short)
            unique_risks.append(r)
        if len(unique_risks) >= 6:
            break

    best = max(valid, key=lambda x: MODELS.get(x["model_key"], {}).get("weight", 0))
    agree_count = sum(
        1 for r in valid
        if (r.get("verdict") or "").upper() == market_decision
    )
    agreement = round(agree_count / len(valid) * 100)

    display_action = market_decision  # RISEDUAL never overrides display today
    return {
        # ── legacy surface (unchanged shape so existing consumers keep working) ──
        "verdict": display_action,
        "confidence": post_weight_conf_pct,
        "agreement": agreement,
        "price_target_short": best.get("price_target_short", "N/A"),
        "price_target_medium": best.get("price_target_medium", "N/A"),
        "thesis": best.get("thesis", ""),
        "catalysts": unique_catalysts,
        "risks": unique_risks,
        "congressional_activity": best.get("congressional_activity", ""),
        "sector_impact": best.get("sector_impact", ""),
        "technical_outlook": best.get("technical_outlook", ""),
        "summary": (
            f"Consensus: {display_action} ({post_weight_conf_pct}% confidence, "
            f"{agreement}% model agreement)"
        ),
        # ── new transparency receipt (2026-05-15 doctrine) ──
        "market_decision": market_decision,
        "execution_decision": execution_decision,
        "display_action": display_action,
        "raw_action": market_decision,
        "raw_confidence": pre_weight_conf_pct,
        "final_action": display_action,
        "final_confidence": post_weight_conf_pct,
        "hold_reason": hold_reason,
        "blocked_by": blocked_by,
        "would_have_traded_without_gates": would_have_traded,
        "pre_weight_confidence": pre_weight_conf_pct,
        "post_weight_confidence": post_weight_conf_pct,
        "council_penalty": council_penalty_pct,
        "disagreement_kind": disagreement.kind,
        "individual_weights": weight_map,
        # High-conviction override audit (2026-05-16, A-pattern).
        # ``override_reason`` is None when the consensus path took
        # the normal floor / weight route; non-None when a single
        # brain at ≥ HIGH_CONVICTION_OVERRIDE % flipped the direction.
        "override_reason": override_reason,
        "override_brain": override_brain,
        "override_confidence": override_confidence,
    }


async def generate_hypothesis(api_key: str, symbol: str, data: dict, model: str = "alpha") -> dict:
    """Generate hypothesis using a single brain persona, or 4-brain
    consensus when ``model='consensus'``.

    For unknown / legacy model keys we coerce to ``alpha`` (the free
    tier default).
    """
    if model == "consensus":
        prompt = _build_prompt(symbol, data)
        tasks = [_run_single_model(api_key, mk, symbol, prompt) for mk in MODELS]
        results = await asyncio.gather(*tasks)

        consensus = _weighted_consensus(results)
        consensus["symbol"] = symbol.upper()
        consensus["model"] = "Consensus"
        consensus["model_key"] = "consensus"
        consensus["individual_results"] = [
            {
                "model": r["model"],
                "model_key": r["model_key"],
                "verdict": r.get("verdict", "ERROR"),
                "confidence": r.get("confidence", 0),
                "summary": r.get("summary", ""),
                "error": r.get("error", False),
            }
            for r in results
        ]
        # 2026-05-15: emit the doctrine receipt to MC's /api/intents
        # for the operator audit trail. Off by default — flipping
        # ``RISEDUAL_EMIT_INTENTS_TO_MC=1`` turns it on once MC's
        # endpoint is fielded and the operator has confirmed the
        # rollout window. Fire-and-forget; MC failures never block
        # the hypothesis response.
        if os.environ.get("RISEDUAL_EMIT_INTENTS_TO_MC", "0") == "1":
            try:
                from sovereign.mc_client import MCClient
                from sovereign.intent_bridge import emit_intent_from_consensus
                mc = MCClient(
                    base_url=os.environ.get("MC_BASE_URL", ""),
                    brain="alpha",
                    runtime_token=os.environ.get("ALPHA_INGEST_TOKEN", ""),
                )
                await emit_intent_from_consensus(mc, consensus)
            except Exception as exc:  # noqa: BLE001
                logger.warning("intent emit failed (non-fatal): %s", exc)
        return consensus

    # Single brain mode — call the brain's persona LLM directly. The
    # multi-agent CrewAI path was tuned for generic LLMs and would
    # erase the brain's personality, so we skip it for branded brains.
    if model not in MODELS:
        model = "alpha"
    prompt = _build_prompt(symbol, data)
    out = await _run_single_model(api_key, model, symbol, prompt)
    out["is_pro"] = True
    return out
