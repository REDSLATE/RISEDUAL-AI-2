import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, RefreshCw, TrendingDown, Activity, Target, Users as UsersIcon, Layers } from 'lucide-react';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

// ── Colour scale for the horizontal breakdown bars ──────────────
// Each dimension gets its own hue so the six panels don't visually
// blur together. All colours live in the red/amber family because
// we're visualising failure data — green would feel dissonant.
const BAR_COLORS = {
  failure_code: 'bg-red-500/70',
  feature: 'bg-amber-500/70',
  confidence_bucket: 'bg-orange-500/70',
  direction: 'bg-pink-500/70',
  sector: 'bg-rose-500/70',
  model_version: 'bg-fuchsia-500/70',
  grade: 'bg-red-400/70',
};

const fmtPct = (n) => `${(Number(n || 0) * 100).toFixed(1)}%`;

const BreakdownBar = ({ item, maxCount, colorClass, testIdPrefix }) => {
  const widthPct = maxCount > 0 ? Math.max(4, Math.round((item.count / maxCount) * 100)) : 4;
  return (
    <div className="flex items-center gap-2 text-xs" data-testid={`${testIdPrefix}-row-${item.key}`}>
      <div className="w-32 text-slate-300 truncate" title={item.key}>{item.key}</div>
      <div className="flex-1 bg-slate-900/60 rounded-sm h-5 relative overflow-hidden border border-slate-700/40">
        <div className={`h-full ${colorClass}`} style={{ width: `${widthPct}%` }} />
        <span className="absolute inset-0 flex items-center px-2 text-[11px] text-white font-mono">
          {item.count} · {fmtPct(item.pct)} · avg {item.avg_confidence_pct?.toFixed?.(1) ?? item.avg_confidence_pct}%
        </span>
      </div>
    </div>
  );
};

const BreakdownSection = ({ icon: Icon, title, items, colorKey, testId, emptyLabel = 'No data' }) => {
  const maxCount = useMemo(() => Math.max(1, ...items.map((i) => i.count || 0)), [items]);
  return (
    <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-4" data-testid={testId}>
      <div className="flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4 text-[#3DE8D9]" />
        <h3 className="text-white text-sm font-semibold">{title}</h3>
        <span className="text-slate-400 text-[11px]">{items.length} keys</span>
      </div>
      {items.length === 0 ? (
        <div className="text-slate-500 text-xs italic">{emptyLabel}</div>
      ) : (
        <div className="space-y-1.5">
          {items.map((it) => (
            <BreakdownBar
              key={it.key}
              item={it}
              maxCount={maxCount}
              colorClass={BAR_COLORS[colorKey] || 'bg-slate-500/70'}
              testIdPrefix={testId}
            />
          ))}
        </div>
      )}
    </div>
  );
};

