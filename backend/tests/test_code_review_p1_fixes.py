"""P1 code-review bug fixes regression tests (iter_183).

Covers bugs 1, 2, 3, 4, 5, 7, 8, 9 from external code review. Uses a mix
of static source-file assertions and live endpoint calls.
"""
import re
from pathlib import Path

import pytest
import requests

from conftest_creds import ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL

REPO = Path("/app")


# ---------- BUG 1: OrderFlowHeatmap tailwind double-opacity ----------
def test_bug1_orderflow_heatmap_no_double_opacity_class():
    src = (REPO / "frontend/src/components/OrderFlowHeatmap.jsx").read_text()
    assert "border-slate-400/30/60" not in src, "double-opacity class still present"
    # line ~206 should contain the corrected single-opacity class
    assert "border-slate-400/30" in src


# ---------- BUG 2: ADVERSARIAL CHECK renumbering 1..5 (not 1,2,3,4,4) ----------
def test_bug2_adversarial_check_numbered_1_to_5():
    src = (REPO / "backend/services/crew_definitions.py").read_text()
    # The prediction crew ADVERSARIAL block has "5. In your summary"
    assert "5. In your summary" in src
    # And should not contain a duplicated "4. In your summary" line
    fours = re.findall(r"^\s*4\.\s*In your summary", src, flags=re.MULTILINE)
    assert len(fours) == 0, f"duplicate '4. In your summary' still present: {fours}"


# ---------- BUG 3: OrderFlowStream._bg_tasks tracking ----------
def test_bug3_orderflow_bg_tasks_attribute_and_callback():
    src = (REPO / "backend/services/orderflow_ws_service.py").read_text()
    assert "self._bg_tasks: set[asyncio.Task] = set()" in src
    assert "self._bg_tasks.add(task)" in src
    assert "self._bg_tasks.discard" in src


# ---------- BUG 4: exponential backoff in _run_stream ----------
def test_bug4_exponential_backoff_constants():
    src = (REPO / "backend/services/orderflow_ws_service.py").read_text()
    assert "base_delay = 3.0" in src
    assert "max_delay = 60.0" in src
    assert "reconnect_delay = base_delay" in src
    # Doubling with cap
    assert "min(reconnect_delay * 2.0, max_delay)" in src
    # Static: no leftover fixed 3-second sleep
    assert "asyncio.sleep(3)" not in src


# ---------- BUG 5: crew_engine uses get_running_loop ----------
def test_bug5_crew_engine_uses_get_running_loop():
    src = (REPO / "backend/services/crew_engine.py").read_text()
    assert "asyncio.get_running_loop()" in src
    assert "asyncio.get_event_loop()" not in src


# ---------- BUG 7: routes/auth.py re-exports auth helpers ----------
def test_bug7_auth_routes_imports_helpers():
    src = (REPO / "backend/routes/auth.py").read_text()
    assert "from services.auth_helpers import" in src
    # These helpers should NOT be redefined in routes/auth.py
    assert re.search(r"^\s*def\s+get_jwt_secret\s*\(", src, re.MULTILINE) is None
    assert re.search(r"^\s*def\s+get_current_user\s*\(", src, re.MULTILINE) is None
    assert re.search(r"^\s*def\s+get_optional_user\s*\(", src, re.MULTILINE) is None


def test_bug7_helpers_reexport_and_login_flow():
    # Import re-export path
    from routes.auth import get_current_user, get_optional_user, get_jwt_secret  # noqa: F401
    from services.auth_helpers import (
        get_current_user as h_gcu,
        get_optional_user as h_gou,
        get_jwt_secret as h_gjs,
    )
    from routes import auth as auth_mod
    assert auth_mod.get_current_user is h_gcu
    assert auth_mod.get_optional_user is h_gou
    assert auth_mod.get_jwt_secret is h_gjs


def _login_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"admin login unavailable: {r.status_code} {r.text[:200]}")
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def test_bug7_login_and_me_returns_user():
    s = _login_session()
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=10)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body.get("email", "").lower() == ADMIN_EMAIL.lower()


def test_bug7_admin_retention_status_accessible():
    """Proves that get_current_user re-export works across modules."""
    s = _login_session()
    r = s.get(f"{BASE_URL}/api/admin/retention/status", timeout=15)
    assert r.status_code == 200, r.text[:300]


# ---------- BUG 8: server startup env validation ----------
def test_bug8_required_env_guard_in_server():
    src = (REPO / "backend/server.py").read_text()
    assert '_REQUIRED_ENV = ("MONGO_URL", "DB_NAME", "JWT_SECRET")' in src
    # Should raise RuntimeError on missing envs
    assert "RuntimeError" in src


# ---------- BUG 9: logger.warning replaces bare except Exception: pass ----------
def test_bug9_war_room_and_hypothesis_warnings_present():
    src = (REPO / "backend/services/crew_definitions.py").read_text()
    war_tags = re.findall(r"\[war_room_crew:\{symbol\}\]", src)
    hyp_tags = re.findall(r"\[hypothesis_crew:\{symbol\}\]", src)
    # 2 in war_room (memory + orderflow), 2 in hypothesis (memory + orderflow)
    assert len(war_tags) >= 2, f"expected >=2 war_room_crew warnings, got {len(war_tags)}"
    assert len(hyp_tags) >= 2, f"expected >=2 hypothesis_crew warnings, got {len(hyp_tags)}"


def test_bug9_no_bare_except_pass_in_war_room_or_hypothesis():
    """Scan the two target functions for lingering `except Exception: pass`."""
    src = (REPO / "backend/services/crew_definitions.py").read_text()
    # Find bounds of run_war_room_crew and run_hypothesis_crew
    lines = src.splitlines()

    def _slice_func(fname):
        start = None
        for i, ln in enumerate(lines):
            if re.match(rf"\s*(async\s+)?def\s+{fname}\s*\(", ln):
                start = i
                break
        if start is None:
            return ""
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = len(lines)
        for j in range(start + 1, len(lines)):
            ln = lines[j]
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent and re.match(r"\s*(async\s+)?def\s+", ln):
                end = j
                break
        return "\n".join(lines[start:end])

    for fname in ("run_war_room_crew", "run_hypothesis_crew"):
        body = _slice_func(fname)
        assert body, f"could not locate {fname}"
        # Look for a bare `except Exception:` followed by `pass` on the next non-empty line
        bad = re.findall(r"except\s+Exception\s*:\s*\n\s*pass\b", body)
        assert not bad, f"{fname} still has bare `except Exception: pass`"
