import React, { useEffect, useState, useCallback } from 'react';
import { Swords, Activity, Pause } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Adversarial Cores · 24h — compact at-a-glance chip for the admin
 * Terminal tab. Reads /api/admin/adversarial-cores/24h and renders:
 *   - Phase + enabled status (traffic-light)
 *   - 24h decision count + breakdown (LONG / SHORT_OR_AVOID / NO_TRADE)
 *   - Bull vs Bear win-rate spread (once closed rows exist)
 *   - Avg edge_gap + avg agent confidence
 *
 * Never blocks render on missing data — shows "no decisions yet"
 * empty state when the core is enabled but hasn't ticked yet, and a
 * muted "kill-switch off" state when env flag is unset.
 */
const AdversarialCoresChip = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/admin/adversarial-cores/24h`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(e.message || 'fetch failed');
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, [fetchData]);

  if (error) {
    return (
      <div
        className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-300"
        data-testid="adversarial-cores-error"
      >
        Adversarial Cores error: {error}
      </div>
    );
  }

  if (!data) return null;

  const {
    enabled, phase, decisions_24h, closed_24h, decision_counts, wins,
    avg_edge_gap, avg_bull_confidence, avg_bear_confidence,
    bull_win_rate, bear_win_rate, last_decision_at,
  } = data;

  let statusBorder = 'border-slate-700/60';
  let statusBg = 'bg-slate-800/30';
  let statusText = 'text-slate-300';
  let StatusIcon = Pause;
  let statusLabel = 'OFF';

  if (enabled && decisions_24h > 0) {
    statusBorder = 'border-emerald-500/40';
    statusBg = 'bg-emerald-500/5';
    statusText = 'text-emerald-300';
    StatusIcon = Activity;
    statusLabel = 'LEARNING';
  } else if (enabled) {
    statusBorder = 'border-amber-500/30';
    statusBg = 'bg-amber-500/5';
    statusText = 'text-amber-300';
    StatusIcon = Activity;
    statusLabel = 'IDLE';
  }

  const lastRunLabel = last_decision_at
    ? `${Math.max(0, Math.floor((Date.now() - new Date(last_decision_at).getTime()) / 60_000))}m ago`
    : '—';

  const winSpread =
    bull_win_rate != null && bear_win_rate != null
      ? ((bull_win_rate - bear_win_rate) * 100).toFixed(1)
      : null;

  return (
    <div
      data-testid="adversarial-cores-chip"
      className={`rounded-xl border ${statusBorder} ${statusBg} p-3`}
    >
      <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wider">
        <div className={`flex items-center gap-2 ${statusText}`}>
          <Swords className="h-3.5 w-3.5" /> Adversarial Cores · 24h
        </div>
        <div className="flex items-center gap-2">
          <span className={`${statusText} font-semibold`} data-testid="adversarial-cores-status">
            {statusLabel}
          </span>
          <span className="text-slate-500 text-[10px]">· {phase}</span>
          <StatusIcon className={`h-3.5 w-3.5 ${statusText}`} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div data-testid="adversarial-cores-decisions">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {decisions_24h}
          </div>
          <div className="text-xs text-slate-500">decisions 24h</div>
        </div>
        <div data-testid="adversarial-cores-closed">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {closed_24h}
          </div>
          <div className="text-xs text-slate-500">closed</div>
        </div>
        <div data-testid="adversarial-cores-edge-gap">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {avg_edge_gap?.toFixed?.(3) ?? '—'}
          </div>
          <div className="text-xs text-slate-500">avg edge_gap</div>
        </div>
        <div data-testid="adversarial-cores-last-run">
          <div className="text-sm font-semibold text-white">{lastRunLabel}</div>
          <div className="text-xs text-slate-500">last decision</div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
        <div className="rounded-md bg-slate-900/30 p-2">
          <div className="flex items-center justify-between text-slate-400">
            <span>Bull</span>
            <span className="tabular-nums text-emerald-300">
              {avg_bull_confidence?.toFixed?.(2) ?? '—'}
            </span>
          </div>
          <div className="text-slate-500">
            wins <span className="tabular-nums text-slate-300">{wins?.bull ?? 0}</span>
            {bull_win_rate != null && (
              <span className="ml-2">
                · wr <span className="tabular-nums text-slate-300">
                  {(bull_win_rate * 100).toFixed(0)}%
                </span>
              </span>
            )}
          </div>
        </div>
        <div className="rounded-md bg-slate-900/30 p-2">
          <div className="flex items-center justify-between text-slate-400">
            <span>Bear</span>
            <span className="tabular-nums text-rose-300">
              {avg_bear_confidence?.toFixed?.(2) ?? '—'}
            </span>
          </div>
          <div className="text-slate-500">
            wins <span className="tabular-nums text-slate-300">{wins?.bear ?? 0}</span>
            {bear_win_rate != null && (
              <span className="ml-2">
                · wr <span className="tabular-nums text-slate-300">
                  {(bear_win_rate * 100).toFixed(0)}%
                </span>
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
        <div data-testid="adversarial-cores-counts">
          L <span className="text-slate-300 tabular-nums">{decision_counts?.LONG ?? 0}</span>
          {' · '}
          S/A <span className="text-slate-300 tabular-nums">
            {decision_counts?.SHORT_OR_AVOID ?? 0}
          </span>
          {' · '}
          NT <span className="text-slate-300 tabular-nums">
            {decision_counts?.NO_TRADE ?? 0}
          </span>
        </div>
        {winSpread !== null && (
          <div data-testid="adversarial-cores-spread">
            Bull−Bear spread{' '}
            <span className={`tabular-nums ${Number(winSpread) >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
              {winSpread}pp
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

export default AdversarialCoresChip;
