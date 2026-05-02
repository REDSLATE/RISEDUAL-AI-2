"""Options flow-score ranker.

Score formula (user spec):

    score = log(max(volume, 1)) + 2.0 * (volume / open_interest) - 0.01 * spread_bps

Term rationale:
    * ``log(volume)``      — rewards raw participation without letting
                             one 50k-volume print dominate a list of
                             strong-but-not-unbelievable contracts.
    * ``2.0 * vol/OI``     — heavily weights net-new positioning over
                             roll activity. A ratio > 1 means today's
                             trades exceeded resting OI — rare and
                             directionally meaningful.
    * ``-0.01 * spread_bps`` — mild penalty so two otherwise-equal
                               contracts prefer the tighter market.
                               Kept small so it never flips ranking
                               between a thin-but-active flow trade
                               and a wide no-flow contract.

Imports kept to stdlib so this module can be pulled into any decision
path (conviction, commander, adversarial core) without pulling half
the service graph.
"""
from __future__ import annotations

import math


def compute_flow_score(contract: dict) -> float:
    """Score a single contract. Higher is "more institutional intent"."""
    volume = contract.get("volume", 0) or 0
    oi = contract.get("open_interest", 0) or 0
    spread_bps = contract.get("spread_bps", 100) or 100

    volume_oi_ratio = volume / max(oi, 1)

    return round(
        math.log(max(volume, 1))
        + 2.0 * volume_oi_ratio
        - 0.01 * float(spread_bps),
        4,
    )


def rank_contracts(contracts: list[dict], top_n: int = 10) -> list[dict]:
    """Attach ``flow_score`` to each contract and return the top ``top_n``
    (descending). Input list is not mutated — we shallow-copy every
    contract so callers' views of the raw chain stay clean.
    """
    scored: list[dict] = []
    for c in contracts:
        enriched = dict(c)
        enriched["flow_score"] = compute_flow_score(enriched)
        scored.append(enriched)

    scored.sort(key=lambda x: x["flow_score"], reverse=True)
    return scored[:top_n]
