"""Pytest configuration — makes sibling modules (conftest_creds, etc.) importable
from any test, regardless of working directory, and loads backend .env so tests
that need REACT_APP_BACKEND_URL / MONGO_URL etc. just work out of the box.

Without this, running `pytest` from /app/backend/ fails on 33 test modules that
import `from conftest_creds import ...` because the tests dir isn't on sys.path.
Moving to pytest's standard conftest.py keeps this invisible to test code —
they can just `import conftest_creds` and it Just Works.
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# Load backend + frontend env files so tests that probe REACT_APP_BACKEND_URL,
# MONGO_URL, etc. find them without manual `source .env` ceremony. Frontend
# .env holds the ingress URL; backend holds secrets.
try:
    from dotenv import load_dotenv
    load_dotenv(TESTS_DIR.parent / ".env")               # /app/backend/.env
    load_dotenv(TESTS_DIR.parent.parent / "frontend" / ".env")  # /app/frontend/.env
except Exception:
    pass



import pytest


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    """Isolate the module-level `ai_core.kill_switch.kill_switch`
    singleton between tests.

    Execution-path tests (execute_signal, drawdown allocator, etc.)
    fire through `kill_switch.is_active()` and record outcomes into
    its rolling error window. Without a reset, a failing test early
    in the run can trip the switch and cascade "kill switch active"
    skips through every later test in the module — which reads as
    10+ unrelated failures.

    Autouse so no test has to remember to import / invoke the fix
    — the invariant is "every test starts with a cleared switch".
    """
    try:
        from ai_core.kill_switch import kill_switch
        kill_switch.reset()
    except ImportError:
        # Kill switch module may not be importable in isolated
        # micro-tests; silently skip.
        pass
    yield
    try:
        from ai_core.kill_switch import kill_switch
        kill_switch.reset()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _disable_crypto_shadow_research(monkeypatch):
    """Block the Tavily + LLM shadow research call across the entire
    test suite by default.

    The crypto bot's shadow lane (services.research_router.fetch_or_skip)
    short-circuits when ``CRYPTO_SHADOW_RESEARCH_DISABLED=1`` is set,
    so this fixture prevents any test that exercises
    ``run_crypto_symbol`` / ``run_crypto_paper_bot`` from making real
    Tavily HTTP calls or burning EMERGENT_LLM_KEY budget.

    Tests that need to exercise the shadow path explicitly should
    delete this env var inside the test (e.g. via
    ``monkeypatch.delenv("CRYPTO_SHADOW_RESEARCH_DISABLED")``).
    """
    monkeypatch.setenv("CRYPTO_SHADOW_RESEARCH_DISABLED", "1")


@pytest.fixture(autouse=True)
def _disable_patent_i_in_legacy_tests(monkeypatch):
    """Patent I tightens position sizes based on rolling track record.

    Many pre-existing unit tests pin exact pre-Patent-I sizes against
    stubbed in-memory DBs. Disable the gateway by default in the test
    suite — Patent I has its own dedicated tests
    (``test_authority_risk_budget.py``, ``test_risk_budget_gateway.py``)
    that exercise the live behaviour.

    Tests that need Patent I active explicitly should set
    ``monkeypatch.setenv("PATENT_I_ENABLED", "1")``.
    """
    monkeypatch.setenv("PATENT_I_ENABLED", "0")


@pytest.fixture(autouse=True)
def _disable_patent_guard_in_legacy_tests(monkeypatch):
    """Patent J/K/M/I full guard pipeline tightens / blocks trades and
    persists proof events to ``decision_proof_chain``.

    Same rationale as ``_disable_patent_i_in_legacy_tests``: pre-guard
    unit tests pin exact sizes that the guard will (correctly)
    tighten. The guard has dedicated tests
    (``test_decision_pipeline_guard.py`` + the patent unit tests) that
    exercise live behaviour.

    Tests that exercise the guard should
    ``monkeypatch.setenv("PATENT_GUARD_ENABLED", "1")``.
    """
    monkeypatch.setenv("PATENT_GUARD_ENABLED", "0")


@pytest.fixture(scope="session", autouse=True)
def _clear_brute_force_lockouts_at_session_start():
    """Wipe ``login_attempts`` at the start of every test session.

    The auth route's brute-force limiter locks ``(client_ip, email)``
    buckets for 15 minutes after 5 failed attempts (see
    ``routes.auth.check_brute_force``). Previous runs that
    intentionally exercised the 401 path — or that ran before the
    2026-05-01 X-Forwarded-For fix — left stale lockout rows in
    Mongo that bled into subsequent sessions and produced cascading
    429s that masked real test signal.

    Clearing at session start is scoped, idempotent, and safe: the
    production rate-limiter still fires for end users; only the
    test's own repeated-failure artefacts are purged.
    """
    import os
    try:
        from pymongo import MongoClient
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        if mongo_url and db_name:
            client = MongoClient(mongo_url, serverSelectionTimeoutMS=2000)
            client[db_name].login_attempts.delete_many({})
            client.close()
    except Exception:
        # Never fail the whole session on a cleanup glitch — Mongo
        # might be temporarily unavailable in CI without auth tests
        # running anyway.
        pass
    yield
