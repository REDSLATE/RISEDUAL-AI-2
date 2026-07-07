"""War Room P0 timing regression tests.

Validates the P0 fix that reduces total wall-clock time of
POST /api/web-intel/war-room from ~19s (causing prod 520 errors) to <15s.

- AI_ANALYSIS_TIMEOUT: 18s → 5s
- PROVIDER_PHASE_TIMEOUT: newly capped at 5.5s (asyncio.timeout wrapping gather)
- Individual provider timeouts capped to 4-6s
- ai_analysis max_tokens: 600 → 400

Target: response ≤ 12s consistently. Hard failure at 15s (Cloudflare timeout).
"""
import time
import pytest
import requests
from conftest_creds import BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD


HARD_LIMIT = 15.0    # Cloudflare/ingress proxy limit — must never exceed
TARGET_LIMIT = 12.0  # Target for P0 fix — should be met consistently


@pytest.fixture(scope="module")
def auth_session():
    """Login once per module and return an authenticated requests Session
    (uses httpOnly cookies for auth, as per the app's auth flow)."""
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    # Cookies should be set for subsequent requests; also grab bearer token as belt-and-suspenders
    token = body.get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


# -----------------------------
# Regression: status endpoints
# -----------------------------
class TestStatusEndpoints:
    def test_status_returns_registry_with_capped_timeouts(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/web-intel/status", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "war_room_registry" in data
        reg = data["war_room_registry"]
        assert "providers" in reg
        providers = {p["name"]: p for p in reg["providers"]}

        # Per review request: all providers ≤6s except wikipedia=4s, yahoo/ddg=5s
        expected_max = {
            "wikipedia": 4.0,
            "sec": 6.0,
            "stockfit": 6.0,
            "tavily": 6.0,
            "av_news": 6.0,
            "finnhub_news": 6.0,
            "ddg": 5.0,
            "ddg_news": 5.0,
            "yahoo": 5.0,
            "fred": 6.0,
            "newsapi": 6.0,
        }
        for name, expected in expected_max.items():
            assert name in providers, f"Missing provider {name} in registry"
            actual = providers[name]["timeout"]
            assert actual <= expected, (
                f"Provider {name} timeout={actual} exceeds cap {expected}"
            )

    def test_cache_status_returns_200(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/web-intel/cache/status", timeout=10)
        assert r.status_code == 200
        # Body should be JSON (dict) — cache_status() returns dict of state
        assert isinstance(r.json(), dict)


# ---------------------------------
# Payload shape + timing per query
# ---------------------------------
def _validate_war_room_payload(data: dict) -> None:
    """Common payload assertions per review request."""
    # 'brief' object with headline/summary/signals/risks
    assert "brief" in data, f"Missing 'brief' in response keys={list(data.keys())}"
    brief = data["brief"]
    for k in ("headline", "summary", "signals", "risks"):
        assert k in brief, f"brief missing '{k}': {list(brief.keys())}"

    # 'engine_results' array
    assert "engine_results" in data
    assert isinstance(data["engine_results"], list)
    assert len(data["engine_results"]) > 0, "engine_results is empty"

    # 'warnings' array
    assert "warnings" in data
    assert isinstance(data["warnings"], list)


QUERIES = [
    {"query": "TSLA outlook", "symbol": "TSLA", "mode": "auto"},
    {"query": "AAPL fundamentals", "symbol": "AAPL", "mode": "company"},
    {"query": "MSFT earnings", "symbol": "MSFT", "mode": "auto"},
    {"query": "GOOGL news", "symbol": "GOOGL", "mode": "auto"},
    {"query": "Fed rate decision", "symbol": None, "mode": "auto"},
]


@pytest.mark.parametrize("payload", QUERIES, ids=[q["query"] for q in QUERIES])
def test_war_room_timing_and_payload(auth_session, payload):
    """POST /api/web-intel/war-room — validate timing < HARD_LIMIT
    and payload shape for each query."""
    body = {"query": payload["query"], "mode": payload["mode"]}
    if payload["symbol"]:
        body["symbol"] = payload["symbol"]

    start = time.monotonic()
    r = auth_session.post(
        f"{BASE_URL}/api/web-intel/war-room",
        json=body,
        timeout=HARD_LIMIT + 5,  # give a little headroom for network jitter, still catches hangs
    )
    elapsed = time.monotonic() - start
    print(f"[war-room] query='{payload['query']}' elapsed={elapsed:.2f}s status={r.status_code}")

    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
    assert elapsed < HARD_LIMIT, (
        f"War Room exceeded hard limit {HARD_LIMIT}s "
        f"(got {elapsed:.2f}s) — would trigger prod 520 error"
    )
    if elapsed > TARGET_LIMIT:
        # Non-fatal warning but recorded — surfaces slowness without hard failing
        pytest.warns(UserWarning) if False else None
        print(f"WARN: elapsed {elapsed:.2f}s > target {TARGET_LIMIT}s")

    data = r.json()
    _validate_war_room_payload(data)


def test_ai_analysis_engine_present_and_ok_for_typical_query(auth_session):
    """AI analysis result should be present with engine starting 'ai_analysis'
    and status='ok' for a typical/known ticker query."""
    body = {"query": "AAPL fundamentals", "symbol": "AAPL", "mode": "company"}
    start = time.monotonic()
    r = auth_session.post(f"{BASE_URL}/api/web-intel/war-room", json=body, timeout=HARD_LIMIT + 5)
    elapsed = time.monotonic() - start
    print(f"[ai_analysis check] elapsed={elapsed:.2f}s")

    assert r.status_code == 200
    assert elapsed < HARD_LIMIT
    data = r.json()

    ai_results = [
        er for er in data["engine_results"]
        if isinstance(er.get("engine"), str) and er["engine"].startswith("ai_analysis")
    ]
    assert ai_results, (
        "No engine result starting with 'ai_analysis' — "
        f"got engines={[er.get('engine') for er in data['engine_results']]}"
    )
    ai = ai_results[0]
    assert ai.get("status") == "ok", (
        f"AI analysis engine status={ai.get('status')} error={ai.get('error')}"
    )


def test_provider_phase_capped_no_hang(auth_session):
    """Even worst-case queries must return within HARD_LIMIT — this guards
    the asyncio.timeout wrapper around asyncio.gather that returns partials
    if the provider phase exceeds PROVIDER_PHASE_TIMEOUT."""
    body = {"query": "XYZQ nonexistent ticker deep analysis", "symbol": "XYZQ", "mode": "company"}
    start = time.monotonic()
    r = auth_session.post(f"{BASE_URL}/api/web-intel/war-room", json=body, timeout=HARD_LIMIT + 5)
    elapsed = time.monotonic() - start
    print(f"[hang-guard] elapsed={elapsed:.2f}s degraded={r.json().get('degraded') if r.ok else 'n/a'}")

    assert r.status_code == 200
    # PROVIDER_PHASE_TIMEOUT (5.5) + AI_ANALYSIS_TIMEOUT (5.0) + overhead ~= ~11s worst case
    assert elapsed < HARD_LIMIT, (
        f"Elapsed {elapsed:.2f}s ≥ hard limit {HARD_LIMIT}s — phase-cap not working"
    )
    data = r.json()
    _validate_war_room_payload(data)
    # degraded may be True for non-existent tickers; both are valid
    assert isinstance(data.get("degraded"), bool)