const TopOffenders = ({ offenders }) => {
  if (!offenders || offenders.length === 0) {
    return (
      <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <TrendingDown className="w-4 h-4 text-red-400" />
          <h3 className="text-white text-sm font-semibold">Top Offenders</h3>
        </div>
        <div className="text-slate-500 text-xs italic">No offenders in window.</div>
      </div>
    );
  }
  return (
    <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-4" data-testid="autopsy-top-offenders">
      <div className="flex items-center gap-2 mb-3">
        <TrendingDown className="w-4 h-4 text-red-400" />
        <h3 className="text-white text-sm font-semibold">Top Offenders</h3>
        <span className="text-slate-400 text-[11px]">{offenders.length} symbols</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-slate-400 border-b border-slate-700/50">
            <tr>
              <th className="text-left py-1.5 pr-3">Symbol</th>
              <th className="text-left py-1.5 pr-3">Sector</th>
              <th className="text-right py-1.5 pr-3">Fails</th>
              <th className="text-right py-1.5 pr-3">Avg Conf</th>
              <th className="text-left py-1.5 pr-3">Top Failure Modes</th>
              <th className="text-left py-1.5">Agents</th>
            </tr>
          </thead>
          <tbody>
            {offenders.map((o) => {
              const topFail = Object.entries(o.failure_codes || {}).sort((a, b) => b[1] - a[1]).slice(0, 2);
              const topAgents = Object.entries(o.features || {}).sort((a, b) => b[1] - a[1]).slice(0, 2);
              return (
                <tr key={o.symbol} className="border-b border-slate-800/50" data-testid={`autopsy-offender-${o.symbol}`}>
                  <td className="py-1.5 pr-3 text-white font-semibold">{o.symbol}</td>
                  <td className="py-1.5 pr-3 text-slate-300">{o.sector || 'Unknown'}</td>
                  <td className="py-1.5 pr-3 text-right text-red-400 font-mono">{o.count}</td>
                  <td className="py-1.5 pr-3 text-right text-slate-300 font-mono">{o.avg_confidence_pct?.toFixed?.(1)}%</td>
                  <td className="py-1.5 pr-3 text-slate-300">
                    {topFail.length === 0 ? '—' : topFail.map(([k, v]) => (
                      <span key={k} className="mr-1 text-[10px] bg-red-500/10 text-red-300 border border-red-500/20 rounded px-1.5 py-0.5">
                        {k}·{v}
                      </span>
                    ))}
                  </td>
                  <td className="py-1.5 text-slate-300">
                    {topAgents.map(([k, v]) => (
                      <span key={k} className="mr-1 text-[10px] bg-amber-500/10 text-amber-300 border border-amber-500/20 rounded px-1.5 py-0.5">
                        {k}·{v}
                      </span>
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const SamplesTable = ({ samples }) => {
  const [open, setOpen] = useState(false);
  if (!samples || samples.length === 0) return null;
  return (
    <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center justify-between w-full text-white text-sm font-semibold"
        data-testid="autopsy-samples-toggle"
      >
        <span className="flex items-center gap-2">
          <Layers className="w-4 h-4 text-[#3DE8D9]" />
          Raw Failures ({samples.length})
        </span>
        <span className="text-slate-400 text-xs">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (
        <div className="mt-3 overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="w-full text-[11px]">
            <thead className="text-slate-400 border-b border-slate-700/50 sticky top-0 bg-slate-800">
              <tr>
                <th className="text-left py-1 pr-2">When</th>
                <th className="text-left py-1 pr-2">Symbol</th>
                <th className="text-left py-1 pr-2">Sector</th>
                <th className="text-left py-1 pr-2">Agent</th>
                <th className="text-left py-1 pr-2">Direction</th>
                <th className="text-right py-1 pr-2">Conf</th>
                <th className="text-left py-1 pr-2">Grade</th>
                <th className="text-left py-1 pr-2">Failure Code</th>
                <th className="text-right py-1 pr-2">Px Entry</th>
                <th className="text-right py-1">Px Verified</th>
              </tr>
            </thead>
            <tbody>
              {samples.map((s) => (
                <tr key={s.prediction_id} className="border-b border-slate-800/50" data-testid={`autopsy-sample-${s.prediction_id}`}>
                  <td className="py-1 pr-2 text-slate-400 whitespace-nowrap">{s.verified_at ? new Date(s.verified_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</td>
                  <td className="py-1 pr-2 text-white font-semibold">{s.symbol}</td>
                  <td className="py-1 pr-2 text-slate-300">{s.sector || 'Unknown'}</td>
                  <td className="py-1 pr-2 text-slate-300">{s.feature}</td>
                  <td className="py-1 pr-2 text-slate-300">{s.direction}</td>
                  <td className="py-1 pr-2 text-right text-slate-200 font-mono">{s.confidence_pct?.toFixed?.(1)}%</td>
                  <td className="py-1 pr-2 text-red-300">{s.grade}</td>
                  <td className="py-1 pr-2 text-slate-300">{s.failure_code}</td>
                  <td className="py-1 pr-2 text-right text-slate-400 font-mono">{s.price_at_prediction ? `$${s.price_at_prediction.toFixed(2)}` : '—'}</td>
                  <td className="py-1 text-right text-slate-400 font-mono">{s.price_verified ? `$${s.price_verified.toFixed(2)}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

const ToxicSpikeAutopsyPanel = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [days, setDays] = useState(2);
  const [minConf, setMinConf] = useState(80);

  const fetchAutopsy = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const res = await authFetch(`${API}/admin/toxic-spike/autopsy?days=${days}&min_confidence=${minConf}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [days, minConf]);

  useEffect(() => { fetchAutopsy(); }, [fetchAutopsy]);

  if (loading && !data) {
    return (
      <div className="p-6 text-slate-400 text-sm" data-testid="autopsy-loading">
        Loading autopsy…
      </div>
    );
  }
  if (err) {
    return (
      <div className="p-6 text-red-400 text-sm" data-testid="autopsy-error">
        Failed to load: {err}
        <Button onClick={fetchAutopsy} size="sm" variant="outline" className="ml-3">
          Retry
        </Button>
      </div>
    );
  }
  if (!data) return null;

  const total = data.total_failures || 0;
  const hasData = total > 0;

  return (
    <div className="space-y-4" data-testid="toxic-spike-autopsy-panel">
      {/* ── Header / Summary ── */}
      <div className="bg-gradient-to-r from-red-500/10 to-orange-500/10 border border-red-500/30 rounded-lg p-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-orange-400 mt-0.5" />
            <div>
              <h2 className="text-white text-base font-bold" data-testid="autopsy-header-title">
                Toxic Spike Autopsy
              </h2>
              <p className="text-slate-300 text-xs mt-0.5">
                {total === 0 ? 'No high-confidence failures in window — clean run.' : (
                  <>
                    <span className="text-red-400 font-semibold">{total}</span> high-confidence failures
                    · <span className="text-amber-400">{data.unique_symbols}</span> symbols
                    · avg confidence <span className="text-white">{data.avg_confidence_pct}%</span>
                  </>
                )}
              </p>
              <p className="text-slate-500 text-[10px] mt-0.5">
                Window: last {data.window?.days}d · min conf ≥ {data.min_confidence_pct}% · {data.total_misses_in_window} total misses in window (neutrals excluded)
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-slate-400 text-xs flex items-center gap-1.5">
              Days
              <select
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white text-xs"
                data-testid="autopsy-days-select"
              >
                {[1, 2, 3, 7, 14, 30].map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </label>
            <label className="text-slate-400 text-xs flex items-center gap-1.5">
              Min Conf
              <select
                value={minConf}
                onChange={(e) => setMinConf(Number(e.target.value))}
                className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white text-xs"
                data-testid="autopsy-min-conf-select"
              >
                {[60, 70, 80, 85, 90].map((m) => <option key={m} value={m}>{m}%</option>)}
              </select>
            </label>
            <Button
              onClick={fetchAutopsy}
              size="sm"
              variant="outline"
              disabled={loading}
              className="border-red-500/40 text-red-300 hover:bg-red-500/10"
              data-testid="autopsy-refresh-btn"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              <span className="ml-1.5">Refresh</span>
            </Button>
          </div>
        </div>
      </div>

      {!hasData ? (
        <div className="text-center py-10 text-slate-500 bg-slate-800/40 border border-slate-700/50 rounded-lg" data-testid="autopsy-empty">
          No toxic spikes in this window — the model's calibration is behaving within expected bounds.
        </div>
      ) : (
        <>
          {/* ── Six-up breakdown grid ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <BreakdownSection icon={AlertTriangle} title="By Failure Mode" items={data.by_failure_code || []} colorKey="failure_code" testId="autopsy-by-failure-code" />
            <BreakdownSection icon={UsersIcon} title="By Agent (feature)" items={data.by_feature || []} colorKey="feature" testId="autopsy-by-feature" />
            <BreakdownSection icon={Target} title="By Confidence Bucket" items={data.by_confidence_bucket || []} colorKey="confidence_bucket" testId="autopsy-by-confidence-bucket" />
            <BreakdownSection icon={Activity} title="By Direction" items={data.by_direction || []} colorKey="direction" testId="autopsy-by-direction" />
            <BreakdownSection icon={Layers} title="By Sector" items={data.by_sector || []} colorKey="sector" testId="autopsy-by-sector" />
            <BreakdownSection icon={Layers} title="By Model Version" items={data.by_model_version || []} colorKey="model_version" testId="autopsy-by-model-version" />
          </div>

          <TopOffenders offenders={data.top_offenders || []} />
          <SamplesTable samples={data.samples || []} />
        </>
      )}

      <div className="text-slate-500 text-[10px] text-right">
        Generated {data.generated_at ? new Date(data.generated_at).toLocaleString() : '—'}
      </div>
    </div>
  );
};

export default ToxicSpikeAutopsyPanel;
