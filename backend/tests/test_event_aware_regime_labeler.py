"""Tests for the pure-labeling Event-Aware Regime Labeler.

Most importantly, this suite pins the **isolation contract**: the
labeler must never reach into the live decision stack, execution,
or memory writers (the four hard rules in the module docstring).
"""
from __future__ import annotations

import importlib

import pytest

from services.event_aware_regime_labeler import (
    EventAwareRegimeLabeler,
    InputRow,
    MARKET_EVENTS,
    RegimeLabel,
)


@pytest.fixture
def labeler() -> EventAwareRegimeLabeler:
    return EventAwareRegimeLabeler()


# ─── event tagging ───────────────────────────────────────────────────


def test_event_tag_known_dates(labeler):
    cases = [
        ("2008-10-15", "GFC_CRISIS"),
        ("2010-05-06", "FLASH_CRASH"),
        ("2010-05-07", "FLASH_CRASH"),
        ("2015-12-01", "CHINA_DEVALUATION"),
        ("2018-02-15", "VOL_SPIKE_2018"),
        ("2020-03-15", "COVID_CRASH"),
        ("2021-06-01", "COVID_RECOVERY"),
        ("2022-09-15", "RATE_HIKE_CYCLE"),
        ("2024-07-01", "AI_BUBBLE"),
    ]
    for date_str, expected in cases:
        assert labeler.tag_event(date_str) == expected, date_str


def test_event_tag_outside_any_window_returns_none(labeler):
    # Pre-GFC and a quiet 2017 stretch are outside every window.
    assert labeler.tag_event("2007-01-15") == "NONE"
    assert labeler.tag_event("2017-07-15") == "NONE"


def test_event_family_mapping(labeler):
    assert labeler.event_family("GFC_CRISIS") == "crash"
    assert labeler.event_family("COVID_CRASH") == "crash"
    assert labeler.event_family("COVID_RECOVERY") == "recovery"
    assert labeler.event_family("AI_BUBBLE") == "bubble"
    assert labeler.event_family("RATE_HIKE_CYCLE") == "tightening"
    assert labeler.event_family("VOL_SPIKE_2018") == "volatility_event"
    assert labeler.event_family("FLASH_CRASH") == "volatility_event"
    assert labeler.event_family("CHINA_DEVALUATION") == "macro_shock"
    assert labeler.event_family("NONE") == "normal"
    assert labeler.event_family("MADE_UP_EVENT") == "normal"


def test_only_gfc_and_covid_crash_are_crises(labeler):
    assert labeler.is_crisis("GFC_CRISIS") is True
    assert labeler.is_crisis("COVID_CRASH") is True
    for _, _, name in MARKET_EVENTS:
        if name in {"GFC_CRISIS", "COVID_CRASH"}:
            continue
        assert labeler.is_crisis(name) is False, name
    assert labeler.is_crisis("NONE") is False


# ─── regime tag boundaries ───────────────────────────────────────────


def test_vix_buckets(labeler):
    assert labeler.tag_vix(None) == "unknown"
    assert labeler.tag_vix(10.0) == "low"
    assert labeler.tag_vix(14.99) == "low"
    assert labeler.tag_vix(15.0) == "normal"
    assert labeler.tag_vix(19.99) == "normal"
    assert labeler.tag_vix(20.0) == "elevated"
    assert labeler.tag_vix(29.99) == "elevated"
    assert labeler.tag_vix(30.0) == "extreme"
    assert labeler.tag_vix(80.0) == "extreme"


def test_yield_curve_buckets(labeler):
    assert labeler.tag_yield_curve(None, 1.0) == "unknown"
    assert labeler.tag_yield_curve(1.0, None) == "unknown"
    # spread > 1 → steep
    assert labeler.tag_yield_curve(4.5, 3.0) == "steep"
    # 0 < spread <= 1 → flat
    assert labeler.tag_yield_curve(4.5, 4.0) == "flat"
    assert labeler.tag_yield_curve(4.5, 4.5) == "inverted"
    # spread <= 0 → inverted
    assert labeler.tag_yield_curve(3.5, 4.5) == "inverted"


def test_liquidity_buckets(labeler):
    assert labeler.tag_liquidity(None, 1000.0) == "unknown"
    assert labeler.tag_liquidity(1000.0, None) == "unknown"
    assert labeler.tag_liquidity(1000.0, 0) == "unknown"
    assert labeler.tag_liquidity(500.0, 1000.0) == "thin"   # ratio 0.5
    assert labeler.tag_liquidity(800.0, 1000.0) == "normal"  # ratio 0.8
    assert labeler.tag_liquidity(1300.0, 1000.0) == "normal"  # ratio 1.3 (boundary)
    assert labeler.tag_liquidity(1500.0, 1000.0) == "high"  # ratio 1.5


def test_macro_phase_buckets(labeler):
    assert labeler.tag_macro_phase(None) == "unknown"
    assert labeler.tag_macro_phase(2500.0) == "contraction"
    assert labeler.tag_macro_phase(3500.0) == "late_cycle"
    assert labeler.tag_macro_phase(5000.0) == "expansion"


# ─── regime_id is deterministic ──────────────────────────────────────


