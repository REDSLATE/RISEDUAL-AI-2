import React, { useCallback, useEffect, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, ExternalLink, Play, RefreshCw, ToggleLeft, ToggleRight, X } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL || '';

async function apiGet(path) {
  const token = localStorage.getItem('token') || '';
  const res = await fetch(`${API}${path}`, {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const token = localStorage.getItem('token') || '';
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json();
}

const Cell = ({ label, value, hint }) => (
  <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
    <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
    <div className="mt-1 text-2xl font-semibold text-zinc-100">
      {value ?? <span className="text-zinc-600">—</span>}
    </div>
    {hint ? <div className="mt-1 text-xs text-zinc-500">{hint}</div> : null}
  </div>
);

const Ratio = ({ label, value, warn }) => {
  const display = value === null || value === undefined ? '—' : `${(value * 100).toFixed(0)}%`;
  const color = warn ? 'text-amber-400' : (value === null || value === undefined ? 'text-zinc-500' : (value >= 0.9 ? 'text-emerald-400' : value >= 0.5 ? 'text-yellow-400' : 'text-red-400'));
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${color}`}>{display}</div>
    </div>
  );
};

const Toggle = ({ label, on, onChange, disabled, source }) => (
  <button
    type="button"
    disabled={disabled}
    onClick={() => onChange(!on)}
    data-testid={`alpha-daytrader-toggle-${label.toLowerCase()}`}
    className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition ${
      on
        ? 'border-emerald-600/60 bg-emerald-600/10 text-emerald-300 hover:bg-emerald-600/20'
        : 'border-zinc-700 bg-zinc-800 text-zinc-300 hover:bg-zinc-700'
    } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
  >
    {on ? <ToggleRight className="w-4 h-4" /> : <ToggleLeft className="w-4 h-4" />}
    <span>{label}</span>
    {source ? <span className="text-[10px] uppercase text-zinc-500">({source})</span> : null}
  </button>
);

export default function AlphaDayTraderPanel() {
  const [counters, setCounters] = useState(null);
  const [setups, setSetups] = useState([]);
  const [outcomes, setOutcomes] = useState([]);
  const [rollups, setRollups] = useState([]);
  const [runtime, setRuntime] = useState(null);
  const [regime, setRegime] = useState(null);
  const [fastRegime, setFastRegime] = useState(null);
  const [edges, setEdges] = useState([]);
  const [report, setReport] = useState(null);
  const [timelineFor, setTimelineFor] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const [c, s, o, r, rt, rg, fr, eg, rp] = await Promise.all([
        apiGet('/api/admin/alpha-daytrader/counters'),
        apiGet('/api/admin/alpha-daytrader/setups?limit=25'),
        apiGet('/api/admin/alpha-daytrader/outcomes?limit=25'),
        apiGet('/api/admin/alpha-daytrader/pattern-performance'),
        apiGet('/api/admin/alpha-daytrader/runtime'),
        apiGet('/api/admin/alpha-daytrader/regime'),
        apiGet('/api/admin/alpha-daytrader/fast-regime'),
        apiGet('/api/admin/alpha-daytrader/edge'),
        apiGet('/api/admin/alpha-daytrader/resolved-trades?limit=50'),
      ]);
      setCounters(c);
      setSetups(s.setups || []);
      setOutcomes(o.outcomes || []);
      setRollups(r.rollups || []);
      setRuntime(rt);
      setRegime(rg);
      setFastRegime(fr);
      setEdges(eg.rollups || []);
      setReport(rp);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const toggle = async (key, next) => {
    try {
      const patch = key === 'scan'
        ? { scan_enabled: next, execute_enabled: runtime?.execute_enabled ?? null }
        : { execute_enabled: next, scan_enabled: runtime?.scan_enabled ?? null };
      await apiPost('/api/admin/alpha-daytrader/runtime', patch);
      await load();
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  const forceTick = async () => {
    try {
      await apiPost('/api/admin/alpha-daytrader/tick');
      await load();
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  const recomputeRollups = async () => {
    try {
      await apiPost('/api/admin/alpha-daytrader/pattern-performance/recompute');
      await load();
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  const openTimeline = useCallback(async (setupId) => {
    if (!setupId) return;
    setTimelineFor(setupId);
    setTimeline(null);
    try {
      const data = await apiGet(`/api/admin/alpha-daytrader/setup/${encodeURIComponent(setupId)}/timeline`);
      setTimeline(data);
      // Update the URL hash so the view is shareable/refreshable.
      if (typeof window !== 'undefined') {
        window.location.hash = `alpha-timeline-${setupId}`;
      }
    } catch (e) {
      setTimeline({ error: String(e.message || e) });
    }
  }, []);

  const closeTimeline = () => {
    setTimelineFor(null);
    setTimeline(null);
    if (typeof window !== 'undefined' && window.location.hash.startsWith('#alpha-timeline-')) {
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  };

  // Deep-link: if URL hash is #alpha-timeline-<setup_id>, open it on mount.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const m = /^#alpha-timeline-(.+)$/.exec(window.location.hash);
    if (m && m[1]) openTimeline(decodeURIComponent(m[1]));
  }, [openTimeline]);

  const conv = counters?.conversion || {};
  const discoveryBuckets = (edges || [])
    .filter((e) => (e.samples ?? 0) < 10)
    .sort((a, b) => (b.samples || 0) - (a.samples || 0))
    .slice(0, 12);

  return (
    <div className="space-y-6" data-testid="alpha-daytrader-panel">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-zinc-100 flex items-center gap-2">
            <Activity className="w-5 h-5 text-cyan-400" />
            Alpha Day Trader
          </h2>
          <div className="text-sm text-zinc-500">
            Scanner → setup → trigger → intent → broker · conversion ratios · pattern performance
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            data-testid="alpha-daytrader-refresh"
            className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={forceTick}
            data-testid="alpha-daytrader-force-tick"
            className="inline-flex items-center gap-1 rounded-md border border-cyan-700/60 bg-cyan-600/10 px-3 py-1.5 text-xs text-cyan-300 hover:bg-cyan-600/20"
          >
            <Play className="w-3 h-3" />
            Force tick
          </button>
        </div>
      </div>

      {err ? (
        <div className="rounded-md border border-red-700/60 bg-red-900/20 p-3 text-sm text-red-300">
          <AlertTriangle className="inline w-4 h-4 mr-1" /> {err}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Toggle
          label="Scanner"
          on={!!runtime?.scan_enabled}
          onChange={(v) => toggle('scan', v)}
          disabled={!runtime}
          source={runtime?.scan_source}
        />
        <Toggle
          label="Live Execution"
          on={!!runtime?.execute_enabled}
          onChange={(v) => toggle('execute', v)}
          disabled={!runtime}
          source={runtime?.execute_source}
        />
        <div className="text-xs text-zinc-500">
          Env: SCAN={String(runtime?.env?.scan)} · EXECUTE={String(runtime?.env?.execute)}
          {runtime?.updated_at ? ` · overridden ${new Date(runtime.updated_at).toLocaleString()}` : ''}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Market regime</h3>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <div className="text-xs uppercase tracking-wide text-zinc-500">Slow · Daily SPY HMM</div>
                <div className="mt-1 text-2xl font-semibold">
                  <span className={regime?.label === 'UNKNOWN' ? 'text-zinc-400' : 'text-cyan-300'}>
                    {regime?.label ?? '—'}
                  </span>
                </div>
                <div className="text-xs text-zinc-500 mt-1">
                  {regime?.trained_samples ? `${regime.trained_samples} training samples` : 'HMM not trained yet'}
                  {regime?.updated_at ? ` · updated ${new Date(regime.updated_at).toLocaleString()}` : ''}
                </div>
              </div>
              <div className="text-right">
                <div className="text-xs uppercase tracking-wide text-zinc-500">Model preference</div>
                <div className="text-2xl font-mono text-zinc-200">
                  {regime?.probability != null ? `${(regime.probability * 100).toFixed(0)}%` : '—'}
                </div>
              </div>
            </div>
            <div className="text-xs text-zinc-500 space-y-0.5 mt-3">
              {regime?.posteriors && Object.keys(regime.posteriors).length
                ? Object.entries(regime.posteriors)
                    .sort((a, b) => (b[1] || 0) - (a[1] || 0))
                    .map(([k, v]) => (
                      <div key={k} className="flex justify-between text-zinc-400">
                        <span>{k}</span>
                        <span className="font-mono">{typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : String(v)}</span>
                      </div>
                    ))
                : <span className="text-zinc-500">—</span>}
            </div>
            <div className="mt-3 text-[10px] text-zinc-500 italic">
              This is the fitted model&apos;s posterior preference for its own learned states,
              not the objective probability that the market is in that regime.
            </div>
          </div>

          <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
            <div className="flex items-center justify-between mb-2">
              <div>
                <div className="text-xs uppercase tracking-wide text-zinc-500">Fast · Current session</div>
                <div className="mt-1 text-2xl font-semibold">
                  <span className={fastRegime?.label === 'UNKNOWN' ? 'text-zinc-400' : 'text-amber-300'}>
                    {fastRegime?.label ?? '—'}
                  </span>
                </div>
                <div className="text-xs text-zinc-500 mt-1">
                  Rule-based · today&apos;s SPY features
                  {fastRegime?.updated_at ? ` · updated ${new Date(fastRegime.updated_at).toLocaleString()}` : ''}
                </div>
              </div>
            </div>
            <div className="text-xs text-zinc-500 space-y-0.5 mt-3">
              {fastRegime?.features && Object.keys(fastRegime.features).length
                ? Object.entries(fastRegime.features).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-zinc-400">
                      <span>{k}</span>
                      <span className="font-mono">{typeof v === 'number' ? v.toFixed(3) : String(v)}</span>
                    </div>
                  ))
                : <span className="text-zinc-500">{fastRegime?.reason || '—'}</span>}
            </div>
            <div className="mt-3 text-[10px] text-zinc-500 italic">
              Complements the slow HMM. Alpha combines both for edge lookup;
              neither can block a trade.
            </div>
          </div>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-zinc-300">Edge modifiers by (pattern × slow × fast)</h3>
          <span className="text-xs text-zinc-500">
            &lt;10 samples → DISCOVERING (neutral 1.00×)
          </span>
        </div>
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="min-w-full text-sm text-zinc-300">
            <thead className="bg-zinc-900/60 text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-3 py-2 text-left">Pattern</th>
                <th className="px-3 py-2 text-left">Slow regime</th>
                <th className="px-3 py-2 text-left">Fast regime</th>
                <th className="px-3 py-2 text-right">Samples</th>
                <th className="px-3 py-2 text-right">Expectancy R</th>
                <th className="px-3 py-2 text-right">Modifier</th>
                <th className="px-3 py-2 text-left">State</th>
              </tr>
            </thead>
            <tbody>
              {edges.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-4 text-center text-zinc-500">
                  No edge rollups yet. Rows appear as real fills complete.
                </td></tr>
              ) : edges.map((e) => (
                <tr key={`${e.pattern}-${e.slow_regime || e.regime}-${e.fast_regime}`} className="border-t border-zinc-800/60">
                  <td className="px-3 py-2 font-medium">{e.pattern}</td>
                  <td className="px-3 py-2">{e.slow_regime || e.regime}</td>
                  <td className="px-3 py-2">{e.fast_regime || 'UNKNOWN'}</td>
                  <td className="px-3 py-2 text-right">{e.samples}</td>
                  <td className={`px-3 py-2 text-right ${
                    e.expectancy_r > 0 ? 'text-emerald-400' :
                    e.expectancy_r < 0 ? 'text-red-400' : ''
                  }`}>{e.expectancy_r ?? '—'}</td>
                  <td className={`px-3 py-2 text-right font-mono ${
                    (e.modifier ?? 1) > 1.02 ? 'text-emerald-400' :
                    (e.modifier ?? 1) < 0.98 ? 'text-amber-400' : ''
                  }`}>{(e.modifier ?? 1).toFixed(2)}×</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-xs ${
                      e.state === 'POSITIVE' ? 'bg-emerald-900/40 text-emerald-300' :
                      e.state === 'NEGATIVE' ? 'bg-red-900/40 text-red-300' :
                      e.state === 'FLAT' ? 'bg-yellow-900/40 text-yellow-300' :
                      'bg-zinc-800 text-zinc-400'
                    }`}>{e.state}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Today lifecycle</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <Cell label="Candidates" value={counters?.candidates_seen} />
          <Cell label="Setups" value={counters?.setups_created} />
          <Cell label="Armed" value={counters?.setups_armed} />
          <Cell label="Triggers" value={counters?.triggers} />
          <Cell label="Intents" value={counters?.intents_created} />
          <Cell label="Broker" value={counters?.broker_submitted} />
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Conversion ratios</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Ratio label="Setup → Trigger" value={conv.setup_to_trigger} />
          <Ratio label="Trigger → Intent" value={conv.trigger_to_intent} warn={conv.trigger_to_intent !== null && conv.trigger_to_intent < 0.95} />
          <Ratio label="Intent → Broker" value={conv.intent_to_broker} />
          <Ratio label="Broker → Fill" value={conv.broker_to_fill} />
        </div>
        <p className="mt-2 text-xs text-zinc-500">
          <CheckCircle2 className="inline w-3 h-3 mr-1 text-emerald-500" />
          Trigger → Intent should stay near 100%. Anything lower means the setup fired but Alpha did not
          produce an intent — a documented hard-safety reason should be visible in the setup timeline.
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-zinc-300">Pattern performance</h3>
          <button
            onClick={recomputeRollups}
            data-testid="alpha-daytrader-recompute-rollups"
            className="text-xs text-cyan-400 hover:text-cyan-300"
          >
            Recompute
          </button>
        </div>
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="min-w-full text-sm text-zinc-300">
            <thead className="bg-zinc-900/60 text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-3 py-2 text-left">Pattern</th>
                <th className="px-3 py-2 text-right">Setups</th>
                <th className="px-3 py-2 text-right">Executed</th>
                <th className="px-3 py-2 text-right">Win rate</th>
                <th className="px-3 py-2 text-right">Expectancy (R)</th>
                <th className="px-3 py-2 text-right">Profit factor</th>
                <th className="px-3 py-2 text-right">Median MFE</th>
                <th className="px-3 py-2 text-right">Median MAE</th>
                <th className="px-3 py-2 text-right">Slippage bps</th>
                <th className="px-3 py-2 text-left">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {rollups.length === 0 ? (
                <tr><td colSpan={10} className="px-3 py-4 text-center text-zinc-500">
                  No resolved outcomes yet. Rollups fill in as trades close.
                </td></tr>
              ) : rollups.map((r) => (
                <tr key={r.pattern} className="border-t border-zinc-800/60">
                  <td className="px-3 py-2 font-medium">{r.pattern}</td>
                  <td className="px-3 py-2 text-right">{r.unique_setups}</td>
                  <td className="px-3 py-2 text-right">{r.executed}</td>
                  <td className="px-3 py-2 text-right">{r.win_rate !== null && r.win_rate !== undefined ? `${(r.win_rate * 100).toFixed(0)}%` : '—'}</td>
                  <td className={`px-3 py-2 text-right ${r.expectancy_r > 0 ? 'text-emerald-400' : r.expectancy_r < 0 ? 'text-red-400' : ''}`}>{r.expectancy_r ?? '—'}</td>
                  <td className="px-3 py-2 text-right">{r.profit_factor ?? '—'}</td>
                  <td className="px-3 py-2 text-right">{r.median_mfe_r ?? '—'}</td>
                  <td className="px-3 py-2 text-right">{r.median_mae_r ?? '—'}</td>
                  <td className="px-3 py-2 text-right">{r.avg_slippage_bps ?? '—'}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-xs ${
                      r.sample_confidence === 'high' ? 'bg-emerald-900/40 text-emerald-300' :
                      r.sample_confidence === 'medium' ? 'bg-yellow-900/40 text-yellow-300' :
                      'bg-zinc-800 text-zinc-400'
                    }`}>{r.sample_confidence}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-zinc-300 mb-2">First resolved trade report</h3>
        {report?.totals ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 mb-3">
            <Cell label="Measured trades" value={report.totals.count} />
            <Cell label="Win rate" value={report.totals.win_rate != null ? `${(report.totals.win_rate * 100).toFixed(0)}%` : '—'} />
            <Cell label="Avg realized R" value={report.totals.avg_realized_r ?? '—'} />
            <Cell label="Gross P&L $" value={report.totals.gross_pnl_usd ?? '—'} />
            <Cell label="Median entry slip" value={report.totals.median_entry_slippage_bps != null ? `${report.totals.median_entry_slippage_bps} bps` : '—'} />
            <Cell label="Edge agreement" value={report.totals.edge_agreement_ratio != null ? `${(report.totals.edge_agreement_ratio * 100).toFixed(0)}%` : '—'} hint="AGREE / (AGREE + DISAGREE)" />
          </div>
        ) : null}
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="min-w-full text-sm text-zinc-300">
            <thead className="bg-zinc-900/60 text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-3 py-2 text-left">Symbol</th>
                <th className="px-3 py-2 text-left">Pattern</th>
                <th className="px-3 py-2 text-left">Slow</th>
                <th className="px-3 py-2 text-left">Fast</th>
                <th className="px-3 py-2 text-right">Det → Trigger → Fill</th>
                <th className="px-3 py-2 text-right">R</th>
                <th className="px-3 py-2 text-right">MFE / MAE</th>
                <th className="px-3 py-2 text-right">Slip in / out</th>
                <th className="px-3 py-2 text-right">P&L $</th>
                <th className="px-3 py-2 text-right">I→B ms</th>
                <th className="px-3 py-2 text-left">Exit</th>
                <th className="px-3 py-2 text-left">Edge</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {(report?.trades || []).length === 0 ? (
                <tr><td colSpan={13} className="px-3 py-4 text-center text-zinc-500">
                  No resolved trades with fill economics yet. Fills the moment Alpha closes its first live trade.
                </td></tr>
              ) : (report.trades || []).map((t) => (
                <tr key={t.setup_id} className="border-t border-zinc-800/60">
                  <td className="px-3 py-2 font-medium">{t.symbol}</td>
                  <td className="px-3 py-2">{t.setup_type}</td>
                  <td className="px-3 py-2 text-xs">{t.regime || '—'}</td>
                  <td className="px-3 py-2 text-xs">{t.fast_regime || '—'}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    ${t.detected_price?.toFixed?.(2) ?? '—'} → ${t.confirmation_price?.toFixed?.(2) ?? '—'} → ${t.entry_fill_price?.toFixed?.(2) ?? '—'}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${
                    t.realized_r > 0 ? 'text-emerald-400' :
                    t.realized_r < 0 ? 'text-red-400' : ''
                  }`}>{t.realized_r ?? '—'}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    <span className="text-emerald-500/70">{t.mfe_r ?? '—'}</span>
                    <span className="text-zinc-500"> / </span>
                    <span className="text-red-500/70">{t.mae_r ?? '—'}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    {t.entry_slippage_bps ?? '—'} / {t.exit_slippage_bps ?? '—'}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${
                    t.realized_pnl_usd > 0 ? 'text-emerald-400' :
                    t.realized_pnl_usd < 0 ? 'text-red-400' : ''
                  }`}>{t.realized_pnl_usd ?? '—'}</td>
                  <td className="px-3 py-2 text-right text-xs">{t.intent_to_broker_ms ?? '—'}</td>
                  <td className="px-3 py-2 text-xs">{t.close_reason || '—'}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-xs ${
                      t.edge_agreement === 'AGREE' ? 'bg-emerald-900/40 text-emerald-300' :
                      t.edge_agreement === 'DISAGREE' ? 'bg-red-900/40 text-red-300' :
                      'bg-zinc-800 text-zinc-500'
                    }`}>{t.edge_agreement}</span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => openTimeline(t.setup_id)}
                      data-testid={`alpha-open-timeline-${t.setup_id}`}
                      className="text-xs text-cyan-400 hover:text-cyan-300 inline-flex items-center gap-1"
                    >
                      Timeline <ExternalLink className="w-3 h-3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-zinc-300">Edge discovery radar</h3>
          <span className="text-xs text-zinc-500">Buckets approaching the 10-sample threshold</span>
        </div>
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="min-w-full text-sm text-zinc-300">
            <thead className="bg-zinc-900/60 text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-3 py-2 text-left">Pattern</th>
                <th className="px-3 py-2 text-left">Slow</th>
                <th className="px-3 py-2 text-left">Fast</th>
                <th className="px-3 py-2 text-right">Samples</th>
                <th className="px-3 py-2 text-right">To threshold</th>
                <th className="px-3 py-2">Progress</th>
                <th className="px-3 py-2 text-right">Interim R</th>
              </tr>
            </thead>
            <tbody>
              {discoveryBuckets.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-4 text-center text-zinc-500">
                  No DISCOVERING buckets yet — either no resolved samples or every bucket already graduated.
                </td></tr>
              ) : discoveryBuckets.map((b) => {
                const pct = Math.min(100, ((b.samples || 0) / 10) * 100);
                return (
                  <tr key={`${b.pattern}-${b.slow_regime || b.regime}-${b.fast_regime}`} className="border-t border-zinc-800/60">
                    <td className="px-3 py-2 font-medium">{b.pattern}</td>
                    <td className="px-3 py-2">{b.slow_regime || b.regime}</td>
                    <td className="px-3 py-2">{b.fast_regime || 'UNKNOWN'}</td>
                    <td className="px-3 py-2 text-right font-mono">{b.samples || 0}</td>
                    <td className="px-3 py-2 text-right text-xs">{Math.max(0, 10 - (b.samples || 0))} more</td>
                    <td className="px-3 py-2">
                      <div className="w-full bg-zinc-800 rounded-full h-1.5">
                        <div className="bg-cyan-500 h-1.5 rounded-full" style={{ width: `${pct}%` }} />
                      </div>
                    </td>
                    <td className={`px-3 py-2 text-right font-mono text-xs ${
                      (b.expectancy_r ?? 0) > 0 ? 'text-emerald-400/80' :
                      (b.expectancy_r ?? 0) < 0 ? 'text-red-400/80' : 'text-zinc-500'
                    }`}>{b.expectancy_r ?? '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <h3 className="text-sm font-medium text-zinc-300 mb-2">Recent setups</h3>
          <div className="rounded-lg border border-zinc-800 max-h-72 overflow-y-auto text-sm">
            {setups.length === 0 ? (
              <div className="p-3 text-center text-zinc-500">No setups tracked yet.</div>
            ) : setups.map((s) => (
              <div key={s.setup_id} className="border-b border-zinc-800/60 px-3 py-2 hover:bg-zinc-900/40 cursor-pointer"
                   onClick={() => openTimeline(s.setup_id)}>
                <div className="flex items-center justify-between">
                  <span className="font-medium text-zinc-100">{s.symbol}</span>
                  <span className="text-xs text-zinc-500">{s.setup_type}</span>
                </div>
                <div className="text-xs text-zinc-500">
                  state=<span className="text-zinc-300">{s.state}</span> · score={s.score?.toFixed?.(2)} ·
                  trigger=${s.trigger_price?.toFixed?.(2)} · inv=${s.invalidation_price?.toFixed?.(2)}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h3 className="text-sm font-medium text-zinc-300 mb-2">Recent outcomes</h3>
          <div className="rounded-lg border border-zinc-800 max-h-72 overflow-y-auto text-sm">
            {outcomes.length === 0 ? (
              <div className="p-3 text-center text-zinc-500">No outcomes recorded yet.</div>
            ) : outcomes.map((o, i) => (
              <div key={`${o.setup_id}-${i}`} className="border-b border-zinc-800/60 px-3 py-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-zinc-100">{o.symbol}</span>
                  <span className={`text-xs ${o.filled ? 'text-emerald-400' : o.order_submitted ? 'text-yellow-400' : 'text-zinc-500'}`}>
                    {o.filled ? 'filled' : o.order_submitted ? 'submitted' : (o.reject_reason || 'no trade')}
                  </span>
                </div>
                <div className="text-xs text-zinc-500">
                  {o.setup_type} · conf={o.confirmation_price?.toFixed?.(2) ?? '—'} · trig={o.triggered ? 'yes' : 'no'}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {timelineFor ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" data-testid="alpha-timeline-modal">
          <div className="w-full max-w-4xl max-h-[85vh] overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950 shadow-2xl flex flex-col">
            <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
              <div>
                <div className="text-sm text-zinc-500">Setup timeline</div>
                <div className="font-mono text-xs text-cyan-300 break-all">{timelineFor}</div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    if (typeof navigator !== 'undefined' && navigator.clipboard) {
                      navigator.clipboard.writeText(window.location.href);
                    }
                  }}
                  className="text-xs text-zinc-400 hover:text-zinc-200"
                  data-testid="alpha-timeline-copy-url"
                >
                  Copy URL
                </button>
                <button
                  onClick={closeTimeline}
                  className="rounded-full p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
                  data-testid="alpha-timeline-close"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
            <div className="overflow-y-auto p-4 space-y-4">
              {!timeline ? (
                <div className="text-sm text-zinc-500">Loading timeline…</div>
              ) : timeline.error ? (
                <div className="text-sm text-red-400">{timeline.error}</div>
              ) : (
                <>
                  {timeline.latency_ms && Object.keys(timeline.latency_ms).length ? (
                    <div>
                      <h4 className="text-xs uppercase text-zinc-500 mb-2">Latency samples (ms)</h4>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                        {['signal_to_trigger', 'trigger_to_intent', 'intent_to_broker', 'broker_to_fill'].map((phase) => {
                          const arr = timeline.latency_ms[phase];
                          return (
                            <div key={phase} className="rounded border border-zinc-800 bg-zinc-900/40 p-2">
                              <div className="text-[10px] text-zinc-500 uppercase">{phase.replaceAll('_', ' ')}</div>
                              <div className="text-lg font-mono text-zinc-200">
                                {Array.isArray(arr) && arr.length ? arr[0] : '—'}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                  <div>
                    <h4 className="text-xs uppercase text-zinc-500 mb-2">Events ({(timeline.events || []).length})</h4>
                    <div className="space-y-1">
                      {(timeline.events || []).length === 0 ? (
                        <div className="text-sm text-zinc-500">No events recorded.</div>
                      ) : (timeline.events || []).map((ev, i) => (
                        <div key={i} className="rounded border border-zinc-800/60 bg-zinc-900/30 px-3 py-2 text-xs">
                          <div className="flex items-center justify-between">
                            <span className="font-medium text-zinc-100">{ev.event}</span>
                            <span className="text-zinc-500">{ev.ts ? new Date(ev.ts).toLocaleString() : ''}</span>
                          </div>
                          {ev.stage ? <div className="text-[10px] text-zinc-500">stage: {ev.stage}</div> : null}
                          {ev.payload && Object.keys(ev.payload).length ? (
                            <pre className="mt-1 whitespace-pre-wrap break-all text-[11px] text-zinc-400">{JSON.stringify(ev.payload, null, 2)}</pre>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
