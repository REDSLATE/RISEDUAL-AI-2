import React, { useEffect, useState, useCallback } from 'react';
import { Eye, Snowflake, XCircle, AlertCircle, RefreshCcw } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Ticker Abandonment / Cooldown table — admin Terminal tab.
 *
 * Polls /api/admin/ticker-abandonment every 60s. Renders one row per
 * (lane, symbol) discovered in the rolling window with the gate's
 * current decision. Color-coded: ABANDON rose, COOLDOWN amber,
 * KEEP slate. Sort order matches the backend (ABANDON → COOLDOWN
 * by cooldown desc → KEEP alpha).
 */
const ICONS = {
  KEEP: Eye,
  COOLDOWN: Snowflake,
  ABANDON: XCircle,
};

const STYLE_BY_ACTION = {
  ABANDON: {
    border: 'border-rose-500/40',
    bg: 'bg-rose-500/5',
    text: 'text-rose-300',
    pill: 'bg-rose-500/20 text-rose-200 border-rose-500/40',
  },
  COOLDOWN: {
    border: 'border-amber-500/30',
    bg: 'bg-amber-500/5',
    text: 'text-amber-300',
    pill: 'bg-amber-500/20 text-amber-200 border-amber-500/30',
  },
  KEEP: {
    border: 'border-slate-700/50',
    bg: 'bg-slate-900/30',
    text: 'text-slate-400',
    pill: 'bg-slate-800/60 text-slate-300 border-slate-700/50',
  },
};

const cooldownLabel = (mins) => {
  if (!mins) return '';
  if (mins >= 1440) return `${Math.round(mins / 1440)}d`;
  if (mins >= 60) return `${Math.round(mins / 60)}h`;
  return `${mins}m`;
};

const TickerAbandonmentTable = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = useCallback(async () => {
    setRefreshing(true);
    try {
      const r = await authFetch(`${API}/admin/ticker-abandonment`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError(null);
    } catch (e) {
      setError(e.message || 'fetch failed');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, [fetchData]);

  if (error) {
    return (
      <div
        data-testid="ticker-abandonment-error"
        className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-300"
      >
        Ticker Abandonment error: {error}
      </div>
    );
  }
  if (!data) return null;

  const { rows = [], totals = {}, window_days } = data;

  return (
    <div
      data-testid="ticker-abandonment-table"
      className="rounded-xl border border-slate-700/60 bg-slate-900/30 p-3"
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wider text-slate-400">
        <div className="flex items-center gap-2">
          <AlertCircle className="h-3.5 w-3.5" />
          Ticker Abandonment · last {window_days}d
        </div>
        <div className="flex items-center gap-2 normal-case">
          <span
            data-testid="ticker-abandonment-totals"
            className="flex items-center gap-1 rounded-md bg-slate-900/50 px-2 py-0.5 text-[11px] text-slate-300"
          >
            <span className="text-rose-300 tabular-nums">{totals.abandon ?? 0}</span>
            <span className="text-slate-600">abandon</span>
            <span className="text-slate-700">·</span>
            <span className="text-amber-300 tabular-nums">{totals.cooldown ?? 0}</span>
            <span className="text-slate-600">cooldown</span>
            <span className="text-slate-700">·</span>
            <span className="text-slate-300 tabular-nums">{totals.keep ?? 0}</span>
            <span className="text-slate-600">keep</span>
          </span>
          <button
            type="button"
            onClick={fetchData}
            disabled={refreshing}
            data-testid="ticker-abandonment-refresh"
            className="rounded-md border border-slate-700/60 bg-slate-900/50 p-1 text-slate-400 hover:bg-slate-800/60 disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCcw className={`h-3 w-3 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="py-6 text-center text-[11px] text-slate-500">
          No tickers tracked in the last {window_days}d. Bots haven&apos;t
          fired any signals or trades yet.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]" data-testid="ticker-abandonment-rows">
            <thead className="text-[10px] uppercase tracking-wider text-slate-500">
              <tr className="border-b border-slate-800/60">
                <th className="px-2 py-1.5 text-left">Sym</th>
                <th className="px-1 py-1.5 text-left">Lane</th>
                <th className="px-1 py-1.5 text-center">Action</th>
                <th className="px-2 py-1.5 text-left font-normal normal-case text-slate-500">
                  Reason
                </th>
                <th className="px-1 py-1.5 text-right">Sig</th>
                <th className="px-1 py-1.5 text-right">Rej</th>
                <th className="px-1 py-1.5 text-right text-emerald-400/60">W</th>
                <th className="px-1 py-1.5 text-right text-rose-400/60">L</th>
                <th className="px-1 py-1.5 text-right">Conf</th>
                <th className="px-1 py-1.5 text-right">RR</th>
                <th className="px-1 py-1.5 text-right">Cool</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const action = row.decision?.action || 'KEEP';
                const style = STYLE_BY_ACTION[action] || STYLE_BY_ACTION.KEEP;
                const Icon = ICONS[action] || Eye;
                const inputs = row.inputs || {};
                return (
                  <tr
                    key={`${row.lane}-${row.symbol}`}
                    className={`border-b border-slate-800/30 ${style.bg}`}
                    data-testid={`ticker-abandonment-row-${row.symbol}`}
                  >
                    <td className={`px-2 py-1.5 font-semibold tabular-nums ${style.text}`}>
                      {row.symbol}
                    </td>
                    <td className="px-1 py-1.5 text-[10px] text-slate-500">
                      {row.lane}
                    </td>
                    <td className="px-1 py-1.5 text-center">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${style.pill}`}
                        data-testid={`ticker-abandonment-action-${row.symbol}`}
                      >
                        <Icon className="h-2.5 w-2.5" />
                        {action}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-slate-500">
                      {row.decision?.reason || '—'}
                    </td>
                    <td className="px-1 py-1.5 text-right tabular-nums text-slate-300">
                      {inputs.recent_signals ?? 0}
                    </td>
                    <td className="px-1 py-1.5 text-right tabular-nums text-slate-400">
                      {inputs.recent_rejections ?? 0}
                    </td>
                    <td className="px-1 py-1.5 text-right tabular-nums text-emerald-300/80">
                      {inputs.recent_wins ?? 0}
                    </td>
                    <td className="px-1 py-1.5 text-right tabular-nums text-rose-300/80">
                      {inputs.recent_losses ?? 0}
                    </td>
                    <td className="px-1 py-1.5 text-right tabular-nums text-slate-400">
                      {(inputs.avg_confidence ?? 0).toFixed(2)}
                    </td>
                    <td className="px-1 py-1.5 text-right tabular-nums text-slate-400">
                      {(inputs.avg_rr ?? 0).toFixed(2)}
                    </td>
                    <td className={`px-1 py-1.5 text-right tabular-nums ${style.text}`}>
                      {cooldownLabel(row.decision?.cooldown_minutes)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default TickerAbandonmentTable;