def test_regime_id_is_deterministic(labeler):
    row = InputRow(
        date="2024-07-01", vix=18.0, ten_year=4.5, two_year=4.7,
        volume=1000.0, avg_volume=900.0, sp500=5400.0,
    )
    a = labeler.label_row(row)
    b = labeler.label_row(row)
    assert a.regime_id == b.regime_id
    assert isinstance(a.regime_id, str)
    assert len(a.regime_id) == 16


def test_regime_id_differs_when_inputs_differ(labeler):
    row_a = InputRow(
        date="2020-03-15", vix=70.0, ten_year=0.6, two_year=0.4,
        volume=2000.0, avg_volume=1000.0, sp500=2400.0,
    )
    row_b = InputRow(
        date="2024-07-01", vix=14.0, ten_year=4.5, two_year=4.7,
        volume=900.0, avg_volume=900.0, sp500=5400.0,
    )
    label_a = labeler.label_row(row_a)
    label_b = labeler.label_row(row_b)
    assert label_a.regime_id != label_b.regime_id


def test_label_row_returns_full_envelope(labeler):
    row = InputRow(
        date="2020-03-15", vix=70.0, ten_year=0.6, two_year=0.4,
        volume=2000.0, avg_volume=1000.0, sp500=2400.0,
    )
    label = labeler.label_row(row)
    assert isinstance(label, RegimeLabel)
    assert label.market_event == "COVID_CRASH"
    assert label.event_family == "crash"
    assert label.is_crisis is True
    assert label.vix_level == "extreme"
    assert label.yield_curve == "flat"  # spread 0.2 → 0 < spread <= 1 → flat
    assert label.liquidity == "high"
    assert label.macro_phase == "contraction"


# ─── batch labeling ──────────────────────────────────────────────────


def test_label_dataset_preserves_input_fields_and_adds_labels(labeler):
    rows = [
        InputRow(date="2017-07-15", vix=11.0, ten_year=2.4, two_year=1.4,
                 volume=900.0, avg_volume=1000.0, sp500=2470.0),
        InputRow(date="2020-03-15", vix=70.0, ten_year=0.6, two_year=0.4,
                 volume=2500.0, avg_volume=1000.0, sp500=2400.0),
    ]
    out = labeler.label_dataset(rows)
    assert len(out) == 2
    for original, labeled in zip(rows, out):
        # Original fields survive.
        assert labeled["date"] == original.date
        assert labeled["vix"] == original.vix
        # New labels are present.
        for key in (
            "regime_label", "vix_level", "yield_curve", "liquidity",
            "market_event", "event_family", "is_crisis", "regime_id",
        ):
            assert key in labeled

    # First row is outside any event window, second is COVID_CRASH.
    assert out[0]["market_event"] == "NONE"
    assert out[0]["is_crisis"] is False
    assert out[1]["market_event"] == "COVID_CRASH"
    assert out[1]["is_crisis"] is True


def test_label_dataset_handles_empty_input(labeler):
    assert labeler.label_dataset([]) == []


def test_partial_input_row_yields_unknown_tags(labeler):
    """Missing inputs must surface as ``unknown`` — never silently
    default to a regime bucket."""
    row = InputRow(date="2017-07-15")  # all numeric fields None
    label = labeler.label_row(row)
    assert label.vix_level == "unknown"
    assert label.yield_curve == "unknown"
    assert label.liquidity == "unknown"
    assert label.macro_phase == "unknown"
    assert label.market_event == "NONE"


# ─── ISOLATION CONTRACT ──────────────────────────────────────────────


def test_module_does_not_import_decision_or_execution_layers():
    """Hard rule: this is a pure labeler. It must not reach into the
    live decision stack, execution, risk modulation, or memory writers.
    """
    import services.event_aware_regime_labeler as mod
    src = open(mod.__file__).read()
    forbidden_imports = [
        "from services.prediction_tracker",
        "from services.confidence_gate",
        "from services.conviction_service",
        "from services.commander_decision_stream",
        "from services.commander_phase2_brake",
        "from services.council_risk_modulator",
        "from services.regime_memory_retrieval",
        "from services.adversarial_enforcer",
        "from services.adversarial_promotion_gate",
        "from services.broker_service",
        "from services.crypto_paper_trader",
        "from services.paper_trading",  # any paper trading service
        "from routes.options_trading",
        "from routes.broker",
        "from routes.trading",
        "from motor",         # no Mongo handle
        "from pymongo",       # no Mongo handle
        "AsyncIOMotor",
    ]
    for token in forbidden_imports:
        assert token not in src, (
            f"forbidden import found in pure labeler: {token}"
        )


def test_module_performs_no_collection_writes():
    """Static check — no insert/update/upsert call against any
    Mongo-style collection. Looks for actual call patterns rather
    than mere mentions, so the docstring listing forbidden
    collections doesn't cause a false positive.
    """
    import services.event_aware_regime_labeler as mod
    src = open(mod.__file__).read()
    write_patterns = [
        ".insert_one(",
        ".insert_many(",
        ".update_one(",
        ".update_many(",
        ".replace_one(",
        ".delete_one(",
        ".delete_many(",
        ".find_one_and_update(",
    ]
    for token in write_patterns:
        assert token not in src, (
            f"DB write pattern found in pure labeler: {token}"
        )


def test_module_can_be_imported_in_isolation():
    """The labeler must import cleanly without pulling motor / fastapi
    / decision-stack modules into the test environment."""
    importlib.import_module("services.event_aware_regime_labeler")
