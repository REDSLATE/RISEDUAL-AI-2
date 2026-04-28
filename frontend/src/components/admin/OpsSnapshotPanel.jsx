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

  const fetchSnap = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const res = await authFetch(`${API}/admin/ops-snapshot`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSnap(await res.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSnap(); }, [fetchSnap]);

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

      {/* Heuristic notes — single most useful element on the page */}
      <Section icon={AlertTriangle} title={`Notes (${snap.notes?.length || 0})`}>
        <ul className="space-y-1.5" data-testid="ops-notes-list">
          {(snap.notes || []).map((n, i) => {
            const isAllGood = n === 'All gauges nominal.';
            return (
              <li
                key={i}
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
                <StatusDot ok={snap.scheduler?.ok} /> Scheduler heartbeat
              </span>
              <span className="text-slate-400" data-testid="ops-scheduler-status">
                {snap.scheduler?.last_signal_at
                  ? fmtAge(snap.scheduler.age_seconds)
                  : (snap.scheduler?.error || '—')}
              </span>
            </div>
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
