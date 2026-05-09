"""Tests for the new ML feature builders.

Pins the architectural rules the operator laid out (2026-05-09):

  * Builders produce structured feature dicts only.
  * Builders NEVER raise.
  * Equity-only firewall on ``fundamentals`` (crypto / unknown
    lanes return ``{}``).
  * Builders never import anything from the executor / RoadGuard /
    FastVeto / broker chain (zero decision authority).
  * Builders never write to Mongo collections owned by the
    pipeline (alpha_decision_log, roadguard_*, phase5b_intents,
    paper_trades).
  * The wiring into ``extract_live_features`` is observation-only:
    ``frame.market.fundamentals`` and ``frame.market.technicals``
    show up as nested keys; the existing 10-dim Strategist vector
    is unchanged (artifact compatibility preserved).
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import services.ml.features.fundamentals as fundamentals_mod
import services.ml.features.technicals as technicals_mod
from services.ml.features.fundamentals import (
    _classify_pe_band,
    _safe_float,
    build_fundamentals_features,
)
from services.ml.features.technicals import build_technicals_features


# ───────────────────────────────────────────────────────────────
# Pure-helper coverage
# ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [
    (None, None), ("", None), ("None", None), ("-", None),
    ("not_a_number", None), ("18.4", 18.4), (18.4, 18.4),
    (12, 12.0), (0, 0.0), ("0.0", 0.0),
])
def test_safe_float_handles_av_quirks(value, expected):
    assert _safe_float(value) == expected


def test_pe_band_unknown_when_pe_is_none():
    assert _classify_pe_band(None) == {
        "negative_eps": 0, "pe_in_value_band": 0,
        "pe_in_growth_band": 0, "pe_in_speculative": 0,
    }


@pytest.mark.parametrize("pe, expected_band", [
    (-5, "negative_eps"),
    (0, "negative_eps"),
    (12.0, "pe_in_value_band"),
    (19.99, "pe_in_value_band"),
    (20.0, "pe_in_growth_band"),
    (49.99, "pe_in_growth_band"),
    (50.0, "pe_in_speculative"),
    (100.0, "pe_in_speculative"),
])
def test_pe_band_classification(pe, expected_band):
    flags = _classify_pe_band(pe)
    assert flags[expected_band] == 1
    others = [k for k in flags if k != expected_band]
    assert all(flags[k] == 0 for k in others), \
        f"only {expected_band} should be set, got {flags}"


# ───────────────────────────────────────────────────────────────
# Fundamentals — equity-only firewall
# ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fundamentals_returns_empty_for_crypto_lane():
    """Pinned firewall: fundamentals must NEVER return non-empty
    for crypto. Crypto symbols have no P/E surface; feeding zero
    or garbage into a future Strategist retrain would teach the
    wrong lesson.
    """
    out = await build_fundamentals_features(symbol="BTC-USD", lane="crypto")
    assert out == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["", "unknown", "options", "futures"])
async def test_fundamentals_returns_empty_for_unknown_lanes(lane):
    out = await build_fundamentals_features(symbol="AAPL", lane=lane)
    assert out == {}


@pytest.mark.asyncio
async def test_fundamentals_returns_empty_for_blank_symbol():
    out = await build_fundamentals_features(symbol="", lane="equity")
    assert out == {}


# ───────────────────────────────────────────────────────────────
# Fundamentals — never raises
# ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fundamentals_never_raises_when_provider_throws(monkeypatch):
    import services.price_provider as pp

    def _explode(symbol):
        raise RuntimeError("AV provider down")
    monkeypatch.setattr(pp, "get_overview_sync", _explode)

    out = await build_fundamentals_features(symbol="AAPL", lane="equity")
    assert out == {}


@pytest.mark.asyncio
async def test_fundamentals_returns_empty_when_provider_returns_none(monkeypatch):
    import services.price_provider as pp
    monkeypatch.setattr(pp, "get_overview_sync", lambda symbol: None)
    out = await build_fundamentals_features(symbol="AAPL", lane="equity")
    assert out == {}


@pytest.mark.asyncio
async def test_fundamentals_returns_empty_when_provider_returns_empty(monkeypatch):
    import services.price_provider as pp
    monkeypatch.setattr(pp, "get_overview_sync", lambda symbol: {})
    out = await build_fundamentals_features(symbol="AAPL", lane="equity")
    assert out == {}


# ───────────────────────────────────────────────────────────────
# Fundamentals — happy path shape
# ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fundamentals_shapes_av_overview_into_features(monkeypatch):
    import services.price_provider as pp

    fake_overview = {
        "Symbol": "AAPL",
        "PERatio": "29.4",
        "ForwardPE": "27.1",
        "TrailingPE": "29.4",
        "PEGRatio": "2.1",
        "EPS": "6.43",
        "DilutedEPSTTM": "6.40",
        "DividendYield": "0.005",
        "ProfitMargin": "0.25",
        "OperatingMarginTTM": "0.30",
        "ReturnOnEquityTTM": "1.45",
        "ReturnOnAssetsTTM": "0.20",
        "PriceToBookRatio": "55.2",
        "PriceToSalesRatioTTM": "9.5",
        "EVToRevenue": "9.0",
        "EVToEBITDA": "23.0",
        "Beta": "1.25",
        "QuarterlyRevenueGrowthYOY": "0.08",
        "QuarterlyEarningsGrowthYOY": "0.12",
        "BookValue": "4.55",
    }
    monkeypatch.setattr(pp, "get_overview_sync", lambda symbol: fake_overview)

    out = await build_fundamentals_features(symbol="aapl", lane="equity")
    # Raw numerics
    assert out["pe_ratio"] == 29.4
    assert out["forward_pe"] == 27.1
    assert out["peg_ratio"] == 2.1
    assert out["eps"] == 6.43
    assert out["dividend_yield"] == 0.005
    assert out["beta"] == 1.25
    assert out["revenue_growth_yoy"] == 0.08
    # Band flags — 29.4 is growth, NOT value
    assert out["pe_in_value_band"] == 0
    assert out["pe_in_growth_band"] == 1
    assert out["pe_in_speculative"] == 0
    assert out["negative_eps"] == 0
    assert out["dividend_payer"] == 1


@pytest.mark.asyncio
async def test_fundamentals_dividend_payer_zero_when_no_dividend(monkeypatch):
    import services.price_provider as pp
    monkeypatch.setattr(pp, "get_overview_sync", lambda s: {
        "Symbol": "TSLA", "PERatio": "60", "DividendYield": "0",
    })
    out = await build_fundamentals_features(symbol="TSLA", lane="equity")
    assert out["dividend_payer"] == 0
    assert out["pe_in_speculative"] == 1


# ───────────────────────────────────────────────────────────────
# Technicals — never raises, shape correctness
# ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_technicals_returns_empty_for_unknown_lane():
    out = await build_technicals_features(symbol="AAPL", lane="options")
    assert out == {}


@pytest.mark.asyncio
async def test_technicals_returns_empty_for_blank_symbol():
    out = await build_technicals_features(symbol="", lane="equity")
    assert out == {}


@pytest.mark.asyncio
async def test_technicals_never_raises_when_provider_throws(monkeypatch):
    import services.price_provider as pp

    async def _explode(symbol, outputsize="compact"):
        raise RuntimeError("provider down")
    monkeypatch.setattr(pp, "get_daily_history", _explode)

    out = await build_technicals_features(symbol="AAPL", lane="equity")
    assert out == {}


@pytest.mark.asyncio
async def test_technicals_returns_empty_when_no_bars(monkeypatch):
    import services.price_provider as pp

    async def _empty(symbol, outputsize="compact"):
        return []
    monkeypatch.setattr(pp, "get_daily_history", _empty)

    out = await build_technicals_features(symbol="AAPL", lane="equity")
    assert out == {}


@pytest.mark.asyncio
async def test_technicals_emits_full_feature_shape(monkeypatch):
    """Synthetic bar series → full feature shape. Pins which keys
    must always be present and that binary flags are 0/1 only."""
    import services.price_provider as pp

    # 250 ascending bars so SMA20/SMA50/SMA200 are all available.
    async def _bars(symbol, outputsize="compact"):
        bars: list = []
        # Newest-first per provider convention.
        for i in range(250, 0, -1):
            bars.append({"close": 100.0 + i * 0.5, "volume": 1_000_000})
        return bars
    monkeypatch.setattr(pp, "get_daily_history", _bars)

    out = await build_technicals_features(symbol="AAPL", lane="equity")
    # Required keys present
    for key in [
        "last_close", "rsi14", "macd", "macd_signal", "macd_hist",
        "sma20", "sma50", "sma200", "bar_count",
        "price_above_sma20", "price_above_sma50", "price_above_sma200",
        "sma20_above_sma50", "sma50_above_sma200",
        "rsi_oversold", "rsi_overbought", "rsi_neutral",
        "macd_bullish", "macd_bearish",
    ]:
        assert key in out, f"missing key: {key}"
    # Binary flags must be exactly 0 or 1
    binary_keys = [
        "price_above_sma20", "price_above_sma50", "price_above_sma200",
        "sma20_above_sma50", "sma50_above_sma200",
        "rsi_oversold", "rsi_overbought", "rsi_neutral",
        "macd_bullish", "macd_bearish",
    ]
    for k in binary_keys:
        assert out[k] in (0, 1), f"{k}={out[k]} must be 0 or 1"
    # On a strictly ascending price series the last close is the
    # global max → strictly above every SMA.
    assert out["price_above_sma20"] == 1
    assert out["price_above_sma50"] == 1
    assert out["price_above_sma200"] == 1


@pytest.mark.asyncio
async def test_technicals_works_for_crypto_lane(monkeypatch):
    """Technicals are universal — crypto must NOT be lane-firewalled."""
    import services.price_provider as pp

    async def _bars(symbol, outputsize="compact"):
        return [{"close": 100.0 + i, "volume": 10}
                for i in range(250, 0, -1)]
    monkeypatch.setattr(pp, "get_daily_history", _bars)

    out = await build_technicals_features(symbol="BTC-USD", lane="crypto")
    assert out  # non-empty
    assert "rsi14" in out


# ───────────────────────────────────────────────────────────────
# Authority-boundary firewall — feature builders MUST NOT import
# anything from the decision/execution/broker chain
# ───────────────────────────────────────────────────────────────


_FORBIDDEN_IMPORT_PATTERNS = [
    # Broker / execution
    r"\bfrom services\.broker_service\b",
    r"\bfrom services\.trading_bot_service\b",
    r"\bfrom services\.crypto_paper_trader\b",
    r"\bfrom services\.paper_trading_service\b",
    r"\bfrom routes\.broker\b",
    # Authority chain
    r"\bfrom services\.ml\.executors\b",
    r"\bfrom services\.ml\.roadguard\b",
    r"\bfrom services\.ml\.fast_veto\b",
    r"\bfrom services\.ml\.shadow_wiring\b",
    r"\bfrom services\.ml\.pipeline\b",
    r"\bfrom services\.ml\.broker_wire\b",
    # Receipts / audit chains owned by the pipeline
    r"\bfrom services\.alpha_decision_log\b",
    r"\bfrom services\.kill_switch\b",
    r"\bfrom ai_core\.kill_switch\b",
]

_FEATURE_FILES = [
    Path(fundamentals_mod.__file__),
    Path(technicals_mod.__file__),
]


@pytest.mark.parametrize("path", _FEATURE_FILES,
                         ids=[p.name for p in _FEATURE_FILES])
def test_feature_builders_have_no_authority_imports(path: Path):
    src = path.read_text()
    for pattern in _FORBIDDEN_IMPORT_PATTERNS:
        assert not re.search(pattern, src), (
            f"{path.name} imports forbidden authority/broker/receipt "
            f"surface: {pattern}"
        )


# ───────────────────────────────────────────────────────────────
# Hard-rule absence — feature builders MUST NOT branch on values
# to issue verdicts (no `return "BUY"` etc.)
# ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _FEATURE_FILES,
                         ids=[p.name for p in _FEATURE_FILES])
def test_feature_builders_emit_no_verdicts(path: Path):
    """Static check: no string literal `BUY`/`SELL`/`NO_TRADE`,
    no calls into ``Verdict.X.value``, no mention of
    ``set_active`` / ``promote``.
    """
    src = path.read_text()
    forbidden_substrings = [
        '"BUY"', "'BUY'",
        '"SELL"', "'SELL'",
        '"NO_TRADE"', "'NO_TRADE'",
        "Verdict.BUY", "Verdict.SELL", "Verdict.NO_TRADE",
        "set_active", "promote_now", "promote_action",
        ".place_order", "broker.execute",
    ]
    for needle in forbidden_substrings:
        assert needle not in src, (
            f"{path.name} contains forbidden authority/decision marker "
            f"'{needle}' — feature builders must produce features only."
        )


# ───────────────────────────────────────────────────────────────
# Wiring into extract_live_features — observation-only
# ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_live_features_includes_nested_feature_keys(monkeypatch):
    """When builders return non-empty dicts, the nested keys land
    in the FeatureFrame.market dict — but ONLY as observation
    fields (the existing 10-dim Strategist vector still reads its
    own perception/recall scores, untouched here)."""
    from services.ml import feature_extraction as fe

    async def _fake_fund(symbol, lane):
        return {"pe_ratio": 18.4, "pe_in_value_band": 1}

    async def _fake_tech(symbol, lane):
        return {"rsi14": 65.0, "macd_bullish": 1}

    monkeypatch.setattr(
        "services.ml.features.fundamentals.build_fundamentals_features",
        _fake_fund,
    )
    monkeypatch.setattr(
        "services.ml.features.technicals.build_technicals_features",
        _fake_tech,
    )

    out = await fe.extract_live_features(
        symbol="AAPL", lane="equity", db=None,
    )
    assert out["fundamentals"] == {"pe_ratio": 18.4, "pe_in_value_band": 1}
    assert out["technicals"] == {"rsi14": 65.0, "macd_bullish": 1}


@pytest.mark.asyncio
async def test_extract_live_features_omits_empty_builder_results(monkeypatch):
    """When a builder returns ``{}`` (e.g. fundamentals on a crypto
    lane), the nested key must NOT be added — keeping the frame
    clean rather than littering it with empty containers."""
    from services.ml import feature_extraction as fe

    async def _empty(symbol, lane):
        return {}

    monkeypatch.setattr(
        "services.ml.features.fundamentals.build_fundamentals_features",
        _empty,
    )
    monkeypatch.setattr(
        "services.ml.features.technicals.build_technicals_features",
        _empty,
    )

    out = await fe.extract_live_features(
        symbol="BTC-USD", lane="crypto", db=None,
    )
    assert "fundamentals" not in out
    assert "technicals" not in out


@pytest.mark.asyncio
async def test_extract_live_features_does_not_change_existing_keys(monkeypatch):
    """Adding the nested feature builders MUST NOT shadow any
    existing top-level key (broker_uptime, intraday_progress,
    error_rate, ...). Pre-existing tests rely on these; if the
    nested dicts ever start writing flat keys, this catches it.
    """
    from services.ml import feature_extraction as fe

    async def _fund(symbol, lane):
        return {"pe_ratio": 18.4}  # nested key only

    async def _tech(symbol, lane):
        return {"rsi14": 50.0}  # nested key only

    monkeypatch.setattr(
        "services.ml.features.fundamentals.build_fundamentals_features",
        _fund,
    )
    monkeypatch.setattr(
        "services.ml.features.technicals.build_technicals_features",
        _tech,
    )

    out = await fe.extract_live_features(
        symbol="AAPL", lane="equity", db=None,
    )
    # Pre-existing surface still present
    assert "broker_uptime" in out
    assert "intraday_progress" in out
    assert "error_rate" in out


# ───────────────────────────────────────────────────────────────
# Strategist artifact-shape invariant — feature builders MUST NOT
# silently widen the 10-dim vector the Strategist artifact expects.
# ───────────────────────────────────────────────────────────────


def test_strategist_feature_dim_unchanged():
    """The Strategist vector dim is the contract every loaded
    artifact obeys. Adding feature builders must NOT change it.
    """
    from services.ml.strategist import base as strat_base
    assert strat_base._FEATURE_DIM == 10, (
        "Strategist _FEATURE_DIM changed; that breaks every loaded "
        "artifact. Feature-builder outputs are observation-only "
        "(frame.market.fundamentals / .technicals); they do not "
        "extend the live decision vector. If you intend to widen "
        "it, a coordinated retrain must follow."
    )


def test_build_features_function_does_not_read_new_nested_keys():
    """Static signal: the strategist's ``_build_features`` must
    not accidentally start reading from the new nested feature
    dicts. If it does, the artifact-shape contract has drifted.
    """
    from services.ml.strategist import base as strat_base
    src = inspect.getsource(strat_base._build_features)
    assert "fundamentals" not in src
    assert "technicals" not in src
