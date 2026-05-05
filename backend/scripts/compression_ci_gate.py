"""RISEDUAL AI — Compression CI Gate (READ-ONLY).

Compares a candidate (quantized / pruned / distilled) model_tag's
realised calibration against a baseline model_tag's realised
calibration, using the existing ``RegimePerformanceTracker`` +
``EventAwareRegimeLabeler`` analytics pipeline.

CRITICAL CONTRACT
-----------------
* Pure analytics. No writes. No memory promotion. No sizing.
* Never imported by the live decision stack.
* Both tags must already exist in ``predictions`` (stamped at insert
  time by ``services.prediction_tracker.log_prediction``).

OUTCOMES
--------
* PASS         — exit 0
* FAIL         — exit 1 (only when ``--fail-on-breach`` is set; else 0)
* INCONCLUSIVE — exit 2 (insufficient samples; never a green light)

The two-tier guardrail (per-family + weighted aggregate) catches
regime-specific overfit AND broad calibration drift.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from services.event_aware_regime_labeler import EventAwareRegimeLabeler, InputRow
from services.regime_performance_tracker import RegimePerformanceTracker, ResolvedTradeRow


GUARDRAIL_CAL_GAP_ABS = 0.04
GUARDRAIL_WEIGHTED_GAP_ABS = 0.02
MIN_RESOLVED_FOR_VERDICT = 30
MIN_FAMILY_SAMPLES = 10


@dataclass
class GateVerdict:
    ok: bool
    inconclusive: bool
    breaches: list[str]
    baseline_summary: dict[str, Any]
    candidate_summary: dict[str, Any]


async def _load_resolved(db, model_tag: str, since: datetime) -> list[dict]:
    cursor = db.predictions.find(
        {
            "model_tag": model_tag,
            "verified_24h.correct": {"$in": [True, False]},
            "timestamp": {"$gte": since},
        },
        {
            "_id": 0,
            "symbol": 1,
            "timestamp": 1,
            "action": 1,
            "confidence": 1,
            "verified_24h.correct": 1,
            "verified_24h.pnl_pct": 1,
            "macro": 1,
        },
    )
    return await cursor.to_list(length=20_000)


def _date_str(ts: Any) -> str:
    if isinstance(ts, datetime):
        return ts.date().isoformat()
    if isinstance(ts, str):
        return ts[:10]
    return "1970-01-01"


def _to_resolved_rows(raw: list[dict]) -> list[ResolvedTradeRow]:
    labeler = EventAwareRegimeLabeler()
    rows: list[ResolvedTradeRow] = []

    for r in raw:
        macro = r.get("macro") or {}
        date = _date_str(r.get("timestamp"))

        label = labeler.label_row(
            InputRow(
                date=date,
                vix=macro.get("vix"),
                ten_year=macro.get("ten_year"),
                two_year=macro.get("two_year"),
                volume=macro.get("volume"),
                avg_volume=macro.get("avg_volume"),
                sp500=macro.get("sp500"),
            )
        )

        verified = r.get("verified_24h") or {}

        rows.append(
            ResolvedTradeRow(
                symbol=r.get("symbol", "?"),
                date=date,
                action=r.get("action", "?"),
                confidence=r.get("confidence"),
                win=verified.get("correct"),
                pnl_pct=verified.get("pnl_pct"),
                regime_label=label.macro_phase,
                vix_level=label.vix_level,
                yield_curve=label.yield_curve,
                liquidity=label.liquidity,
                market_event=label.market_event,
                event_family=label.event_family,
                is_crisis=label.is_crisis,
                regime_id=label.regime_id,
            )
        )

    return rows


def _weighted_avg_calibration_gap(report: dict) -> float | None:
    """Weighted-by-samples mean of |calibration_gap| over event families.

    Buckets below ``MIN_FAMILY_SAMPLES`` are excluded — same floor used by
    the per-family check so a tiny noisy bucket can't poison the
    aggregate either.
    """
    families = report["reports"]["by_event_family"]

    observed = [
        bucket
        for bucket in families.values()
        if bucket.get("calibration_gap") is not None
        and bucket.get("samples", 0) >= MIN_FAMILY_SAMPLES
    ]

    if not observed:
        return None

    total = sum(bucket["samples"] for bucket in observed)
    if total <= 0:
        return None

    return sum(
        (bucket["samples"] / total) * abs(bucket["calibration_gap"])
        for bucket in observed
    )


def _sample_count(report: dict) -> int:
    return sum(
        bucket.get("samples", 0)
        for bucket in report["reports"]["by_event_family"].values()
    )


def evaluate_gate(
    baseline: list[ResolvedTradeRow],
    candidate: list[ResolvedTradeRow],
) -> GateVerdict:
    if len(baseline) < MIN_RESOLVED_FOR_VERDICT:
        return GateVerdict(
            ok=False,
            inconclusive=True,
            breaches=[
                f"baseline has only {len(baseline)} resolved predictions "
                f"(<{MIN_RESOLVED_FOR_VERDICT})"
            ],
            baseline_summary={},
            candidate_summary={},
        )

    if len(candidate) < MIN_RESOLVED_FOR_VERDICT:
        return GateVerdict(
            ok=False,
            inconclusive=True,
            breaches=[
                f"candidate has only {len(candidate)} resolved predictions "
                f"(<{MIN_RESOLVED_FOR_VERDICT})"
            ],
            baseline_summary={},
            candidate_summary={},
        )

    tracker = RegimePerformanceTracker()
    base_report = tracker.full_report(baseline)
    cand_report = tracker.full_report(candidate)

    breaches: list[str] = []

    base_families = base_report["reports"]["by_event_family"]
    cand_families = cand_report["reports"]["by_event_family"]

    for family, cand_bucket in cand_families.items():
        base_bucket = base_families.get(family)
        if not base_bucket:
            continue

        if base_bucket.get("samples", 0) < MIN_FAMILY_SAMPLES:
            continue

        if cand_bucket.get("samples", 0) < MIN_FAMILY_SAMPLES:
            continue

        base_gap = base_bucket.get("calibration_gap")
        cand_gap = cand_bucket.get("calibration_gap")

        if base_gap is None or cand_gap is None:
            continue

        delta = abs(cand_gap) - abs(base_gap)

        if delta > GUARDRAIL_CAL_GAP_ABS:
            breaches.append(
                f"calibration_gap[{family}] worsened "
                f"{base_gap:+.3f} -> {cand_gap:+.3f} "
                f"(delta={delta:+.3f}, limit={GUARDRAIL_CAL_GAP_ABS})"
            )

    base_weighted_gap = _weighted_avg_calibration_gap(base_report)
    cand_weighted_gap = _weighted_avg_calibration_gap(cand_report)

    if base_weighted_gap is not None and cand_weighted_gap is not None:
        delta = cand_weighted_gap - base_weighted_gap

        if delta > GUARDRAIL_WEIGHTED_GAP_ABS:
            breaches.append(
                f"weighted_avg_calibration_gap worsened "
                f"{base_weighted_gap:.4f} -> {cand_weighted_gap:.4f} "
                f"(delta={delta:+.4f}, limit={GUARDRAIL_WEIGHTED_GAP_ABS})"
            )

    return GateVerdict(
        ok=len(breaches) == 0,
        inconclusive=False,
        breaches=breaches,
        baseline_summary={
            "samples": _sample_count(base_report),
            "weighted_avg_calibration_gap": base_weighted_gap,
        },
        candidate_summary={
            "samples": _sample_count(cand_report),
            "weighted_avg_calibration_gap": cand_weighted_gap,
        },
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-tag", required=False)
    parser.add_argument("--candidate-tag", required=False)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--fail-on-breach", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print("dry-run: compression gate loaded; no DB read performed.")
        sys.exit(0)

    if not args.baseline_tag or not args.candidate_tag:
        print(
            "error: --baseline-tag and --candidate-tag required "
            "(or pass --dry-run)",
            file=sys.stderr,
        )
        sys.exit(64)

    load_dotenv("/app/backend/.env")

    mongo_url = os.getenv("MONGO_URL")
    db_name = os.getenv("DB_NAME")

    if not mongo_url or not db_name:
        print("error: MONGO_URL and DB_NAME required", file=sys.stderr)
        sys.exit(64)

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    since = datetime.now(timezone.utc) - timedelta(days=args.window_days)

    baseline_raw = await _load_resolved(db, args.baseline_tag, since)
    candidate_raw = await _load_resolved(db, args.candidate_tag, since)

    verdict = evaluate_gate(
        _to_resolved_rows(baseline_raw),
        _to_resolved_rows(candidate_raw),
    )

    print(f"baseline:  {verdict.baseline_summary}")
    print(f"candidate: {verdict.candidate_summary}")
    print()

    if verdict.inconclusive:
        print("VERDICT: INCONCLUSIVE")
        for breach in verdict.breaches:
            print(f"  - {breach}")
        sys.exit(2)

    if verdict.ok:
        print("VERDICT: PASS")
        sys.exit(0)

    print("VERDICT: FAIL")
    for breach in verdict.breaches:
        print(f"  - {breach}")

    sys.exit(1 if args.fail_on_breach else 0)


if __name__ == "__main__":
    asyncio.run(main())
