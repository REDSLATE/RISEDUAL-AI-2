"""Alpha candidate -> Sovereign feature contract. No invented market facts.

Inject a verified Alpha feature builder which returns SovereignFeatures with
real timestamps, spreads, volume, technical inputs and setup R:R. Missing
required inputs cause Sovereign HOLD; the caller cannot fill with zeroes.
"""
from __future__ import annotations
from typing import Awaitable, Callable
from services.alpha_core_v2.contracts import Candidate
from services.alpha_core_v2.sovereign import SovereignFeatures, SovereignDecision, evaluate

FeatureBuilder = Callable[[Candidate], Awaitable[SovereignFeatures]]

class SovereignGate:
    def __init__(self, build_features: FeatureBuilder):
        self.build_features = build_features

    async def __call__(self, candidate: Candidate) -> SovereignDecision:
        features = await self.build_features(candidate)
        if features.symbol.upper() != candidate.symbol.upper():
            raise ValueError('candidate/feature symbol mismatch')
        return evaluate(features)
