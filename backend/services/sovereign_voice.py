"""
Sovereign Voice — deterministic narrative layer.

Renders a :class:`services.sovereign_ai_core.SovereignDecision` into a
structured payload an operator can read at a glance:

    {
      "verdict": "LONG 0.71",
      "key_drivers": [...],         # top-3 sub-models that argued FOR the verdict
      "dissent_flags": [...],       # sub-models that argued AGAINST
      "warnings": [...],            # small-sample / stale-data / restricted flags
      "rationale_text": "...",      # one terse paragraph, evidence-cited
    }

Doctrine
--------
* **No LLM calls.** This module is the operator-trustable voice for Sovereign.
  If it ever depended on a network model, the whole platform-independence
  story collapses. Pure template rendering from the structured
  ``model_votes`` + ``feature_snapshot`` that ``sovereign_decide`` already
  produces.
* **Microseconds, not seconds.** No I/O. No async. No external state.
  Safe to call inside the decision tick.
* **Sovereign's character is terse.** No narrative fluff, no
  "intriguing confluence of indicators." Just the numbers Sovereign
  acted on and which sub-models drove the verdict.
* **The voice MUST NOT invent data.** Every claim cites a feature
  value or a sub-model score. If a sub-model didn't fire, the voice
  doesn't speak about it.
"""
from __future__ import annotations

from typing import Any, Mapping

# Sub-model display names — keeps log lines readable without touching the
# canonical key names ``sovereign_ai_core`` uses on ``model_votes``.
_SUBMODEL_LABEL = {
    "strategist": "trend",
    "regime": "regime",
    "options_intent": "options_flow",
    "memory": "memory",
    "adversarial": "adversarial",
    "calibration": "calibration",
}


# ── Evidence extractors ──────────────────────────────────────────────


def _evidence_for_strategist(votes: Mapping[str, Any],
                             snap: Mapping[str, Any]) -> str:
    """RSI + momentum + adversarial-amp citation."""
    bits = []
    rsi = snap.get("rsi")
    if isinstance(rsi, (int, float)):
        bits.append(f"RSI {rsi:.0f}")
    mom = snap.get("momentum_5b")
    if isinstance(mom, (int, float)):
        bits.append(f"mom5b {mom:+.3f}")
    bull = votes.get("bull", 0)
    bear = votes.get("bear", 0)
    if bull or bear:
        bits.append(f"bull/bear {bull:.2f}/{bear:.2f}")
    return ", ".join(bits) or "—"


def _evidence_for_regime(votes: Mapping[str, Any],
                         snap: Mapping[str, Any]) -> str:
    regime = snap.get("regime") or "unknown"
    gate = votes.get("gate")
    if gate is None:
        return regime
    return f"{regime} (gate {gate:.2f})"


def _evidence_for_options(votes: Mapping[str, Any],
                          snap: Mapping[str, Any]) -> str:
    skew = votes.get("skew")
    if skew is None:
        return "no flow data"
    direction = (
        "bullish" if votes.get("bullish_flow") else
        "bearish" if votes.get("bearish_flow") else
        "neutral"
    )
    return f"skew {skew:+.2f} ({direction})"


def _evidence_for_memory(votes: Mapping[str, Any],
                         _snap: Mapping[str, Any]) -> str:
    n = votes.get("similar_n")
    win_rate = votes.get("similar_win_rate")
    if n is None or win_rate is None:
        return "no priors"
    return f"{int(n)} similar, {int(round(win_rate * 100))}% +R"


def _evidence_for_adversarial(votes: Mapping[str, Any],
                              _snap: Mapping[str, Any]) -> str:
    bull = votes.get("bull_score")
    bear = votes.get("bear_score")
    if bull is None or bear is None:
        return "shadow only"
    return f"Bull {bull:.2f} vs Bear {bear:.2f}"


def _evidence_for_calibration(votes: Mapping[str, Any],
                              _snap: Mapping[str, Any]) -> str:
    score = votes.get("score")
    if score is None:
        return "uncalibrated"
    return f"calibration {score:.2f}"


_EVIDENCE_BY_SUBMODEL = {
    "strategist": _evidence_for_strategist,
    "regime": _evidence_for_regime,
    "options_intent": _evidence_for_options,
    "memory": _evidence_for_memory,
    "adversarial": _evidence_for_adversarial,
    "calibration": _evidence_for_calibration,
}


# ── Score extraction ─────────────────────────────────────────────────


def _submodel_score(name: str, votes: Mapping[str, Any]) -> float:
    """Return a 0-1 score for how strongly this sub-model argued for
    the action. Different sub-models expose different shapes; this
    function normalizes them so they can be ranked together."""
    if name == "strategist":
        return float(votes.get("confidence", 0.5))
    if name == "regime":
        return float(votes.get("gate", 0.5))
    if name == "options_intent":
        # Aligned flow → strong, opposed → weak, no data → neutral.
        skew = votes.get("skew")
        if skew is None:
            return 0.5
        return min(1.0, 0.5 + abs(float(skew)) * 0.5)
    if name == "memory":
        win_rate = votes.get("similar_win_rate")
        return float(win_rate) if isinstance(win_rate, (int, float)) else 0.5
    if name == "adversarial":
        bull = float(votes.get("bull_score", 0) or 0)
        bear = float(votes.get("bear_score", 0) or 0)
        return max(bull, bear)
    if name == "calibration":
        return float(votes.get("score", 0.5) or 0.5)
    return 0.5


