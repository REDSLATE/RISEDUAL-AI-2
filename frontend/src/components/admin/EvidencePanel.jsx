import React, { useState, useEffect, useCallback } from 'react';
import { RefreshCw, ShieldCheck, Zap, Ban, AlertTriangle, TrendingUp } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { toast } from '../ui/sonner';

const API = `${getApiBase()}/api`;

const BUCKET_STYLE = {
  proven:   { color: 'text-emerald-300', bg: 'bg-emerald-900/30 border-emerald-700/50', icon: ShieldCheck, label: 'PROVEN' },
  ok:       { color: 'text-cyan-300',    bg: 'bg-cyan-900/30 border-cyan-700/50',       icon: Zap,        label: 'OK'     },
  untested: { color: 'text-slate-300',   bg: 'bg-slate-800/50 border-slate-700',         icon: AlertTriangle, label: 'UNTESTED' },
  losing:   { color: 'text-red-300',     bg: 'bg-red-900/30 border-red-700/50',         icon: Ban,        label: 'LOSING' },
  unknown:  { color: 'text-slate-400',   bg: 'bg-slate-800/40 border-slate-700',         icon: AlertTriangle, label: 'UNKNOWN' },
};

export default function EvidencePanel() {
  const [snap, setSnap] = useState(null);
  const [loading, setLoading] = useState(false);
  const [recomputing, setRecomputing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API}/admin/evidence/scores`);
      if (!res.ok) throw new Error(`status ${res.status}`);
      setSnap(await res.json());
    } catch (e) {
      setError(e.message || 'failed to load evidence scores');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const recompute = useCallback(async () => {
    setRecomputing(true);
    try {
      const res = await authFetch(`${API}/admin/evidence/recompute`, { method: 'POST' });
      if (!res.ok) throw new Error(`recompute failed ${res.status}`);
      const data = await res.json();
      toast.success(`Evaluated ${data.strategies_evaluated} strategies, wrote ${data.wrote} scores`);
      await load();
    } catch (e) {
      toast.error(e.message || 'recompute failed');
    } finally {
      setRecomputing(false);
    }
  }, [load]);

  if (loading && !snap) return <div className="text-slate-400 text-sm">Loading evidence scores…</div>;
  if (error) return <div className="text-red-400 text-sm">{error}</div>;
  if (!snap) return null;

  const { strategies, last_run: lastRun, enforcement_enabled: enforcing, window_days: windowDays, min_trades_for_evidence: minTrades, untested_multiplier: untestedMult } = snap;

  return (
    <div className="space-y-6" data-testid="evidence-panel">
      <div className="flex items-start justify-between gap-6 flex-wrap">
        <div>
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-cyan-400" />
            Strategy Evidence
          </h3>
          <p className="text-sm text-slate-400 mt-1 max-w-xl">
            Nightly attribution over last <span className="text-cyan-300">{windowDays}d</span> of closed live trades.
            Strategies with &lt; <span className="text-cyan-300">{minTrades}</span> trades fire at <span className="text-cyan-300">{Math.round(untestedMult * 100)}%</span> of baseline notional.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            disabled={loading}
            className="px-3 py-2 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs flex items-center gap-1.5 disabled:opacity-50"
            data-testid="evidence-refresh-btn"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={recompute}
            disabled={recomputing}
            className="px-4 py-2 rounded bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold flex items-center gap-2 disabled:opacity-50"
            data-testid="evidence-recompute-btn"
          >
            <TrendingUp className={`w-4 h-4 ${recomputing ? 'animate-pulse' : ''}`} />
            {recomputing ? 'Computing…' : 'RECOMPUTE NOW'}
          </button>
        </div>
      </div>

      {/* Enforcement banner */}
      <div className={`px-4 py-3 rounded border ${enforcing ? 'bg-emerald-900/30 border-emerald-700/50' : 'bg-amber-900/20 border-amber-800/50'}`} data-testid="evidence-enforcement-banner">
        <div className="flex items-center gap-2 text-sm">
          {enforcing ? (
            <>
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              <span className="text-emerald-100"><span className="font-semibold">ENFORCING</span> — evidence multipliers ARE applied to live notional.</span>
            </>
          ) : (
            <>
              <AlertTriangle className="w-4 h-4 text-amber-400" />
              <span className="text-amber-100"><span className="font-semibold">SHADOW mode</span> — multipliers are logged on each fire but NOT reducing notional. Set <span className="font-mono">RISEDUAL_EVIDENCE_ENFORCE=1</span> to apply.</span>
            </>
          )}
        </div>
      </div>

      {/* Last-run tile */}
      {lastRun && (
        <div className="text-xs text-slate-500" data-testid="evidence-last-run">
          Last run: {new Date(lastRun.finished_at).toLocaleString()} · evaluated {lastRun.strategies_evaluated} strategies · wrote {lastRun.wrote} · window {lastRun.window_days}d · min trades {lastRun.min_trades}
        </div>
      )}

      {/* Strategy table */}
      <div className="border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm" data-testid="evidence-strategy-table">
          <thead className="bg-slate-900/60 text-xs uppercase text-slate-400">
            <tr>
              <th className="text-left px-4 py-2 font-normal">Strategy</th>
              <th className="text-right px-4 py-2 font-normal">Trades</th>
              <th className="text-right px-4 py-2 font-normal">Hit %</th>
              <th className="text-right px-4 py-2 font-normal">Mean Ret</th>
              <th className="text-right px-4 py-2 font-normal">Sharpe</th>
              <th className="text-right px-4 py-2 font-normal">Expectancy</th>
              <th className="text-center px-4 py-2 font-normal">Bucket</th>
              <th className="text-right px-4 py-2 font-normal">Notional ×</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {strategies.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-slate-500 text-sm" data-testid="evidence-empty">
                  No strategy scores yet. Click RECOMPUTE NOW to seed, or wait for the 03:15 UTC nightly job.
                </td>
              </tr>
            )}
            {strategies.map((s) => {
              const style = BUCKET_STYLE[s.bucket] || BUCKET_STYLE.unknown;
              const Icon = style.icon;
              return (
                <tr key={s.strategy_id} className="hover:bg-slate-900/40" data-testid={`evidence-row-${s.strategy_id}`}>
                  <td className="px-4 py-2 font-mono text-xs text-slate-200">{s.strategy_id}</td>
                  <td className="px-4 py-2 text-right tabular-nums text-slate-300">{s.trade_count}</td>
                  <td className="px-4 py-2 text-right tabular-nums text-slate-300">{(s.hit_rate * 100).toFixed(1)}%</td>
                  <td className={`px-4 py-2 text-right tabular-nums ${s.mean_return > 0 ? 'text-emerald-300' : s.mean_return < 0 ? 'text-red-300' : 'text-slate-400'}`}>
                    {(s.mean_return * 100).toFixed(2)}%
                  </td>
                  <td className={`px-4 py-2 text-right tabular-nums font-semibold ${s.sharpe >= 1 ? 'text-emerald-300' : s.sharpe >= 0 ? 'text-cyan-300' : 'text-red-300'}`}>
                    {s.sharpe.toFixed(2)}
                  </td>
                  <td className={`px-4 py-2 text-right tabular-nums ${s.expectancy > 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                    {(s.expectancy * 100).toFixed(1)}bp
                  </td>
                  <td className="px-4 py-2">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold border ${style.bg} ${style.color}`}>
                      <Icon className="w-3 h-3" />
                      {style.label}
                    </span>
                  </td>
                  <td className={`px-4 py-2 text-right tabular-nums font-mono ${style.color}`}>
                    {s.notional_multiplier.toFixed(2)}×
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="text-[11px] text-slate-500 leading-relaxed">
        <span className="text-slate-400 font-semibold">How buckets map:</span>{' '}
        <span className="text-emerald-300">PROVEN</span> = ≥{minTrades} trades &amp; Sharpe ≥ 1.0 → 1.00×.{' '}
        <span className="text-cyan-300">OK</span> = ≥{minTrades} trades &amp; Sharpe ≥ 0.0 → 0.50×.{' '}
        <span className="text-red-300">LOSING</span> = ≥{minTrades} trades &amp; Sharpe &lt; 0.0 → 0.10×.{' '}
        <span className="text-slate-300">UNTESTED</span> = &lt;{minTrades} trades → {untestedMult.toFixed(2)}×.
      </div>
    </div>
  );
}
