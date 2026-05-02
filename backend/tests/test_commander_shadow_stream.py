"""
Tests for the Commander Shadow per-market JSONL mirror + equity
shadow promotion gate.

The mirror is developer-ergonomics — Mongo stays authoritative.
The promotion gate is *consequential* — it decides when Commander
graduates from observation to pre-Tier-3 brake authority. Both
are pinned here so the invariants don't silently drift.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services import commander_decision_stream as cds
from services.equity_shadow_promotion import (
    compute_equity_shadow_promotion_status,
    MIN_SCORED_ROWS,
    MIN_WIN_RATE,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def tmp_stream_dir(tmp_path: Path, monkeypatch):
    """Redirect the JSONL mirror to a pytest tmp dir so tests don't
    touch the real /app/backend/data tree."""
    monkeypatch.setattr(cds, "COMMANDER_DECISIONS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def sample_decision():
    """A minimal ShadowDecision-shaped dict. Keeping it a dict instead
    of the dataclass keeps the test focused on the mirror's own
    serialization rules."""
    return {
        "bot_id": "equity_ml_orchestrator",
        "symbol": "NVDA",
        "asset_type": "stock",
        "shadow_engine": "adversarial",
        "active_action": "LONG",
        "shadow_action": "SHORT",
        "shadow_confidence": 0.82,
        "mid_price": 180.50,
        "decision_phase": "cycle",
        "is_dissent": True,
    }


# ── Mirror: append + layout ──────────────────────────────────────


def test_append_writes_to_correct_per_market_file(tmp_stream_dir, sample_decision):
    """stock → stocks.jsonl, crypto → crypto.jsonl, options →
    options.jsonl. Unknown asset_types are dropped (False return)
    and never routed to a default file."""
    assert cds.append_commander_decision(sample_decision, "stock") is True
    assert cds.append_commander_decision({**sample_decision, "asset_type": "crypto"}, "crypto") is True
    assert cds.append_commander_decision({**sample_decision, "asset_type": "options"}, "options") is True

    # Unknown type — must NOT create a fallback file.
    assert cds.append_commander_decision(sample_decision, "forex") is False

    assert (tmp_stream_dir / "stocks.jsonl").exists()
    assert (tmp_stream_dir / "crypto.jsonl").exists()
    assert (tmp_stream_dir / "options.jsonl").exists()
    assert not (tmp_stream_dir / "forex.jsonl").exists()


def test_appended_row_is_valid_jsonl_and_preserves_fields(tmp_stream_dir, sample_decision):
    cds.append_commander_decision(sample_decision, "stock")
    raw = (tmp_stream_dir / "stocks.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(raw) == 1
    row = json.loads(raw[0])
    # Sanity: original fields are intact.
    assert row["symbol"] == "NVDA"
    assert row["shadow_action"] == "SHORT"
    assert row["shadow_confidence"] == 0.82
    # Mirror stamp is added for grep-ability.
    assert "mirrored_at" in row


def test_append_survives_multiple_writes_same_file(tmp_stream_dir, sample_decision):
    """Each append is a new line, no row overwriting."""
    for conf in (0.60, 0.75, 0.82):
        cds.append_commander_decision({**sample_decision, "shadow_confidence": conf}, "stock")
    lines = (tmp_stream_dir / "stocks.jsonl").read_text().strip().split("\n")
    assert len(lines) == 3
    confidences = [json.loads(line)["shadow_confidence"] for line in lines]
    assert confidences == [0.60, 0.75, 0.82]


def test_read_recent_tails_newest_rows(tmp_stream_dir, sample_decision):
    for symbol in ("A", "B", "C", "D", "E"):
        cds.append_commander_decision({**sample_decision, "symbol": symbol}, "stock")
    rows = cds.read_recent_decisions("stock", limit=3)
    assert [r["symbol"] for r in rows] == ["C", "D", "E"]


def test_read_recent_empty_asset_returns_empty_list(tmp_stream_dir):
    assert cds.read_recent_decisions("crypto", limit=10) == []
    # Unknown asset too — no crash.
    assert cds.read_recent_decisions("forex", limit=10) == []


# ── Promotion gate: Phase 1 vs Phase 2 ───────────────────────────


class _FakeCollection:
    """Minimal stand-in supporting only ``count_documents``."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    async def count_documents(self, query: dict[str, Any]) -> int:
        def _match(doc: dict[str, Any]) -> bool:
            for k, v in query.items():
                if isinstance(v, dict):
                    # Only handle the ``$exists`` operator — it's the
                    # only nested filter this function uses.
                    if "$exists" in v:
                        # Support dotted key (e.g. "tactical_score.shadow_was_right").
                        path = k.split(".")
                        cur: Any = doc
                        for part in path:
                            if isinstance(cur, dict) and part in cur:
                                cur = cur[part]
                            else:
                                cur = ...
                                break
                        present = cur is not ...
                        if v["$exists"] != present:
                            return False
                else:
                    path = k.split(".")
                    cur = doc
                    for part in path:
                        if isinstance(cur, dict) and part in cur:
                            cur = cur[part]
                        else:
                            return False
                    if cur != v:
                        return False
            return True
        return sum(1 for d in self._rows if _match(d))


