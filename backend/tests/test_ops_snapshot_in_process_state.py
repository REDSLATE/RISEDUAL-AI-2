"""Unit tests for ``services.ops_snapshot._collect_in_process_scheduler_state``.

The live preview /api/admin/ops-snapshot test only exercises the
``running: true`` happy path because the preview's APScheduler is
always healthy. These tests cover the failure branches by mutating
``routes.self_test._scheduler`` and ``_scheduler_boot_error`` directly,
locking in the contract surfaced to the Health panel after the
production incident on 2026-05-06 (boot crash invisible from the UI).
"""
from __future__ import annotations

import pytest

from services.ops_snapshot import _collect_in_process_scheduler_state
from routes import self_test as st


@pytest.fixture(autouse=True)
def _reset_state():
    """Restore the module globals after each test so we don't pollute
    later test files that import the live scheduler handle.
    """
    saved_scheduler = st._scheduler
    saved_boot_error = st._scheduler_boot_error
    yield
    st._scheduler = saved_scheduler
    st._scheduler_boot_error = saved_boot_error


def test_handle_none_no_boot_error_returns_default_reason():
    """When the scheduler never started AND no boot error was
    recorded, the envelope must still be operator-readable.
    """
    st._scheduler = None
    st._scheduler_boot_error = None

    out = _collect_in_process_scheduler_state()

    assert out["running"] is False
    assert out["jobs_count"] == 0
    assert "scheduler handle is None" in out["reason"]
    assert "boot_error" not in out


def test_handle_none_with_boot_error_surfaces_full_envelope():
    """When ``set_scheduler_boot_error`` was called, the captured
    exception must appear in the snapshot so the operator can read
    the actual cause without grepping pod logs.
    """
    st._scheduler = None
    try:
        raise RuntimeError("simulated boot crash: missing FOO_BAR_KEY")
    except RuntimeError as exc:
        st.set_scheduler_boot_error(exc, phase="_start_schedulers")

    out = _collect_in_process_scheduler_state()

    assert out["running"] is False
    assert "boot_error" in out, "boot_error must surface when captured"
    be = out["boot_error"]
    assert be["phase"] == "_start_schedulers"
    assert be["exc_type"] == "RuntimeError"
    assert "missing FOO_BAR_KEY" in be["exc_message"]
    # Traceback tail captures the file/line context for triage
    assert "RuntimeError" in be["traceback_tail"]


def test_set_scheduler_clears_previous_boot_error():
    """A successful ``set_scheduler`` must clear any previously
    recorded boot error so the Health panel doesn't show stale
    failure context after a healthy redeploy.
    """
    try:
        raise ValueError("stale failure from previous boot")
    except ValueError as exc:
        st.set_scheduler_boot_error(exc, phase="startup_event")
    assert st._scheduler_boot_error is not None

    class _FakeScheduler:
        running = True

        def get_jobs(self):
            return []

    st.set_scheduler(_FakeScheduler())

    assert st._scheduler_boot_error is None
    out = _collect_in_process_scheduler_state()
    assert out["running"] is True
    assert "boot_error" not in out


def test_running_scheduler_exposes_jobs_telemetry():
    """Healthy scheduler reports jobs_count, paused/overdue counters,
    and the next scheduled job's id + ISO timestamp.
    """
    from datetime import datetime, timedelta, timezone

    class _Job:
        def __init__(self, jid, next_run):
            self.id = jid
            self.next_run_time = next_run

    now = datetime.now(timezone.utc)

    class _FakeScheduler:
        running = True

        def get_jobs(self):
            return [
                _Job("smart_orders", now + timedelta(seconds=30)),
                _Job("paused_job", None),  # contributes to paused count
                _Job("overdue_job", now - timedelta(minutes=5)),  # overdue
            ]

    st._scheduler = _FakeScheduler()
    st._scheduler_boot_error = None

    out = _collect_in_process_scheduler_state()

    assert out["running"] is True
    assert out["jobs_count"] == 3
    assert out["paused_jobs"] == 1
    assert out["overdue_jobs"] == 1
    # Next job is the soonest non-None (overdue counts even though in past)
    assert out["next_job_id"] in {"smart_orders", "overdue_job"}
    assert out["next_job_at"] is not None


def test_get_jobs_raise_returns_structured_envelope_not_exception():
    """``get_jobs`` raising must NOT propagate — Health panel relies
    on a stable envelope. ``running`` should still reflect the
    handle's flag so we know the scheduler started even if the API
    misbehaves.
    """
    class _BrokenScheduler:
        running = True

        def get_jobs(self):
            raise OSError("disk pressure")

    st._scheduler = _BrokenScheduler()
    st._scheduler_boot_error = None

    out = _collect_in_process_scheduler_state()

    assert out["running"] is True
    assert out["jobs_count"] == 0
    assert "get_jobs raised" in out["reason"]
    assert "OSError" in out["reason"]
