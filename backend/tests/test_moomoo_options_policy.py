"""MooMoo options policy + contract selection tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _make_chain(rows):
    """Convenience: build chain dicts from a list of tuples."""
    return [
        {
            "symbol": f"US.TEST{i}",
            "underlying": "TEST",
            "opt_type": r.get("opt_type", "call"),
            "strike": r.get("strike", 100.0),
            "expiry": r.get("expiry"),
            "bid": r.get("bid", 1.0),
            "ask": r.get("ask", 1.05),
            "last": r.get("last", 1.03),
            "volume": r.get("volume", 800),
            "open_interest": r.get("open_interest", 2000),
            "delta": r.get("delta", 0.35),
            "data_time_ns": r.get("data_time_ns"),
        }
        for i, r in enumerate(rows)
    ]


def _default_now():
    return datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc)


def test_policy_defaults_are_conservative(monkeypatch):
    for k in [
        "MOOMOO_OPTIONS_ENABLED", "MOOMOO_OPT_DELTA_MIN", "MOOMOO_OPT_DELTA_MAX",
        "MOOMOO_OPT_DTE_MIN", "MOOMOO_OPT_DTE_MAX", "MOOMOO_OPT_MIN_OI",
        "MOOMOO_OPT_MIN_VOL", "MOOMOO_OPT_MAX_SPREAD_PCT",
        "MOOMOO_OPT_MAX_CONTRACTS", "MOOMOO_OPT_STALE_QUOTE_SEC",
        "MOOMOO_OPT_EARNINGS_BLACKOUT",
    ]:
        monkeypatch.delenv(k, raising=False)
    from services.moomoo_options_policy import policy
    p = policy()
    assert p.enabled is False
    assert p.delta_min == 0.30 and p.delta_max == 0.45
    assert p.dte_min == 7 and p.dte_max == 21
    assert p.min_open_interest == 1000
    assert p.min_daily_volume == 500
    assert p.max_spread_pct == 8.0
    assert p.order_type == "marketable_limit"
    assert p.max_contracts == 1
    assert p.stale_quote_max_sec == 30
    assert p.earnings_blackout is True


def test_policy_respects_env_overrides(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPT_DELTA_MIN", "0.20")
    monkeypatch.setenv("MOOMOO_OPT_MIN_OI", "3000")
    monkeypatch.setenv("MOOMOO_OPTIONS_ENABLED", "1")
    from services.moomoo_options_policy import policy
    p = policy()
    assert p.enabled is True
    assert p.delta_min == 0.20
    assert p.min_open_interest == 3000


def test_select_contract_picks_middle_of_band():
    now = _default_now()
    expiry = (now + timedelta(days=14)).date().isoformat()
    chain = _make_chain([
        {"strike": 95, "expiry": expiry, "delta": 0.55, "bid": 3.0, "ask": 3.10,
         "volume": 900, "open_interest": 3000},  # delta out of band (too high)
        {"strike": 100, "expiry": expiry, "delta": 0.38, "bid": 2.10, "ask": 2.18,
         "volume": 900, "open_interest": 3000},  # sweet spot
        {"strike": 105, "expiry": expiry, "delta": 0.25, "bid": 1.20, "ask": 1.24,
         "volume": 900, "open_interest": 3000},  # too low delta
    ])
    from services.moomoo_options_policy import select_contract
    r = select_contract(chain, direction="call", now=now)
    assert r["selected"] is not None
    assert r["selected"]["delta"] == pytest.approx(0.38)
    assert r["selected"]["contracts"] == 1
    assert r["selected"]["estimated_debit"] == pytest.approx(2.14, rel=0.02)
    # 2 rejected calls above (0.55 too high, 0.25 too low)
    assert r["candidates_evaluated"] == 3
    assert len(r["rejected"]) == 2


def test_select_contract_rejects_wide_spread():
    now = _default_now()
    expiry = (now + timedelta(days=10)).date().isoformat()
    chain = _make_chain([
        {"strike": 100, "expiry": expiry, "delta": 0.38, "bid": 1.00, "ask": 1.20,
         "volume": 900, "open_interest": 3000},  # 18% spread
    ])
    from services.moomoo_options_policy import select_contract
    r = select_contract(chain, direction="call", now=now)
    assert r["selected"] is None
    assert any("spread_over_cap" in str(x.get("rejection")) for x in r["rejected"])


def test_select_contract_rejects_stale_quote():
    now = _default_now()
    expiry = (now + timedelta(days=10)).date().isoformat()
    stale_ns = int((now - timedelta(seconds=120)).timestamp() * 1e9)
    chain = _make_chain([
        {"strike": 100, "expiry": expiry, "delta": 0.38, "bid": 2.10, "ask": 2.18,
         "volume": 900, "open_interest": 3000, "data_time_ns": stale_ns},
    ])
    from services.moomoo_options_policy import select_contract
    r = select_contract(chain, direction="call", now=now)
    assert r["selected"] is None
    assert any("stale_quote" in str(x.get("rejection")) for x in r["rejected"])


def test_select_contract_respects_dte_bounds():
    now = _default_now()
    too_soon = (now + timedelta(days=3)).date().isoformat()
    too_far = (now + timedelta(days=30)).date().isoformat()
    good = (now + timedelta(days=14)).date().isoformat()
    chain = _make_chain([
        {"strike": 100, "expiry": too_soon, "delta": 0.38, "bid": 2.10, "ask": 2.18},
        {"strike": 100, "expiry": too_far, "delta": 0.38, "bid": 2.10, "ask": 2.18},
        {"strike": 100, "expiry": good, "delta": 0.38, "bid": 2.10, "ask": 2.18},
    ])
    from services.moomoo_options_policy import select_contract
    r = select_contract(chain, direction="call", now=now)
    assert r["selected"] is not None
    assert r["selected"]["dte"] == 14


def test_select_contract_filters_by_direction():
    now = _default_now()
    expiry = (now + timedelta(days=14)).date().isoformat()
    chain = _make_chain([
        {"strike": 100, "expiry": expiry, "opt_type": "put", "delta": 0.38,
         "bid": 2.10, "ask": 2.18},
        {"strike": 100, "expiry": expiry, "opt_type": "call", "delta": 0.38,
         "bid": 2.20, "ask": 2.28},
    ])
    from services.moomoo_options_policy import select_contract
    r_call = select_contract(chain, direction="call", now=now)
    r_put = select_contract(chain, direction="put", now=now)
    assert r_call["selected"]["opt_type"] == "call"
    assert r_put["selected"]["opt_type"] == "put"


def test_select_contract_low_liquidity_rejected():
    now = _default_now()
    expiry = (now + timedelta(days=14)).date().isoformat()
    chain = _make_chain([
        {"strike": 100, "expiry": expiry, "delta": 0.38, "bid": 2.10, "ask": 2.18,
         "volume": 100, "open_interest": 200},  # both below floors
    ])
    from services.moomoo_options_policy import select_contract
    r = select_contract(chain, direction="call", now=now)
    assert r["selected"] is None
    reasons = [str(x.get("rejection")) for x in r["rejected"]]
    assert any("open_interest_below_floor" in x or "volume_below_floor" in x
               for x in reasons)


def test_policy_to_dict_includes_notes(monkeypatch):
    from services.moomoo_options_policy import policy, policy_to_dict
    d = policy_to_dict(policy())
    assert "notes" in d
    assert any("V1 read-only" in n for n in d["notes"])
    # Note: enforce the doctrine that MARKET orders are prohibited
    assert any("MARKET" in n for n in d["notes"])


def test_empty_chain_returns_no_selection():
    from services.moomoo_options_policy import select_contract
    r = select_contract([], direction="call")
    assert r["selected"] is None
    assert r["candidates_evaluated"] == 0
