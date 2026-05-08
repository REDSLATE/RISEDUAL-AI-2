"""Train + persist .joblib artifacts for the 8-ML stack.

Run from /app/backend:

    python3 -m scripts.train_ml_artifacts \\
        --out /app/artifacts/ml \\
        --seed 4242

Produces:
    perception/event_shock.joblib
    perception/regime_state.joblib
    perception/drawdown_distance.joblib
    perception/liquidity.joblib
    perception/system_health.joblib
    perception/pacing.joblib
    strategist/model.joblib
    auditor/model.joblib

Set the matching env vars in .env to wire them in:
    PERCEPTION_EVENT_SHOCK_ARTIFACT=/app/artifacts/ml/perception/event_shock.joblib
    ...
    STRATEGIST_ARTIFACT=/app/artifacts/ml/strategist/model.joblib
    AUDITOR_ARTIFACT=/app/artifacts/ml/auditor/model.joblib

The artifacts produced here use the same synthetic-realistic training
data the in-process placeholders bootstrap. The point is not "real"
models in the ML-research sense — it's stable, persisted artifacts so
the pipeline boots from disk like production. When you have labeled
receipt data from Phase 5a / Camaro, replace this script's training
data builder and re-run.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import joblib

logger = logging.getLogger(__name__)


def _train_perception(out_dir: Path, seed: int) -> int:
    """Train + save the 6 perception sub-models. Returns count saved."""
    from services.ml.perception.models import (
        DrawdownDistanceModel,
        EventShockModel,
        LiquidityModel,
        PacingModel,
        RegimeStateModel,
        SystemHealthModel,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = [
        ("event_shock",        EventShockModel),
        ("regime_state",       RegimeStateModel),
        ("drawdown_distance",  DrawdownDistanceModel),
        ("liquidity",          LiquidityModel),
        ("system_health",      SystemHealthModel),
        ("pacing",             PacingModel),
    ]
    saved = 0
    for name, cls in pairs:
        path = out_dir / f"{name}.joblib"
        try:
            inst = cls()
            joblib.dump(inst._model, path)
            print(f"saved {path}")
            saved += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED {name}: {exc}", file=sys.stderr)
    return saved


def _train_strategist(out_dir: Path, seed: int) -> bool:
    from services.ml.strategist.base import _train_placeholder
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "model.joblib"
    try:
        clf = _train_placeholder(seed=seed)
        joblib.dump(clf, path)
        print(f"saved {path}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED strategist: {exc}", file=sys.stderr)
        return False


def _train_auditor(out_dir: Path, seed: int) -> bool:
    from services.ml.auditor.base import _train_placeholder
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "model.joblib"
    try:
        clf = _train_placeholder(seed=seed)
        joblib.dump(clf, path)
        print(f"saved {path}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED auditor: {exc}", file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/app/artifacts/ml",
                    help="Output root directory")
    ap.add_argument("--seed", type=int, default=4242)
    args = ap.parse_args()

    root = Path(args.out)
    print(f"[train_ml_artifacts] writing to {root}")

    perception_count = _train_perception(root / "perception", args.seed)
    s_ok = _train_strategist(root / "strategist", args.seed)
    a_ok = _train_auditor(root / "auditor", args.seed)

    total = perception_count + (1 if s_ok else 0) + (1 if a_ok else 0)
    print(f"[train_ml_artifacts] saved {total} artifacts under {root}")
    print("[train_ml_artifacts] env vars to wire in .env:")
    print(f"  PERCEPTION_EVENT_SHOCK_ARTIFACT={root}/perception/event_shock.joblib")
    print(f"  PERCEPTION_REGIME_STATE_ARTIFACT={root}/perception/regime_state.joblib")
    print(f"  PERCEPTION_DRAWDOWN_ARTIFACT={root}/perception/drawdown_distance.joblib")
    print(f"  PERCEPTION_LIQUIDITY_ARTIFACT={root}/perception/liquidity.joblib")
    print(f"  PERCEPTION_SYSTEM_HEALTH_ARTIFACT={root}/perception/system_health.joblib")
    print(f"  PERCEPTION_PACING_ARTIFACT={root}/perception/pacing.joblib")
    print(f"  STRATEGIST_ARTIFACT={root}/strategist/model.joblib")
    print(f"  AUDITOR_ARTIFACT={root}/auditor/model.joblib")


if __name__ == "__main__":
    main()
