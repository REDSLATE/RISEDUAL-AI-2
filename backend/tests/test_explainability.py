"""Tests for `ai_core.explainability.extract_top_features`.

Pins contract behavior:
  * NaN / non-numeric values silently dropped
  * zero-importance features dropped (not ranked as ties)
  * direction reflects sign of contribution, not feature value
  * ranking by |impact|, stable, top_k respected
  * never-raise — all error paths return [] not exceptions
"""
from __future__ import annotations

import pytest

from ai_core.explainability import extract_top_features


def test_basic_ranking_by_absolute_impact():
    """Top features sorted by |value × importance|, top_k rows."""
    names = ["momentum_14", "rsi_14", "atr_10"]
    values = [1.5, 0.5, -2.0]
    importances = {"momentum_14": 0.30, "rsi_14": 0.10, "atr_10": 0.05}
    # impacts: 0.45, 0.05, -0.10 → ranked by abs: 0.45, 0.10, 0.05
    out = extract_top_features(names, values, importances, top_k=3)
    assert [r["feature"] for r in out] == ["momentum_14", "atr_10", "rsi_14"]
    assert out[0]["direction"] == "bullish"
    assert out[1]["direction"] == "bearish"  # value -2.0 × +imp = negative
    assert out[2]["direction"] == "bullish"


def test_top_k_caps_output():
    """top_k=2 returns exactly 2 rows even when more candidates exist."""
    names = ["a", "b", "c", "d"]
    values = [1.0, 2.0, 3.0, 4.0]
    importances = {"a": 0.1, "b": 0.1, "c": 0.1, "d": 0.1}
    out = extract_top_features(names, values, importances, top_k=2)
    assert len(out) == 2
    assert out[0]["feature"] == "d"  # largest impact 0.4
    assert out[1]["feature"] == "c"


def test_zero_importance_features_dropped():
    """Features the model never used (imp=0) are not ranked. This
    prevents "lucky alignment" noise from appearing in the why."""
    names = ["used", "unused"]
    values = [0.5, 100.0]  # unused has huge value but no importance
    importances = {"used": 0.2, "unused": 0.0}
    out = extract_top_features(names, values, importances)
    assert len(out) == 1
    assert out[0]["feature"] == "used"


def test_missing_importance_defaults_to_zero_and_drops():
    """Feature name absent from importances dict → treated as
    unused → dropped."""
    out = extract_top_features(
        ["a", "b"], [1.0, 1.0], {"a": 0.5},  # only a is in importances
    )
    assert len(out) == 1
    assert out[0]["feature"] == "a"


def test_nan_value_dropped_silently():
    """A NaN feature shouldn't poison the row — drop it."""
    out = extract_top_features(
        ["a", "b"], [float("nan"), 2.0], {"a": 0.5, "b": 0.3},
    )
    assert len(out) == 1
    assert out[0]["feature"] == "b"


def test_infinity_value_dropped():
    """Inf is pathological data; drop rather than let it dominate."""
    out = extract_top_features(
        ["a", "b"], [float("inf"), 1.0], {"a": 0.5, "b": 0.1},
    )
    assert len(out) == 1
    assert out[0]["feature"] == "b"


def test_non_numeric_value_dropped():
    """String from a dirty snapshot — drop silently."""
    out = extract_top_features(
        ["a", "b"], ["bad", 1.0], {"a": 0.5, "b": 0.1},
    )  # type: ignore[list-item]
    assert len(out) == 1


def test_none_value_dropped():
    """None → dropped. Matches the FeaturesSnapshot missing-attr path."""
    out = extract_top_features(
        ["a", "b"], [None, 1.0], {"a": 0.5, "b": 0.1},
    )  # type: ignore[list-item]
    assert len(out) == 1


def test_empty_inputs_return_empty_list():
    """Cold paths: no crash, just empty result."""
    assert extract_top_features([], [], {}) == []
    assert extract_top_features(["a"], [], {"a": 1.0}) == []
    assert extract_top_features([], [1.0], {"a": 1.0}) == []


def test_mismatched_lengths_return_empty():
    """names/values must be parallel — caller error, return empty
    rather than silently realign."""
    assert extract_top_features(["a", "b"], [1.0], {"a": 0.5, "b": 0.3}) == []


def test_direction_reflects_impact_sign_not_value_sign():
    """If the feature value is negative AND importance is positive
    (always is — gradient boosters don't emit negatives), the
    contribution is negative → 'bearish'. The direction label
    describes which way the contribution pushed, not the raw
    feature direction."""
    out = extract_top_features(
        ["bearish_driver"], [-5.0], {"bearish_driver": 0.2},
    )
    assert out[0]["direction"] == "bearish"
    assert out[0]["impact"] == pytest.approx(-1.0)


def test_output_shape_contract_fields_present():
    """Every row must carry the full contract so the frontend can
    render consistently — no missing keys, no surprises."""
    out = extract_top_features(
        ["a"], [1.0], {"a": 0.5},
    )
    row = out[0]
    for key in ("feature", "value", "importance", "impact", "abs_impact", "direction"):
        assert key in row


def test_zero_value_gives_zero_impact_and_ranks_last():
    """Value=0 produces impact=0, which should not dominate rankings."""
    out = extract_top_features(
        ["a", "b"], [0.0, 1.0], {"a": 0.9, "b": 0.1},
    )
    assert out[0]["feature"] == "b"  # impact 0.1 beats a's 0.0
