"""One-shot Alpha weight seeder. No-op if state already exists.

Run before first start of the sidecar (the sidecar self-seeds too,
but running this script gives the operator an explicit log line).

    python3 -m sovereign.bootstrap_alpha
"""
from __future__ import annotations

import os
import sys

from .local_state import LocalState
from .wild_adaptive_core_v2 import default_weights

ALPHA_INITIAL_LR = 0.06


def main() -> int:
    path = os.environ.get(
        "SOVEREIGN_STATE_PATH", "/app/data/sovereign/alpha/state.json"
    )
    s = LocalState(brain="alpha", path=path, mode="DTD")
    if not s.weights:
        weights = default_weights()
        s.set_weights(weights)
        s.set_learning_rate(ALPHA_INITIAL_LR)
        s.save()
        print(f"seeded alpha weights at {path}: {weights}")
        return 0
    print(
        f"alpha state already exists at {path}; not reseeding "
        f"(existing weights: {s.weights}, lr={s.learning_rate}, mode={s.mode})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
