"""Regression: market_daily must always return ascending-by-date bars.

Root cause (2026-06): every provider (Public.com broker + Alpha Vantage
+ Polygon) returns daily bars NEWEST-first, but every consumer
(alpha_day_trader, public_equity_live_executor move-calc, market_regime,
fast_intraday_regime) reads ``bars[-1]`` as the most recent session.
The broker-primary path therefore fed the OLDEST bar (months stale) as
"today", collapsing the whole discovery funnel to zero setups.
``_normalize_daily`` is the single source of truth that fixes it.
"""
from services.market_data_pool import _normalize_daily


def _bars(dates):
    return [{"date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for d in dates]


def test_descending_broker_bars_become_ascending():
    # Public.com shape: newest-first ISO dates.
    pub = _bars(["2026-09-14", "2026-09-11", "2026-09-10", "2026-04-22"])
    out = _normalize_daily(pub)
    dates = [b["date"] for b in out]
    assert dates == sorted(dates)
    assert out[-1]["date"] == "2026-09-14"  # latest is now last
    assert out[0]["date"] == "2026-04-22"


def test_already_ascending_is_preserved():
    asc = _bars(["2026-04-22", "2026-09-10", "2026-09-14"])
    out = _normalize_daily(asc)
    assert [b["date"] for b in out] == ["2026-04-22", "2026-09-10", "2026-09-14"]


def test_empty_and_none_pass_through():
    assert _normalize_daily([]) == []
    assert _normalize_daily(None) is None


def test_missing_date_is_not_scrambled():
    # If any bar lacks a date we refuse to reorder rather than risk
    # producing a wrong "today".
    bad = [{"date": "2026-09-14", "close": 1}, {"close": 2}]
    assert _normalize_daily(bad) == bad


def test_datetime_field_fallback():
    rows = [
        {"datetime": "2026-09-14", "close": 3},
        {"datetime": "2026-09-10", "close": 1},
    ]
    out = _normalize_daily(rows)
    assert out[-1]["datetime"] == "2026-09-14"
