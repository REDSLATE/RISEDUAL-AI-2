"""One-shot Alpha weight seeder. No-op if state already exists.

Run before first start of the sidecar (the sidecar also self-seeds, but
running this script gives the operator an explicit visible log line):

    python3 -m backend.sovereign.bootstrap_alpha
"""
from __future__ import annotations

import os
import sys

from .local_state import LocalState
from .sidecar import ALPHA_INITIAL_LR, ALPHA_INITIAL_WEIGHTS


def main() -> int:
    path = os.environ.get(
        "SOVEREIGN_STATE_PATH", "/app/data/sovereign/alpha/state.json"
    )
    s = LocalState(brain="alpha", path=path, mode="DTD")
    if not s.weights:
        s.set_weights(ALPHA_INITIAL_WEIGHTS)
        s.set_learning_rate(ALPHA_INITIAL_LR)
        s.save()
        print(f"seeded alpha weights at {path}: {ALPHA_INITIAL_WEIGHTS}")
        return 0
    print(
        f"alpha state already exists at {path}; not reseeding "
        f"(existing weights: {s.weights}, lr={s.learning_rate}, mode={s.mode})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
