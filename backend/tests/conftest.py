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