def _agrees_with_action(name: str, votes: Mapping[str, Any],
                        action: str) -> bool:
    """True if this sub-model's vote pointed in the same direction as
    the final action."""
    action_u = action.upper()
    if name == "strategist":
        sub_action = str(votes.get("action", "")).upper()
        return sub_action == action_u
    if name == "regime":
        # Regime gates rather than directing — high gate = supportive.
        return float(votes.get("gate", 0)) >= 0.6
    if name == "options_intent":
        if action_u == "LONG":
            return bool(votes.get("bullish_flow"))
        if action_u == "SHORT":
            return bool(votes.get("bearish_flow"))
        return False
    if name == "memory":
        win_rate = votes.get("similar_win_rate")
        if not isinstance(win_rate, (int, float)):
            return False
        return float(win_rate) >= 0.55
    if name == "adversarial":
        bull = float(votes.get("bull_score", 0) or 0)
        bear = float(votes.get("bear_score", 0) or 0)
        if action_u == "LONG":
            return bull > bear
        if action_u == "SHORT":
            return bear > bull
        return False
    if name == "calibration":
        return float(votes.get("score", 0.5) or 0.5) >= 0.6
    return False


# ── Warnings (small-sample / stale / restricted) ─────────────────────


def _gather_warnings(decision: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    snap = decision.get("feature_snapshot") or {}

    # News-shock / event risk
    ns = (snap.get("news_shock_state") or "").lower()
    if ns in ("elevated", "high"):
        out.append(f"news_shock={ns}")
    er = (snap.get("event_risk") or "").lower()
    if er in ("elevated", "restricted"):
        out.append(f"event_risk={er}")

    # Small-sample memory
    mem = (decision.get("model_votes") or {}).get("memory") or {}
    n = mem.get("similar_n")
    if isinstance(n, (int, float)) and 0 < n < 10:
        out.append(f"small_sample_memory(n={int(n)})")

    # Stale data — if dollar_volume is well below baseline, market is thin
    dv = snap.get("dollar_volume")
    dvb = snap.get("dollar_volume_baseline")
    if (isinstance(dv, (int, float)) and isinstance(dvb, (int, float))
            and dvb > 0 and dv < 0.25 * dvb):
        out.append("thin_volume")

    # Calibration low
    cal = decision.get("calibration_score")
    if isinstance(cal, (int, float)) and cal < 0.40:
        out.append(f"calibration_low({cal:.2f})")

    return out


# ── Public entry point ───────────────────────────────────────────────


def render_voice(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a SovereignDecision dict (e.g. from
    ``SovereignDecision.to_mongo_doc()`` or ``asdict()``) into a
    structured voice payload.

    The shape is stable. Downstream consumers (Shelly filer, dashboard,
    delta logger) can rely on ``verdict / key_drivers / dissent_flags /
    warnings / rationale_text`` always existing.
    """
    action = (decision.get("action") or decision.get("direction") or "HOLD").upper()
    conf = float(decision.get("confidence", 0.0) or 0.0)
    tier = decision.get("conviction_tier", "low")
    symbol = decision.get("symbol", "?")
    votes = decision.get("model_votes") or {}
    snap = decision.get("feature_snapshot") or {}

    # Rank every sub-model by score, then split into agrees / dissents.
    rows = []
    for name in votes:
        v = votes[name] if isinstance(votes[name], dict) else {}
        score = _submodel_score(name, v)
        agrees = _agrees_with_action(name, v, action)
        evidence_fn = _EVIDENCE_BY_SUBMODEL.get(
            name, lambda _v, _s: "—",
        )
        evidence = evidence_fn(v, snap)
        rows.append({
            "submodel": _SUBMODEL_LABEL.get(name, name),
            "score": round(score, 3),
            "evidence": evidence,
            "agrees": agrees,
        })
    rows.sort(key=lambda r: r["score"], reverse=True)

    key_drivers = [r for r in rows if r["agrees"]][:3]
    dissent_flags = [r for r in rows if not r["agrees"]][:3]
    warnings = _gather_warnings(decision)

    # Build the terse rationale string. Reads exactly what fired,
    # nothing it didn't.
    rationale_parts = [f"{action} {conf:.2f} on {symbol} (tier={tier})."]
    if key_drivers:
        drivers_str = "; ".join(
            f"{d['submodel']}={d['score']:.2f} [{d['evidence']}]"
            for d in key_drivers
        )
        rationale_parts.append(f"Drivers: {drivers_str}.")
    if dissent_flags:
        dissent_str = "; ".join(
            f"{d['submodel']} [{d['evidence']}]" for d in dissent_flags
        )
        rationale_parts.append(f"Dissent: {dissent_str}.")
    if warnings:
        rationale_parts.append(f"Warnings: {', '.join(warnings)}.")
    if decision.get("vetoes"):
        rationale_parts.append(f"Vetoes: {', '.join(decision['vetoes'])}.")

    return {
        "verdict": f"{action} {conf:.2f}",
        "action": action,
        "confidence": round(conf, 4),
        "tier": tier,
        "key_drivers": key_drivers,
        "dissent_flags": dissent_flags,
        "warnings": warnings,
        "rationale_text": " ".join(rationale_parts),
    }


__all__ = ["render_voice"]
