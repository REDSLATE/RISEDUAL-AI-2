"""alpha_funnel — funnel orchestrator (discovery → research → promotion).

Operator design (2026-09-11) — a fluid discernment funnel that
wraps the existing 5-min discovery output, does broker research
on the middle tier, deep discernment on the top tier, and
promotes/demotes candidates dynamically. Promotion is never
one-way — a strengthening lower-ranked candidate can overtake a
weakening leader.

Stage sizes (env-tunable, runtime overrideable):

    DISCOVERY_UNIVERSE     40
    PRELIMINARY_SURVIVORS  12
    BROKER_RESEARCH_MAX     8
    DEEP_DISCERNMENT_MAX    4
    PROMOTED_ARMED          3

Behavior rules baked in:

* Discovery keeps everything the caller passed — the ``max_n``
  cap only limits NEW additions to the funnel this cycle, so we
  never accidentally reduce Alpha's field of view.
* Broker research is primarily a re-rank; only OBJECTIVE
  problems (stale price, over-cap spread/drift, no broker
  price, crossed quote) hard-block. A 0.69 vs 0.70 confidence
  gap is a delta, never a block.
* ACTIONABLE never persists across cycles — the 60s stream
  re-evaluates ARMED candidates on every tick and only promotes
  to ACTIONABLE for the *duration of that single trade attempt*.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Config ─────────────────────────────────────────────────────

@dataclass
class FunnelConfig:
    discovery_universe: int = 40
    preliminary_survivors: int = 12
    broker_research_max: int = 8
    deep_discernment_max: int = 4
    promoted_armed: int = 3


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


# Runtime overrides (persisted only in-memory; env is the boot default).
_RUNTIME_OVERRIDES: dict[str, int] = {}


def get_config() -> FunnelConfig:
    return FunnelConfig(
        discovery_universe=_RUNTIME_OVERRIDES.get(
            "discovery_universe",
            _env_int("ALPHA_FUNNEL_DISCOVERY", 40),
        ),
        preliminary_survivors=_RUNTIME_OVERRIDES.get(
            "preliminary_survivors",
            _env_int("ALPHA_FUNNEL_SURVIVORS", 12),
        ),
        broker_research_max=_RUNTIME_OVERRIDES.get(
            "broker_research_max",
            _env_int("ALPHA_FUNNEL_BROKER_RESEARCH", 8),
        ),
        deep_discernment_max=_RUNTIME_OVERRIDES.get(
            "deep_discernment_max",
            _env_int("ALPHA_FUNNEL_DEEP_DISCERNMENT", 4),
        ),
        promoted_armed=_RUNTIME_OVERRIDES.get(
            "promoted_armed",
            _env_int("ALPHA_FUNNEL_PROMOTED", 3),
        ),
    )


def set_runtime_override(key: str, value: Optional[int]) -> FunnelConfig:
    """Runtime knob for the admin endpoint. ``value=None`` clears."""
    valid = {
        "discovery_universe", "preliminary_survivors",
        "broker_research_max", "deep_discernment_max", "promoted_armed",
    }
    if key not in valid:
        raise ValueError(f"unknown funnel config key: {key}")
    if value is None:
        _RUNTIME_OVERRIDES.pop(key, None)
    else:
        _RUNTIME_OVERRIDES[key] = max(1, int(value))
    return get_config()


# ── Cycle orchestrator ─────────────────────────────────────────

async def run_funnel_cycle(
    db: Any, *, ranked_discovery: list[dict],
) -> dict:
    """Run one funnel cycle over the discovery output.

    ``ranked_discovery`` is a list of
    ``{"symbol", "score", "signal_price"}`` dicts, already ranked
    by the 5-min opportunity scanner.

    Returns a summary dict for logging + the admin endpoint.
    """
    from services import alpha_funnel_state as fs
    from services import alpha_broker_research as br

    cfg = get_config()
    logger.info(
        "[alpha_funnel] cycle start config=%s discovery_input=%d",
        cfg, len(ranked_discovery or []),
    )

    # 1. Discovery ingest — full list (no tail chop), cap only NEW adds.
    fs.ingest_discovery(ranked_discovery, max_n=cfg.discovery_universe)

    # 2. Preliminary rank — promote top survivors to RESEARCH.
    live_ranked = fs.rank_candidates()
    survivors = live_ranked[: cfg.preliminary_survivors]
    survivor_symbols = {c.symbol for c in survivors}
    # Demote candidates that fell out of the survivor cut (unless
    # they're already downstream of RESEARCH — protecting an
    # ARMED candidate from a small drop in the survivor rank).
    protected_states = {
        fs.CandidateState.ARMED,
        fs.CandidateState.ACTIONABLE,
        fs.CandidateState.EXECUTED,
    }
    for c in live_ranked:
        if c.symbol in survivor_symbols:
            if c.state == fs.CandidateState.DISCOVERED:
                fs.transition(
                    c.symbol,
                    to_state=fs.CandidateState.RESEARCH,
                    reason="entered_preliminary_survivors",
                )
        else:
            if c.state not in protected_states and c.state != fs.CandidateState.DISCOVERED:
                fs.transition(
                    c.symbol,
                    to_state=fs.CandidateState.WATCH,
                    reason="fell_out_of_preliminary_cut",
                )

    # 3. Broker research on the top ``broker_research_max``.
    research_pool = survivors[: cfg.broker_research_max]
    research_summary: list[dict] = []
    for c in research_pool:
        signal_price = 0.0
        # Prefer a signal price if we captured one from the last
        # broker snap; fall back to the score-neutral 0.0 which
        # produces None drift downstream.
        if c.last_broker_snapshot and c.last_broker_snapshot.get("signal_price"):
            signal_price = float(c.last_broker_snapshot["signal_price"])
        snap = await br.perform_research(
            symbol=c.symbol, signal_price=signal_price or c.current_score,
        )
        fs.attach_broker_snapshot(c.symbol, snap.to_dict())
        delta, reasons, hard_block = br.compute_research_delta(snap)
        if hard_block:
            fs.transition(
                c.symbol,
                to_state=fs.CandidateState.REJECTED,
                delta=-abs(delta) if delta else -0.10,
                reason="broker_research:" + ";".join(reasons)[:120],
            )
        else:
            fs.transition(
                c.symbol,
                to_state=fs.CandidateState.WATCH,
                delta=delta,
                reason="broker_research:" + ";".join(reasons)[:120],
            )
        research_summary.append({
            "symbol": c.symbol, "delta": delta,
            "reasons": reasons, "hard_block": hard_block,
        })

    # 4. Deep discernment — top of the re-ranked WATCH pool.
    watch_pool = [
        c for c in fs.rank_candidates()
        if c.state == fs.CandidateState.WATCH
    ][: cfg.deep_discernment_max]
    for c in watch_pool:
        # v1 deep discernment is a passthrough — the existing
        # opportunity score has already integrated pattern + edge
        # + regime. As Discernment Layer features accumulate
        # (from the postmortem work), this is where they'd land.
        fs.transition(
            c.symbol,
            to_state=fs.CandidateState.ARMED,
            reason="deep_discernment:promoted_from_watch",
        )

    # 5. Promoted / armed — cap at ``promoted_armed``. Anything
    # above the cap gets demoted back to WATCH so the strongest
    # candidates always occupy the armed slots.
    armed_ranked = [
        c for c in fs.rank_candidates()
        if c.state == fs.CandidateState.ARMED
    ]
    for i, c in enumerate(armed_ranked):
        if i >= cfg.promoted_armed:
            fs.transition(
                c.symbol,
                to_state=fs.CandidateState.WATCH,
                reason=f"exceeded_armed_cap:rank={i+1}",
            )

    # Persist so the 60s stream and restart both see this state.
    fs.persist_to_sqlite()

    summary = {
        "config": cfg.__dict__,
        "discovery_input": len(ranked_discovery or []),
        "survivors": len(survivors),
        "researched": len(research_summary),
        "armed": min(len(armed_ranked), cfg.promoted_armed),
        "research_summary": research_summary,
    }
    logger.info("[alpha_funnel] cycle summary %s", {
        k: v for k, v in summary.items() if k != "research_summary"
    })
    return summary


def get_promoted_symbols() -> list[str]:
    """The 60s stream reads this to decide who to re-evaluate."""
    from services import alpha_funnel_state as fs
    return [
        c.symbol for c in fs.rank_candidates()
        if c.state in (fs.CandidateState.ARMED, fs.CandidateState.ACTIONABLE)
    ]


__all__ = [
    "FunnelConfig", "get_config", "set_runtime_override",
    "run_funnel_cycle", "get_promoted_symbols",
]
