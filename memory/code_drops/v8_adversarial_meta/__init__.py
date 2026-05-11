"""v8 adversarial meta-target primitives — RISEDUAL-ready.

Drop-in package: copy this folder into any stack and import directly::

    from v8_adversarial_meta import (
        build_verified_veto_target,
        build_challenger_features,
        majority_vote,
        PENDING, RESOLVED, ERRORED, EXPIRED,
        VALID_STATUSES, NON_TRAINABLE_STATUSES,
    )

Zero dependencies beyond numpy. No sklearn, no pandas, no Mongo.

See ``README.md`` for the nine-invariant doctrine and integration
protocol.
"""
from __future__ import annotations

from .adversarial_meta_target import (
    ERRORED,
    EXPIRED,
    NON_TRAINABLE_STATUSES,
    PENDING,
    RESOLVED,
    VALID_STATUSES,
    build_verified_veto_target,
    normalize_statuses,
)
from .challenger_features import build_challenger_features
from .vote_utils import majority_vote


__all__ = [
    # Status enum
    "PENDING",
    "RESOLVED",
    "ERRORED",
    "EXPIRED",
    "VALID_STATUSES",
    "NON_TRAINABLE_STATUSES",
    # Label construction
    "normalize_statuses",
    "build_verified_veto_target",
    # Feature assembly
    "build_challenger_features",
    # Voting
    "majority_vote",
]


__version__ = "8.0.0"
