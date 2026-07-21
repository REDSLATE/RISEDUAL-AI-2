import React, { useState, useEffect, useCallback } from 'react';
import { Trash2, RefreshCw, ShieldCheck, AlertTriangle, Clock } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { toast } from '../ui/sonner';

const API = `${getApiBase()}/api`;

export default function RetentionPanel() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [purging, setPurging] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API}/admin/retention/status`);
      if (!res.ok) throw new Error(`status ${res.status}`);
      setStatus(await res.json());
    } catch (e) {
      setError(e.message || 'failed to load retention status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const purge = useCallback(async () => {
    setPurging(true);
    try {
      const res = await authFetch(`${API}/admin/retention/purge`, { method: 'POST' });
      if (!res.ok) throw new Error(`purge failed ${res.status}`);
      const data = await res.json();
      toast.success(`Purged ${data.purged_total.toLocaleString()} rows in ${data.duration_ms}ms`, {
        description: data.more_remains ? 'More remains — click again to continue draining' : 'Backlog clear',
      });
      await load();
    } catch (e) {
      toast.error(e.message || 'purge failed');
    } finally {
      setPurging(false);
    }
  }, [load]);

  if (loading && !status) {
    return <div className="text-slate-400 text-sm">Loading retention status…</div>;
  }
  if (error) {
    return <div className="text-red-400 text-sm">{error}</div>;
  }
  if (!status) return null;

  const {
    retention_hours_default: retentionDefault,
    retention_hours_paper: retentionPaper,
    purge_batch_cap: batchCap,
    total_backlog: totalBacklog,
    more_remains: moreRemains,
    breakdown,
    preserved_collections: preserved,
    last_purge: lastPurge,
    total_purged_lifetime: lifetime,
  } = status;

  return (
    <div className="space-y-6" data-testid="retention-panel">
      {/* Header + big button */}
      <div className="flex items-start justify-between gap-6 flex-wrap">
        <div>
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <Clock className="w-5 h-5 text-amber-400" />
            Data Retention
          </h3>
          <p className="text-sm text-slate-400 mt-1 max-w-xl">
            Telemetry older than <span className="text-amber-300">{retentionDefault}h</span> (paper trades: <span className="text-amber-300">{retentionPaper}h</span>) is eligible for purge.
            Executed trades &amp; user data are <span className="text-emerald-400">never</span> touched.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            disabled={loading}
            className="px-3 py-2 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs flex items-center gap-1.5 disabled:opacity-50"
            data-testid="retention-refresh-btn"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={purge}
            disabled={purging || totalBacklog === 0}
            className="px-4 py-2 rounded bg-red-600 hover:bg-red-500 text-white text-sm font-semibold flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed"
            data-testid="purge-backlog-btn"
          >
            <Trash2 className={`w-4 h-4 ${purging ? 'animate-pulse' : ''}`} />
            {purging ? 'Purging…' : 'PURGE BACKLOG NOW'}
          </button>
        </div>
      </div>

      {/* Summary tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Tile label="Expired backlog" value={totalBacklog.toLocaleString()} accent={totalBacklog > 0 ? 'amber' : 'emerald'} testid="retention-backlog-tile" />
        <Tile label="Batch cap / call" value={batchCap.toLocaleString()} testid="retention-batch-cap-tile" />
        <Tile label="Purged lifetime" value={lifetime.toLocaleString()} accent="cyan" testid="retention-lifetime-tile" />
        <Tile
          label="Last purge"
          value={lastPurge ? new Date(lastPurge.finished_at).toLocaleTimeString() : '—'}
          sub={lastPurge ? `${lastPurge.purged_total.toLocaleString()} rows · ${lastPurge.triggered_by}` : 'never'}
          testid="retention-lastpurge-tile"
        />
      </div>

      {/* More remains banner */}
      {moreRemains && (
        <div className="flex items-center gap-2 px-4 py-3 bg-amber-950/40 border border-amber-800/60 rounded" data-testid="retention-more-remains-banner">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <div className="text-sm text-amber-100">
            <span className="font-semibold">More remains.</span> Click <span className="font-mono">PURGE BACKLOG NOW</span> again until it comes back clean.
          </div>
        </div>
      )}

      {/* Per-collection table */}
      <div className="border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm" data-testid="retention-breakdown-table">
          <thead className="bg-slate-900/60 text-xs uppercase text-slate-400">
            <tr>
              <th className="text-left px-4 py-2 font-normal">Collection</th>
              <th className="text-right px-4 py-2 font-normal">TTL</th>
              <th className="text-right px-4 py-2 font-normal">Total rows</th>
              <th className="text-right px-4 py-2 font-normal">Expired backlog</th>
              <th className="text-left px-4 py-2 font-normal pl-6">Notes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {breakdown.map((row) => (
              <tr key={row.collection} className="hover:bg-slate-900/40">
                <td className="px-4 py-2 text-slate-200 font-mono text-xs">{row.collection}</td>
                <td className="px-4 py-2 text-right text-slate-400">{row.ttl_hours}h</td>
                <td className="px-4 py-2 text-right text-slate-300 tabular-nums">{row.total.toLocaleString()}</td>
                <td className={`px-4 py-2 text-right tabular-nums font-semibold ${row.expired_backlog > 0 ? 'text-amber-300' : 'text-slate-600'}`}>
                  {row.expired_backlog.toLocaleString()}{row.backlog_capped_at ? '+' : ''}
                </td>
                <td className="px-4 py-2 pl-6 text-xs text-slate-500">
                  {row.label}
                  {row.preserves_active && <span className="ml-2 text-emerald-500">· preserves active</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Preserved-forever list */}
      <div className="border border-emerald-900/50 rounded-lg p-4 bg-emerald-950/20">
        <div className="flex items-center gap-2 text-emerald-300 text-sm font-semibold">
          <ShieldCheck className="w-4 h-4" />
          Never purged (source of truth)
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {preserved.map((c) => (
            <span key={c} className="px-2 py-0.5 text-xs font-mono bg-emerald-900/40 border border-emerald-800/50 text-emerald-200 rounded">
              {c}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function Tile({ label, value, sub, accent, testid }) {
  const accentClass = {
    amber: 'text-amber-300',
    emerald: 'text-emerald-300',
    cyan: 'text-cyan-300',
  }[accent] || 'text-slate-100';
  return (
    <div className="border border-slate-800 rounded-lg p-3 bg-slate-900/40" data-testid={testid}>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`text-2xl font-semibold tabular-nums ${accentClass}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}