class _FakeDB:
    def __init__(self, rows: list[dict[str, Any]]):
        self.research_shadow_decisions = _FakeCollection(rows)


def _equity_row(shadow_was_right: bool | None = None) -> dict[str, Any]:
    row = {
        "bot_id": "equity_ml_orchestrator",
        "asset_type": "stock",
        "shadow_engine": "adversarial",
    }
    if shadow_was_right is not None:
        row["tactical_score"] = {"shadow_was_right": shadow_was_right}
    return row


@pytest.mark.asyncio
async def test_promotion_status_phase_1_when_no_rows():
    db = _FakeDB(rows=[])
    status = await compute_equity_shadow_promotion_status(db)
    assert status["phase"] == "phase_1_logging_only"
    assert status["rows_scored"] == 0
    assert status["rows_to_go"] == MIN_SCORED_ROWS
    assert status["win_rate"] is None
    assert status["brake_eligible"] is False
    assert "need" in (status["blocker"] or "")


@pytest.mark.asyncio
async def test_promotion_status_phase_1_when_rows_below_threshold():
    # 40 scored rows, all wins — insufficient rows blocks even a 100% win rate.
    rows = [_equity_row(True) for _ in range(40)]
    db = _FakeDB(rows=rows)
    status = await compute_equity_shadow_promotion_status(db)
    assert status["phase"] == "phase_1_logging_only"
    assert status["rows_scored"] == 40
    assert status["win_rate"] == 1.0
    assert status["brake_eligible"] is False


@pytest.mark.asyncio
async def test_promotion_status_phase_1_when_rate_below_threshold():
    # 80 scored rows — 40 right, 40 wrong (50%). Plenty of rows,
    # but well under the 70% bar.
    rows = [_equity_row(True)] * 40 + [_equity_row(False)] * 40
    db = _FakeDB(rows=rows)
    status = await compute_equity_shadow_promotion_status(db)
    assert status["phase"] == "phase_1_logging_only"
    assert status["rows_scored"] == 80
    assert status["win_rate"] == 0.5
    assert status["brake_eligible"] is False
    assert "win_rate" in (status["blocker"] or "")


@pytest.mark.asyncio
async def test_promotion_status_phase_2_when_both_gates_clear():
    # 60 rows, 50 right (83.3%) — clears both gates.
    rows = [_equity_row(True)] * 50 + [_equity_row(False)] * 10
    db = _FakeDB(rows=rows)
    status = await compute_equity_shadow_promotion_status(db)
    assert status["phase"] == "phase_2_brake_eligible"
    assert status["rows_scored"] == 60
    assert status["win_rate"] == round(50 / 60, 3)
    assert status["win_rate"] >= MIN_WIN_RATE
    assert status["brake_eligible"] is True
    assert status["blocker"] is None


@pytest.mark.asyncio
async def test_promotion_status_ignores_non_equity_shadows():
    """Crypto shadows must not count toward the equity gate — that
    would conflate two independent learning paths."""
    crypto_rows = [
        {
            "bot_id": "crypto_fleet",
            "asset_type": "crypto",
            "shadow_engine": "adversarial",
            "tactical_score": {"shadow_was_right": True},
        }
    ] * 200
    db = _FakeDB(rows=crypto_rows)
    status = await compute_equity_shadow_promotion_status(db)
    assert status["rows_total"] == 0
    assert status["rows_scored"] == 0
    assert status["brake_eligible"] is False
