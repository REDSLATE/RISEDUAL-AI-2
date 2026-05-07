import React, { useCallback, useEffect, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle, Database, Cpu, RefreshCw, Server } from 'lucide-react';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

const fmtAge = (s) => {
  if (s == null) return '—';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h ago`;
  return `${(s / 86400).toFixed(1)}d ago`;
};

const StatusDot = ({ ok }) => (
  <span
    className={`inline-block w-2 h-2 rounded-full ${
      ok === true ? 'bg-emerald-400' : ok === false ? 'bg-red-400' : 'bg-slate-500'
    }`}
  />
);

const Section = ({ icon: Icon, title, children }) => (
  <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-4">
    <div className="flex items-center gap-2 mb-3">
      <Icon className="w-4 h-4 text-[#3DE8D9]" />
      <h3 className="text-white text-sm font-semibold">{title}</h3>
    </div>
    {children}
  </div>
);

const OpsSnapshotPanel = () => {
  const [snap, setSnap] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [alerter, setAlerter] = useState(null);
  const [triggering, setTriggering] = useState(false);

  const fetchSnap = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const [snapRes, alertRes] = await Promise.all([
        authFetch(`${API}/admin/ops-snapshot`),
        authFetch(`${API}/admin/ops-alerter/status`),
      ]);
      if (!snapRes.ok) throw new Error(`HTTP ${snapRes.status}`);
      setSnap(await snapRes.json());
      if (alertRes.ok) setAlerter(await alertRes.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const triggerAlerter = useCallback(async () => {
    setTriggering(true);
    try {
      const r = await authFetch(`${API}/admin/ops-alerter/run`, { method: 'POST' });
      if (r.ok) {
        const out = await r.json();
        // Refresh status panel after manual run
        const s = await authFetch(`${API}/admin/ops-alerter/status`);
        if (s.ok) setAlerter(await s.json());
        const summary = out.posted_alert
          ? `Posted ${out.fresh_alerts.length} alert(s)`
          : out.posted_resolved
          ? `Posted ${out.resolved_alerts.length} resolved`
          : 'No transitions to post';
        // eslint-disable-next-line no-alert
        window.alert(summary);
      }
    } finally {
      setTriggering(false);
    }
  }, []);

  useEffect(() => { fetchSnap(); }, [fetchSnap]);

  // Auto-poll every 60s so the panel never shows a stale snapshot
  // after a redeploy / scheduler restart. 60s is the same cadence
  // the backend heartbeat job runs at; faster polling would just
  // burn DB reads without adding signal.
  useEffect(() => {
    const id = setInterval(fetchSnap, 60_000);
    return () => clearInterval(id);
  }, [fetchSnap]);

  if (loading && !snap) {
    return <div className="p-6 text-slate-400 text-sm" data-testid="ops-loading">Loading ops snapshot…</div>;
  }
  if (err) {
    return <div className="p-6 text-red-400 text-sm" data-testid="ops-error">Failed to load: {err}</div>;
  }
  if (!snap) return null;

  return (
    <div className="p-4 sm:p-6 space-y-4" data-testid="ops-snapshot-panel">
      {/* Header / refresh */}
      <div className="flex items-center justify-between">
        <div className="text-slate-400 text-xs">
          Generated {snap.generated_at && new Date(snap.generated_at).toLocaleString()}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={fetchSnap}
          disabled={loading}
          className="bg-slate-900 border-slate-600 text-white"
          data-testid="ops-refresh-btn"
        >
          <RefreshCw className={`w-3.5 h-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {/* Wedge detector banner */}
      {alerter && (
        <div
          className={`rounded-lg p-3 border text-xs flex items-center justify-between gap-3 ${
            alerter.configured
              ? 'bg-emerald-500/5 border-emerald-500/30 text-emerald-200'
              : 'bg-slate-800/40 border-slate-700/50 text-slate-300'
          }`}
          data-testid="ops-alerter-banner"
        >
          <div className="flex items-center gap-2 min-w-0">
            <StatusDot ok={alerter.configured} />
            <div className="min-w-0">
              <div className="font-medium">
                {alerter.configured
                  ? 'Wedge detector active — webhook configured'
                  : 'Wedge detector inactive — set OPS_ALERT_WEBHOOK_URL in .env to enable'}
              </div>
              <div className="text-[11px] opacity-80">
                Diffs notes against last run; posts on transitions; dedup window 4h.
                Last update: {alerter.updated_at ? new Date(alerter.updated_at).toLocaleString() : 'never'}.
              </div>
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={triggerAlerter}
            disabled={triggering}
            className="bg-slate-900 border-slate-600 text-white shrink-0"
            data-testid="ops-alerter-run-btn"
          >
            {triggering ? 'Running…' : 'Run now'}
          </Button>
        </div>
      )}

      {/* Heuristic notes — single most useful element on the page */}
      <Section icon={AlertTriangle} title={`Notes (${snap.notes?.length || 0})`}>
        <ul className="space-y-1.5" data-testid="ops-notes-list">
          {(snap.notes || []).map((n, i) => {
            const isAllGood = n === 'All gauges nominal.';
            return (
              <li
                key={`${i}-${n}`}
                className={`text-xs flex items-start gap-2 ${
                  isAllGood ? 'text-emerald-300' : 'text-amber-200'
                }`}
                data-testid={`ops-note-${i}`}
              >
                {isAllGood ? (
                  <CheckCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                ) : (
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                )}
                <span>{n}</span>
              </li>
            );
          })}
        </ul>
      </Section>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Connectivity */}
        <Section icon={Database} title="Connectivity">
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-slate-300 flex items-center gap-2">
                <StatusDot ok={snap.mongo?.ok} /> Mongo
              </span>
              <span className="text-slate-400" data-testid="ops-mongo-status">
                {snap.mongo?.ok
                  ? `${snap.mongo.ping_ms}ms`
                  : (snap.mongo?.error || '—')}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-300 flex items-center gap-2">
                {/* Primary scheduler-health signal: in-process status.
                    Was: ``snap.scheduler?.ok`` from the heartbeat-doc
                    chain — that path passes through APScheduler →
                    motor → Mongo write → eventually-consistent read,
                    any of which can silently fail and surface as
                    "scheduler dead" even when it isn't. The in-process
                    check passes through one Python global. */}
                <StatusDot ok={
                  snap.scheduler?.in_process
                    ? !!snap.scheduler.in_process.running
                    : snap.scheduler?.ok
                } /> Scheduler
              </span>
              <span className="text-slate-400" data-testid="ops-scheduler-status">
                {snap.scheduler?.in_process?.running
                  ? `${snap.scheduler.in_process.jobs_count ?? 0} jobs`
                  : snap.scheduler?.in_process
                    ? 'NOT RUNNING'
                    : (snap.scheduler?.last_signal_at
                        ? fmtAge(snap.scheduler.age_seconds)
                        : (snap.scheduler?.error || '—'))}
              </span>
            </div>
            {/* In-process scheduler diagnostic — the smoking gun
                when heartbeat is stale. ``running: false`` means
                _start_schedulers() raised on boot; operator goes
                straight to logs instead of chasing a caching
                ghost. Surfaced unconditionally so a healthy state
                ("running · 14 jobs · next: morning_brief 23m")
                also reads as reassuring confirmation. */}
            {snap.scheduler?.in_process && (
              <div
                className="flex items-center justify-between pl-4"
                data-testid="ops-scheduler-in-process"
              >
                <span className="text-slate-500 text-[11px]">
                  ↳ in-process
                </span>
                <span className="text-[11px] font-mono">
                  {snap.scheduler.in_process.running ? (
                    <span data-testid="ops-scheduler-running" className="text-emerald-300">
                      running · {snap.scheduler.in_process.jobs_count ?? 0} jobs
                      {snap.scheduler.in_process.next_job_id && (
                        <span className="text-slate-500">
                          {' '}· next: {snap.scheduler.in_process.next_job_id}
                          {snap.scheduler.in_process.next_job_at &&
                            ` ${fmtAge(
                              Math.max(
                                0,
                                Math.floor(
                                  (new Date(snap.scheduler.in_process.next_job_at).getTime()
                                    - Date.now()) / 1000,
                                ),
                              ),
                            )}`}
                        </span>
                      )}
                      {snap.scheduler.in_process.overdue_jobs > 0 && (
                        <span className="text-amber-300">
                          {' '}· {snap.scheduler.in_process.overdue_jobs} overdue
                        </span>
                      )}
                    </span>
                  ) : (
                    <span data-testid="ops-scheduler-not-running" className="text-rose-300">
                      NOT RUNNING
                      {snap.scheduler.in_process.reason && (
                        <span className="text-slate-500"> · {snap.scheduler.in_process.reason}</span>
                      )}
                    </span>
                  )}
                </span>
              </div>
            )}
            {/* Mongo-write canary — only meaningful when the
                scheduler IS running but the heartbeat doc hasn't
                been updated. That points at the Mongo write path,
                not at the scheduler itself. Hidden when in-process
                and heartbeat agree, because both being green is
                redundant noise. */}
            {snap.scheduler?.in_process?.running &&
              snap.scheduler?.last_signal_at &&
              snap.scheduler?.age_seconds > 180 && (
                <div
                  className="flex items-center justify-between pl-4"
                  data-testid="ops-scheduler-mongo-canary"
                >
                  <span className="text-slate-500 text-[11px]">
                    ↳ heartbeat-doc canary
                  </span>
                  <span className="text-[11px] font-mono text-amber-300">
                    Mongo write stale · {fmtAge(snap.scheduler.age_seconds)}
                  </span>
                </div>
              )}
            {/* Boot-error block — surfaces the actual exception that
                killed _start_schedulers() on this pod's most recent
                boot. Replaces "go grep pod logs" with one glance. */}
            {snap.scheduler?.in_process?.boot_error && (
              <div
                className="mt-2 rounded-lg border border-rose-500/40 bg-rose-500/10 p-2.5 text-[11px]"
                data-testid="ops-scheduler-boot-error"
              >
                <div className="font-mono text-rose-200">
                  Boot failure ({snap.scheduler.in_process.boot_error.phase}) ·{' '}
                  <span data-testid="ops-scheduler-boot-error-type">
                    {snap.scheduler.in_process.boot_error.exc_type}
                  </span>
                </div>
                <div
                  className="mt-1 break-words font-mono text-rose-100/90"
                  data-testid="ops-scheduler-boot-error-msg"
                >
                  {snap.scheduler.in_process.boot_error.exc_message}
                </div>
                {snap.scheduler.in_process.boot_error.traceback_tail && (
                  <pre
                    className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-[10px] leading-snug text-rose-100/70"
                    data-testid="ops-scheduler-boot-error-trace"
                  >
                    {snap.scheduler.in_process.boot_error.traceback_tail}
                  </pre>
                )}
              </div>
            )}
          </div>
        </Section>

        {/* Runtime */}
        <Section icon={Cpu} title="Runtime">
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between text-slate-300">
              <span>Python</span><span className="text-slate-400">{snap.runtime?.python_version}</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span>CPU cores</span><span className="text-slate-400">{snap.runtime?.cpu_cores}</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span>Backend uptime</span>
              <span className="text-slate-400">{fmtAge(snap.runtime?.uptime_seconds).replace(' ago', '')}</span>
            </div>
          </div>
        </Section>

        {/* Tier 3 / Council */}
        <Section icon={Activity} title="Tier 3 / Council state">
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between text-slate-300">
              <span>Tier 3 progress</span>
              <span className="text-slate-400" data-testid="ops-tier3-progress">
                {snap.tier3?.tier3_progress_pct != null
                  ? `${snap.tier3.tier3_progress_pct.toFixed(1)}%`
                  : '—'}
              </span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span>Tier 3 unlocked</span>
              <Badge className={`${snap.tier3?.tier3_unlocked ? 'bg-emerald-500/20 text-emerald-300' : 'bg-slate-700/40 text-slate-300'} border-0 text-[10px]`}>
                {snap.tier3?.tier3_unlocked ? 'YES' : 'NO'}
              </Badge>
            </div>
            <div className="flex justify-between text-slate-300">
              <span>Adversarial phase</span>
              <span className="text-slate-400">{snap.tier3?.adversarial_phase || '—'}</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span>Council modulator</span>
              <Badge className={`${snap.tier3?.council_modulator_enabled ? 'bg-emerald-500/20 text-emerald-300' : 'bg-slate-700/40 text-slate-300'} border-0 text-[10px]`}>
                {snap.tier3?.council_modulator_enabled ? 'ON' : 'OFF'}
              </Badge>
            </div>
          </div>
        </Section>

        {/* Integrations */}
        <Section icon={Server} title="Integrations">
          <div className="space-y-1.5 text-xs" data-testid="ops-integrations-list">
            {(snap.integrations || []).map((i) => (
              <div key={i.env_var} className="flex items-center justify-between">
                <span className="text-slate-300 flex items-center gap-2">
                  <StatusDot ok={i.configured} /> {i.name}
                </span>
                <span className="text-slate-500 font-mono text-[10px]">
                  {i.configured ? 'configured' : 'unset'}
                </span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      {/* Operator flags — full table */}
      <Section icon={Server} title={`Operator flags (${snap.operator_flags?.length || 0})`}>
        <div className="space-y-1 text-xs font-mono" data-testid="ops-flags-list">
          {(snap.operator_flags || []).map((f) => (
            <div key={f.name} className="flex items-center justify-between gap-3">
              <span className="text-slate-300 truncate">{f.name}</span>
              <span className={`text-[11px] truncate text-right ml-2 ${f.set ? 'text-slate-200' : 'text-slate-500'}`}>
                {f.set ? f.value : '(unset)'}
              </span>
            </div>
          ))}
          {(snap.extra_flags || []).map((f) => (
            <div key={f.name} className="flex items-center justify-between gap-3">
              <span className="text-slate-400 truncate">{f.name} <span className="text-[9px] uppercase opacity-60">extra</span></span>
              <span className="text-slate-300 text-[11px] truncate text-right ml-2">{f.value}</span>
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
};

export default OpsSnapshotPanel;
