import React, { useCallback, useEffect, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, Play, RefreshCw, ToggleLeft, ToggleRight } from 'lucide-react';

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
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const [c, s, o, r, rt, rg, eg] = await Promise.all([
        apiGet('/api/admin/alpha-daytrader/counters'),
        apiGet('/api/admin/alpha-daytrader/setups?limit=25'),
        apiGet('/api/admin/alpha-daytrader/outcomes?limit=25'),
        apiGet('/api/admin/alpha-daytrader/pattern-performance'),
        apiGet('/api/admin/alpha-daytrader/runtime'),
        apiGet('/api/admin/alpha-daytrader/regime'),
        apiGet('/api/admin/alpha-daytrader/edge'),
      ]);
      setCounters(c);
      setSetups(s.setups || []);
      setOutcomes(o.outcomes || []);
      setRollups(r.rollups || []);
      setRuntime(rt);
      setRegime(rg);
      setEdges(eg.rollups || []);
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

  const conv = counters?.conversion || {};

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
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Market regime (HMM)</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Cell
            label="Current regime"
            value={
              <span className={regime?.label === 'UNKNOWN' ? 'text-zinc-400' : 'text-cyan-300'}>
                {regime?.label ?? '—'}
              </span>
            }
            hint={regime?.trained_samples ? `${regime.trained_samples} training samples` : 'HMM not trained yet'}
          />
          <Cell
            label="Confidence"
            value={regime?.probability != null ? `${(regime.probability * 100).toFixed(0)}%` : '—'}
            hint={regime?.updated_at ? `Updated ${new Date(regime.updated_at).toLocaleString()}` : null}
          />
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 col-span-2">
            <div className="text-xs uppercase tracking-wide text-zinc-500 mb-1">Posteriors</div>
            <div className="text-xs text-zinc-300 space-y-0.5">
              {regime?.posteriors && Object.keys(regime.posteriors).length
                ? Object.entries(regime.posteriors)
                    .filter(([k]) => !k.startsWith('_') && k !== 'reason')
                    .sort((a, b) => (b[1] || 0) - (a[1] || 0))
                    .map(([k, v]) => (
                      <div key={k} className="flex justify-between">
                        <span>{k}</span>
                        <span className="font-mono">{typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : String(v)}</span>
                      </div>
                    ))
                : <span className="text-zinc-500">—</span>}
            </div>
          </div>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-zinc-300">Edge modifiers by (pattern × regime)</h3>
          <span className="text-xs text-zinc-500">
            &lt;10 samples → DISCOVERING (neutral 1.00×)
          </span>
        </div>
        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="min-w-full text-sm text-zinc-300">
            <thead className="bg-zinc-900/60 text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-3 py-2 text-left">Pattern</th>
                <th className="px-3 py-2 text-left">Regime</th>
                <th className="px-3 py-2 text-right">Samples</th>
                <th className="px-3 py-2 text-right">Expectancy R</th>
                <th className="px-3 py-2 text-right">Modifier</th>
                <th className="px-3 py-2 text-left">State</th>
              </tr>
            </thead>
            <tbody>
              {edges.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-4 text-center text-zinc-500">
                  No edge rollups yet. Fill in as trades close with regime + pattern context.
                </td></tr>
              ) : edges.map((e) => (
                <tr key={`${e.pattern}-${e.regime}`} className="border-t border-zinc-800/60">
                  <td className="px-3 py-2 font-medium">{e.pattern}</td>
                  <td className="px-3 py-2">{e.regime}</td>
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <h3 className="text-sm font-medium text-zinc-300 mb-2">Recent setups</h3>
          <div className="rounded-lg border border-zinc-800 max-h-72 overflow-y-auto text-sm">
            {setups.length === 0 ? (
              <div className="p-3 text-center text-zinc-500">No setups tracked yet.</div>
            ) : setups.map((s) => (
              <div key={s.setup_id} className="border-b border-zinc-800/60 px-3 py-2">
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
    </div>
  );
}
