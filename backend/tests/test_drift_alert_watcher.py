"""Tests for ``services.drift_alert_watcher``.

Four firing scenarios to pin:

1. Threshold crossing into rebuild band (≥ 10%).
2. Sudden jump > 5 pts between consecutive checks.
3. Recovery after a prior rebuild alert.
4. Quiet path — below threshold, no jump, no prior alert → no emits.

Dedup behaviour is handled by ``ai_core_alerts.emit`` (date-bucketed
unique id). We verify the watcher calls emit with the right shape;
the dedup itself has its own coverage in ``ai_core_alerts`` tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.drift_alert_watcher import (
    JUMP_PCT, OK_PCT, REBUILD_PCT,
    _reset_state_for_tests, check_and_alert,
)


def setup_function():
    _reset_state_for_tests()


def _mock_snap(pct: float):
    return {
        "drift": 50,
        "drift_pct": pct,
        "mongo_total": 500,
        "chroma_total": 450,
        "recommendation": (
            "ok" if pct < 1.0 else ("investigate" if pct < 10 else "rebuild")
        ),
    }


# ── 1. Threshold crossing ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_alert_fires_on_first_crossing():
    """First tick with drift ≥ 10 → rebuild alert emitted.
    ``prev_pct`` is None on the first tick, so the crossing rule
    fires (drift_pct >= REBUILD_PCT and prev < REBUILD_PCT)."""
    emit = AsyncMock()
    with patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=_mock_snap(12.0)),
    ), patch("services.ai_core_alerts.emit", new=emit):
        result = await check_and_alert()

    assert "rebuild" in result["fired"]
    assert emit.call_count >= 1
    args, kwargs = emit.call_args_list[0]
    assert args[0] == "memory_drift_rebuild_recommended"
    assert kwargs["metadata"]["drift_pct"] == 12.0


@pytest.mark.asyncio
async def test_rebuild_alert_does_not_refire_while_still_above():
    """Two consecutive ticks both above 10% — dedup rule says we
    should NOT emit rebuild twice (dedup happens at the emit call
    via ``ai_core_alerts`` unique-id, but the watcher's own logic
    skips the second call entirely by requiring ``prev < REBUILD``)."""
    emit = AsyncMock()
    with patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=_mock_snap(12.0)),
    ), patch("services.ai_core_alerts.emit", new=emit):
        await check_and_alert()
        emit.reset_mock()
        # Second tick same drift — crossing rule must NOT fire.
        await check_and_alert()

    rebuild_calls = [
        c for c in emit.call_args_list
        if c.args[0] == "memory_drift_rebuild_recommended"
    ]
    assert len(rebuild_calls) == 0, "rebuild alert fired twice"


# ── 2. Sudden jump ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jump_alert_fires_when_pct_increases_more_than_threshold():
    """Jump from 2% → 8% (+6pts) — above the 5pt jump threshold."""
    emit = AsyncMock()
    with patch("services.ai_core_alerts.emit", new=emit):
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(2.0)),
        ):
            await check_and_alert()
        emit.reset_mock()
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(8.0)),
        ):
            result = await check_and_alert()

    assert "jump" in result["fired"]
    jump_calls = [
        c for c in emit.call_args_list
        if c.args[0] == "memory_drift_jump"
    ]
    assert len(jump_calls) == 1
    meta = jump_calls[0].kwargs["metadata"]
    assert meta["prev_pct"] == 2.0
    assert meta["drift_pct"] == 8.0
    assert meta["delta_pct"] == 6.0


@pytest.mark.asyncio
async def test_small_jump_does_not_fire():
    """3% → 6% (+3pts) — under the 5pt threshold, no alert."""
    emit = AsyncMock()
    with patch("services.ai_core_alerts.emit", new=emit):
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(3.0)),
        ):
            await check_and_alert()
        emit.reset_mock()
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(6.0)),
        ):
            result = await check_and_alert()

    assert "jump" not in result["fired"]
    jump_calls = [
        c for c in emit.call_args_list
        if c.args[0] == "memory_drift_jump"
    ]
    assert jump_calls == []


@pytest.mark.asyncio
async def test_jump_rule_ignores_drops():
    """15% → 2% is good news — must not fire the jump alert (that's
    reserved for positive jumps). Recovery branch handles drops."""
    emit = AsyncMock()
    # First set up the "rebuild alert fired earlier today" state.
    with patch("services.ai_core_alerts.emit", new=emit):
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(15.0)),
        ):
            await check_and_alert()
        emit.reset_mock()
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(2.0)),
        ):
            result = await check_and_alert()

    assert "jump" not in result["fired"]


# ── 3. Recovery ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recovery_alert_fires_after_prior_rebuild_alert():
    """Rebuild alert fired → drift drops to < 1% → recovery alert
    emitted, letting the operator close the incident."""
    emit = AsyncMock()
    with patch("services.ai_core_alerts.emit", new=emit):
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(12.0)),
        ):
            await check_and_alert()
        emit.reset_mock()
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(0.0)),
        ):
            result = await check_and_alert()

    assert "recovered" in result["fired"]
    recovery_calls = [
        c for c in emit.call_args_list
        if c.args[0] == "memory_drift_recovered"
    ]
    assert len(recovery_calls) == 1


@pytest.mark.asyncio
async def test_recovery_alert_does_not_fire_without_prior_rebuild():
    """Drift goes from 5% to 0% — never crossed the rebuild band,
    so no recovery alert is needed."""
    emit = AsyncMock()
    with patch("services.ai_core_alerts.emit", new=emit):
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(5.0)),
        ):
            await check_and_alert()
        emit.reset_mock()
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(0.0)),
        ):
            result = await check_and_alert()

    assert "recovered" not in result["fired"]
    recovery_calls = [
        c for c in emit.call_args_list
        if c.args[0] == "memory_drift_recovered"
    ]
    assert recovery_calls == []


# ── 4. Quiet path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_alerts_in_quiet_path():
    """Drift consistently in ok band → no alerts ever emit.
    Critical — this is the 99% case and the watcher must not
    become a spam source."""
    emit = AsyncMock()
    with patch("services.ai_core_alerts.emit", new=emit), patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=_mock_snap(0.5)),
    ):
        for _ in range(5):  # 25 simulated minutes
            result = await check_and_alert()
            assert result["fired"] == []

    assert emit.call_count == 0


@pytest.mark.asyncio
async def test_compute_failure_does_not_raise():
    """If the drift compute helper fails (e.g., Mongo blip), the
    watcher returns a failure sentinel rather than crashing the
    scheduler tick."""
    with patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=None),
    ):
        result = await check_and_alert()

    assert result == {"ok": False, "reason": "drift_compute_failed"}


# ── Constants sanity ───────────────────────────────────────────────


def test_thresholds_match_drift_endpoint_classifier():
    """The watcher's REBUILD_PCT must equal the drift endpoint's
    ``investigate → rebuild`` boundary so the dashboard and the
    alert channel agree on what "rebuild recommended" means."""
    from routes.admin_memory_drift import THRESHOLD_INVESTIGATE_PCT, THRESHOLD_OK_PCT
    assert REBUILD_PCT == THRESHOLD_INVESTIGATE_PCT
    assert OK_PCT == THRESHOLD_OK_PCT


def test_jump_threshold_is_operator_friendly():
    """A 5pt jump in 5 minutes is a meaningful regression; lower
    would page on noise. Documented here so the value isn't
    tweaked unthinkingly."""
    assert JUMP_PCT == 5.0


# ── 5. Email dispatch ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_alert_dispatches_email(monkeypatch):
    """Rebuild-recommended alerts are actionable — the operator
    should hear about them on the email channel as well as in
    the in-Mongo queue."""
    monkeypatch.setenv("DRIFT_ALERT_EMAIL_ENABLED", "true")
    monkeypatch.setenv("OWNER_EMAIL", "ops@example.com")
    emit = AsyncMock(return_value={"ok": True, "deduped": False, "id": "x"})
    routed_send = AsyncMock(return_value=True)
    with patch("services.ai_core_alerts.emit", new=emit), patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=_mock_snap(12.0)),
    ), patch("services.email_service._routed_send", new=routed_send):
        await check_and_alert()

    assert routed_send.call_count == 1
    to_arg, subject, _ = routed_send.call_args.args
    assert to_arg == ["ops@example.com"]
    assert "RISEDUAL" in subject
    assert "rebuild" in subject.lower()


@pytest.mark.asyncio
async def test_jump_alert_does_not_dispatch_email(monkeypatch):
    """Jump alerts can fire multiple times per day per (from→to)
    bucket. They go to the in-Mongo queue only — emailing every
    jump would clog the operator inbox."""
    monkeypatch.setenv("DRIFT_ALERT_EMAIL_ENABLED", "true")
    monkeypatch.setenv("OWNER_EMAIL", "ops@example.com")
    emit = AsyncMock(return_value={"ok": True, "deduped": False, "id": "x"})
    routed_send = AsyncMock(return_value=True)
    with patch("services.ai_core_alerts.emit", new=emit), patch(
        "services.email_service._routed_send", new=routed_send,
    ):
        # Prime the watcher at 2% so the second tick at 8% triggers
        # the jump rule but stays under the rebuild threshold.
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(2.0)),
        ):
            await check_and_alert()
        with patch(
            "services.drift_alert_watcher._compute_drift",
            new=AsyncMock(return_value=_mock_snap(8.0)),
        ):
            await check_and_alert()

    assert routed_send.call_count == 0, (
        "Jump alerts must NOT dispatch email — Mongo queue only."
    )


@pytest.mark.asyncio
async def test_email_dispatch_skipped_when_flag_off(monkeypatch):
    """Operators muting the email channel via ``DRIFT_ALERT_EMAIL_ENABLED=false``
    must not receive emails even on actionable alerts. The Mongo
    queue still records them."""
    monkeypatch.setenv("DRIFT_ALERT_EMAIL_ENABLED", "false")
    monkeypatch.setenv("OWNER_EMAIL", "ops@example.com")
    emit = AsyncMock(return_value={"ok": True, "deduped": False, "id": "x"})
    routed_send = AsyncMock(return_value=True)
    with patch("services.ai_core_alerts.emit", new=emit), patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=_mock_snap(12.0)),
    ), patch("services.email_service._routed_send", new=routed_send):
        await check_and_alert()

    assert emit.call_count == 1, "Mongo queue still receives the alert"
    assert routed_send.call_count == 0, "Email channel muted by flag"


@pytest.mark.asyncio
async def test_deduped_alert_does_not_resend_email(monkeypatch):
    """When ``ai_core_alerts.emit`` reports ``deduped=True`` (same
    type/day already in the queue), we must NOT spam the operator's
    inbox with a second copy."""
    monkeypatch.setenv("DRIFT_ALERT_EMAIL_ENABLED", "true")
    monkeypatch.setenv("OWNER_EMAIL", "ops@example.com")
    emit = AsyncMock(return_value={"ok": True, "deduped": True, "id": "x"})
    routed_send = AsyncMock(return_value=True)
    with patch("services.ai_core_alerts.emit", new=emit), patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=_mock_snap(12.0)),
    ), patch("services.email_service._routed_send", new=routed_send):
        await check_and_alert()

    assert routed_send.call_count == 0, (
        "Dedup should suppress the email send; operator already got it."
    )


# ── 6. Drift history sample persistence ────────────────────────────


@pytest.mark.asyncio
async def test_check_and_alert_records_drift_sample_every_tick(monkeypatch):
    """Sparkline source: every successful tick must persist a
    drift snapshot, even on the quiet path. This is the time-series
    that powers the dashboard sparkline."""
    record = AsyncMock(return_value=None)
    emit = AsyncMock(return_value={"ok": True, "deduped": False, "id": "x"})
    with patch(
        "services.mongo_chroma_sync_metrics.record_drift_sample",
        new=record,
    ), patch("services.ai_core_alerts.emit", new=emit), patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=_mock_snap(0.5)),  # quiet path
    ):
        await check_and_alert()

    assert record.call_count == 1
    kwargs = record.call_args.kwargs
    assert kwargs["drift_pct"] == 0.5
    assert kwargs["mongo_total"] == 500
    assert kwargs["chroma_total"] == 450


@pytest.mark.asyncio
async def test_drift_history_record_failure_does_not_block_alerts(monkeypatch):
    """If the sparkline write fails (Mongo blip), the alert
    pipeline must still run. History is observability; alerts are
    operational."""
    record = AsyncMock(side_effect=RuntimeError("mongo down"))
    emit = AsyncMock(return_value={"ok": True, "deduped": False, "id": "x"})
    with patch(
        "services.mongo_chroma_sync_metrics.record_drift_sample",
        new=record,
    ), patch("services.ai_core_alerts.emit", new=emit), patch(
        "services.drift_alert_watcher._compute_drift",
        new=AsyncMock(return_value=_mock_snap(12.0)),
    ):
        result = await check_and_alert()

    assert result["ok"] is True
    assert "rebuild" in result["fired"]
    assert emit.call_count == 1
