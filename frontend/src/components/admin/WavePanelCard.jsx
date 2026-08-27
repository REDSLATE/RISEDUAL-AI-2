import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertOctagon, RefreshCw, ShieldAlert, TrendingUp, XCircle,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api/admin/alpha-daytrader/wave-observations`;

/**
 * WavePanelCard — real-time Wave Intelligence state per symbol.
 *
 * Wave Intelligence is a per-symbol observe-only regime machine
 * ({WAIT | TREND_FOLLOW | RANGE_GRID | DANGER_PAUSE}) that catches
 * per-symbol volatility shocks Alpha's SPY-wide regime detectors
 * can't see. DANGER_PAUSE is a hard veto — Alpha refuses to arm any
 * setup on symbols in that mode.
 *
 * Card shows:
 *   * Current mode distribution over the last N hours (default 4h)
 *   * Danger leaderboard — top symbols by max danger score
 *
 * Auto-refreshes every 45s.
 */
const MODE_STYLES = {
  DANGER_PAUSE: { icon: AlertOctagon, cls: 'bg-red-500/10     border-red-500/40     text-red-300' },
  TREND_FOLLOW: { icon: TrendingUp,   cls: 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300' },
  RANGE_GRID:   { icon: Activity,     cls: 'bg-sky-500/10     border-sky-500/40     text-sky-300' },
  WAIT:         { icon: ShieldAlert,  cls: 'bg-slate-500/10   border-slate-500/40   text-slate-300' },
};

const WavePanelCard = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await authFetch(`${API}?since_hours=4&limit=50`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 45_000);
    return () => clearInterval(t);
  }, [load]);

  const total = useMemo(() => {
    const counts = data?.mode_counts || {};
    return Object.values(counts).reduce((s, n) => s + Number(n || 0), 0);
  }, [data]);

  if (loading && !data) {
    return (
      <div data-testid="wave-panel-card-loading" className="rounded-lg border border-slate-700 bg-slate-900/50 p-4">
        <div className="flex items-center gap-2 text-slate-400 text-sm">
          <RefreshCw className="w-4 h-4 animate-spin" /> Loading Wave Intelligence…
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div data-testid="wave-panel-card-error" className="rounded-lg border border-red-500/40 bg-red-500/10 p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-red-300 text-sm">
            <XCircle className="w-4 h-4" /> Wave: {error}
          </div>
          <button
            data-testid="wave-panel-retry-btn"
            onClick={load}
            className="text-xs px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const counts = data?.mode_counts || {};
  const leaderboard = data?.danger_leaderboard || [];
  const modes = ['DANGER_PAUSE', 'TREND_FOLLOW', 'RANGE_GRID', 'WAIT'];

  return (
    <div
      data-testid="wave-panel-card"
      className="rounded-lg border border-slate-700 bg-slate-900/50 p-4 space-y-4"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-200 flex items-center gap-2">
            <Activity className="w-4 h-4" /> Wave Intelligence
          </div>
          <div className="text-xs text-slate-500">
            Per-symbol regime · DANGER_PAUSE vetoes trades · last {data?.since_hours || 4}h
          </div>
        </div>
        <button
          data-testid="wave-panel-refresh-btn"
          onClick={load}
          className="text-xs px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 flex items-center gap-1"
          title="Refresh"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      {/* Mode distribution */}
      <div>
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">
          Mode distribution
        </div>
        {total === 0 && (
          <div
            data-testid="wave-panel-modes-empty"
            className="text-xs text-slate-500"
          >
            No Wave observations recorded yet — Alpha needs ≥5 daily bars per symbol to evaluate.
          </div>
        )}
        {total > 0 && (
          <div
            data-testid="wave-panel-modes"
            className="grid grid-cols-2 md:grid-cols-4 gap-2"
          >
            {modes.map((m) => {
              const n = Number(counts[m] || 0);
              const pct = total > 0 ? (n / total) * 100 : 0;
              const { icon: Icon, cls } = MODE_STYLES[m] || MODE_STYLES.WAIT;
              return (
                <div
                  key={m}
                  data-testid={`wave-panel-mode-${m}`}
                  className={`rounded-md border p-2 ${cls}`}
                >
                  <div className="flex items-center gap-1.5 text-xs font-semibold">
                    <Icon className="w-3.5 h-3.5" />
                    {m.replace('_', ' ')}
                  </div>
                  <div className="mt-1 text-lg font-mono">{n}</div>
                  <div className="text-[10px] opacity-70 font-mono">
                    {pct.toFixed(0)}% of {total}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Danger leaderboard */}
      <div>
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-2 flex items-center gap-1">
          <AlertOctagon className="w-3 h-3" /> Danger leaderboard (top {leaderboard.length || 0})
        </div>
        {leaderboard.length === 0 && (
          <div
            data-testid="wave-panel-leaderboard-empty"
            className="text-xs text-slate-500"
          >
            No elevated-danger symbols right now — safe conditions across the universe.
          </div>
        )}
        {leaderboard.length > 0 && (
          <div data-testid="wave-panel-leaderboard" className="space-y-1">
            {leaderboard.slice(0, 5).map((row, i) => {
              const danger = Number(row.max_danger || 0);
              const barCls = danger >= 0.72
                ? 'bg-red-500/70'
                : danger >= 0.5
                  ? 'bg-orange-500/70'
                  : 'bg-slate-500/50';
              return (
                <div
                  key={row.symbol || i}
                  data-testid={`wave-panel-leaderboard-row-${i}`}
                  className="flex items-center gap-2 text-xs"
                >
                  <span className="w-14 font-mono text-slate-200 truncate">
                    {row.symbol}
                  </span>
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-400 uppercase tracking-wide"
                    data-testid={`wave-panel-leaderboard-mode-${i}`}
                  >
                    {(row.latest_mode || '—').replace('_', ' ')}
                  </span>
                  <div className="flex-1 h-2 rounded bg-slate-800 overflow-hidden">
                    <div
                      className={`h-full ${barCls}`}
                      style={{ width: `${Math.min(100, danger * 100)}%` }}
                    />
                  </div>
                  <span className="w-12 text-right font-mono text-slate-300">
                    {danger.toFixed(2)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default WavePanelCard;
