"""Phase A trace-id instrumentation tests.

Verifies that one intent can be followed end-to-end across:

    ALPHA_*_INTENT_CREATED  →  MC_*_POST_SENT  →  MC_*_RESPONSE_*
                                            ↓
                                  *_ADAPTER_REACHED  →  *_BROKER_*

The trace_id MUST appear in every layer's log line so the operator
can grep one intent across the brain↔MC boundary.
"""
from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock

import pytest

from sovereign.intent_bridge import _build_emission_kwargs, _classify_lane, _new_trace_id
from sovereign.mc_client import _classify_lane_for_log, build_intent_body


def _receipt(symbol="NVDA", action="BUY", conf=80):
    return {
        "symbol": symbol,
        "raw_action": action,
        "market_decision": action,
        "display_action": action,
        "final_confidence": conf,
        "execution_decision": "ALLOW",
    }


# ── basic shape / propagation ─────────────────────────────────────────


def test_new_trace_id_is_8_char_hex():
    tid = _new_trace_id()
    assert re.fullmatch(r"[0-9a-f]{8}", tid)


def test_build_emission_kwargs_mints_trace_id_when_not_supplied(caplog):
    caplog.set_level(logging.INFO, logger="sovereign.intent_bridge")
    out = _build_emission_kwargs(_receipt(), qty=1.0, notes="")
    assert "trace_id" in out
    assert re.fullmatch(r"[0-9a-f]{8}", out["trace_id"])
    # The CREATED log line carries the same trace_id.
    assert any(out["trace_id"] in r.getMessage() for r in caplog.records)
    assert any("ALPHA_EQUITY_INTENT_CREATED" in r.getMessage() for r in caplog.records)


def test_build_emission_kwargs_honors_explicit_trace_id():
    out = _build_emission_kwargs(
        _receipt(), qty=1.0, notes="", trace_id="abcd1234",
    )
    assert out["trace_id"] == "abcd1234"


def test_build_emission_kwargs_tags_crypto_lane(caplog):
    caplog.set_level(logging.INFO, logger="sovereign.intent_bridge")
    out = _build_emission_kwargs(
        _receipt(symbol="BTC/USD"), qty=1.0, notes="",
    )
    assert any("ALPHA_CRYPTO_INTENT_CREATED" in r.getMessage() for r in caplog.records)
    assert out["symbol"] == "BTC/USD"


# ── lane classifier ───────────────────────────────────────────────────


@pytest.mark.parametrize("symbol,expected", [
    ("BTC/USD", "CRYPTO"),
    ("ETH-USD", "CRYPTO"),
    ("SOL/USDT", "CRYPTO"),
    ("DOGE/USDC", "CRYPTO"),
    ("NVDA", "EQUITY"),
    ("AAPL", "EQUITY"),
    ("SPY", "EQUITY"),
])
def test_lane_classifier(symbol, expected):
    assert _classify_lane(symbol) == expected
    assert _classify_lane_for_log(symbol) == expected


# ── build_intent_body accepts + carries trace_id ──────────────────────


def test_build_intent_body_carries_trace_id():
    body = build_intent_body(
        symbol="BTC/USD", side="BUY", qty=1.0, confidence=0.85,
        trace_id="deadbeef",
    )
    assert body["trace_id"] == "deadbeef"


def test_build_intent_body_omits_trace_when_not_supplied():
    body = build_intent_body(
        symbol="NVDA", side="BUY", qty=1.0, confidence=0.85,
    )
    assert "trace_id" not in body


# ── post_intent emits boundary log lines ──────────────────────────────


def test_post_intent_logs_at_send_and_receive(caplog, monkeypatch):
    """MC_POST_SENT + MC_RESPONSE_OK both echo the trace_id."""
    from sovereign.mc_client import MCClient
    caplog.set_level(logging.INFO, logger="sovereign.mc_client")

    fake_resp = MagicMock(
        status_code=200,
        content=b'{"intent_id": "abc123", "executable": true, "decision": "ACCEPTED"}',
    )
    fake_resp.json.return_value = {
        "intent_id": "abc123", "executable": True, "decision": "ACCEPTED",
    }

    client = MCClient(
        base_url="https://mc.test", brain="alpha", runtime_token="tk",
    )
    monkeypatch.setattr(client._client, "post", lambda *a, **k: fake_resp)

    client.post_intent(
        symbol="BTC/USD", side="BUY", qty=1.0, confidence=0.85,
        trace_id="cafef00d",
    )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("cafef00d" in m and "MC_CRYPTO_POST_SENT" in m for m in msgs)
    assert any("cafef00d" in m and "MC_CRYPTO_RESPONSE_OK" in m for m in msgs)


def test_post_intent_warns_on_non_executable_echo(caplog, monkeypatch):
    """If MC accepts but flags executable=false, the operator gets a
    dedicated MC_*_NON_EXECUTABLE warning so the silent-HOLD case
    (Camaro symptom) is impossible to miss."""
    from sovereign.mc_client import MCClient
    caplog.set_level(logging.INFO, logger="sovereign.mc_client")

    fake_resp = MagicMock(
        status_code=200,
        content=b'{"intent_id": "x", "executable": false, "reason": "council_hold"}',
    )
    fake_resp.json.return_value = {
        "intent_id": "x", "executable": False, "reason": "council_hold",
    }

    client = MCClient(
        base_url="https://mc.test", brain="alpha", runtime_token="tk",
    )
    monkeypatch.setattr(client._client, "post", lambda *a, **k: fake_resp)

    client.post_intent(
        symbol="BTC/USD", side="BUY", qty=1.0, confidence=0.85,
        trace_id="11112222",
    )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("11112222" in m and "MC_CRYPTO_NON_EXECUTABLE" in m
               and "council_hold" in m for m in msgs)


def test_post_intent_logs_fail_on_transport_error(caplog, monkeypatch):
    from sovereign.mc_client import MCClient, MCClientError
    import httpx
    caplog.set_level(logging.WARNING, logger="sovereign.mc_client")

    def _raise(*a, **k):
        raise httpx.ConnectError("boom")

    client = MCClient(
        base_url="https://mc.test", brain="alpha", runtime_token="tk",
    )
    monkeypatch.setattr(client._client, "post", _raise)

    with pytest.raises(MCClientError):
        client.post_intent(
            symbol="BTC/USD", side="BUY", qty=1.0, confidence=0.85,
            trace_id="ffffffff",
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("ffffffff" in m and "MC_CRYPTO_RESPONSE_FAIL" in m for m in msgs)
