import React, { useState, useEffect, useCallback } from 'react';
import { Brain, RefreshCw, PowerOff, Undo2, Loader2, AlertCircle, TrendingUp, TrendingDown, Minus, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

// ── Auto-revert audit strip. Lists the most recent auto-reverts
//    surfaced by the safety rail. Each row is expandable (click to
//    show the ΔR/coverage history that triggered the revert).
const AutoRevertStrip = ({ items }) => {
  const [expanded, setExpanded] = useState(null);
  return (
    <div
      className="mb-3 p-3 rounded-lg bg-rose-500/5 border border-rose-500/20"
      data-testid="adaptations-auto-revert-strip"
    >
      <div className="flex items-center gap-2 mb-2">
        <ShieldAlert className="w-3.5 h-3.5 text-rose-300" />
        <div className="text-[10px] uppercase tracking-wider text-rose-300 font-semibold">
          Auto-revert safety rail · {items.length} recent flip{items.length === 1 ? '' : 's'}
        </div>
      </div>
      <ul className="space-y-1.5">
        {items.map((it, i) => {
          const key = it.adaptation_id || `${it.metric}-${it.at}-${i}`;
          const isOpen = expanded === key;
          return (
            <li
              key={key}
              className="rounded bg-slate-900/50 border border-slate-700/40"
              data-testid={`adaptations-auto-revert-row-${i}`}
            >
              <button
                type="button"
                onClick={() => setExpanded(isOpen ? null : key)}
                className="w-full flex items-start justify-between gap-2 px-2 py-1.5 text-left hover:bg-slate-800/40 transition-colors"
              >
                <div className="min-w-0 flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-mono font-bold text-white">{it.metric}</span>
                  {it.direction && it.direction !== 'ANY' && (
                    <span className="text-[9px] font-mono px-1 rounded bg-slate-700/60 text-slate-300">
                      {it.direction}
                    </span>
                  )}
                  <span className="text-[10px] text-slate-400 tabular-nums">
                    ΔR {it.deltas_r?.map((d) => (d >= 0 ? '+' : '') + d.toFixed(3)).join(' · ')}
                  </span>
                </div>
                <span className="text-[10px] text-slate-500 tabular-nums shrink-0">
                  {it.at ? new Date(it.at).toLocaleString() : '—'}
                </span>
              </button>
              {isOpen && (
                <div
                  className="px-3 py-2 border-t border-slate-700/40 text-[10px] text-slate-300 leading-snug space-y-0.5"
                  data-testid={`adaptations-auto-revert-body-${i}`}
                >
                  <div>
                    <span className="text-slate-500 uppercase tracking-wider text-[9px] mr-1">Δwin_rate</span>
                    <span className="font-mono tabular-nums">
                      {it.deltas_wr?.map((d) => (d >= 0 ? '+' : '') + (d * 100).toFixed(1) + '%').join(' · ')}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 uppercase tracking-wider text-[9px] mr-1">Coverage</span>
                    <span className="font-mono tabular-nums">
                      {it.coverages?.map((c) => (c * 100).toFixed(1) + '%').join(' · ')}
                    </span>
                  </div>
                  <p className="text-slate-500 italic mt-1 break-all">{it.reason}</p>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

const API = `${getApiBase()}/api/admin/adaptations`;

// ── Sign-aware impact badge. Green for improvements, rose for
//    regressions, slate for neutral. When ``pct`` is set we render
//    the raw value × 100 with a `%` suffix (win-rate semantics);
//    otherwise as a signed decimal (expectancy/R). When ``compact``
//    is set we shrink to the inline-row variant for per-adaptation
//    attribution badges — same math, smaller surface.
const ImpactBadge = ({ label, value, pct = false, compact = false }) => {
  if (value === null || value === undefined || !isFinite(value)) return null;
  const isUp = value > 1e-6;
  const isDown = value < -1e-6;
  const Icon = isUp ? TrendingUp : isDown ? TrendingDown : Minus;
  const tone = isUp
    ? 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30'
    : isDown
      ? 'text-rose-300 bg-rose-500/10 border-rose-500/30'
      : 'text-slate-300 bg-slate-700/40 border-slate-600/40';
  const rendered = pct
    ? `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
    : `${value >= 0 ? '+' : ''}${value.toFixed(3)}`;
  if (compact) {
    return (
      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-mono tabular-nums ${tone}`}>
        <Icon className="w-2.5 h-2.5" />
        <span className="text-[9px] uppercase tracking-wider opacity-70">{label}</span>
        {rendered}
      </span>
    );
  }
  return (
    <div className={`flex flex-col items-center justify-center rounded-lg border px-3 py-2 ${tone}`}>
      <div className="flex items-center gap-1 text-[9px] uppercase tracking-wider opacity-70">
        <Icon className="w-3 h-3" />
        {label}
      </div>
      <div className="text-sm font-mono font-bold tabular-nums">{rendered}</div>
    </div>
  );
};

// ── Global impact strip. Renders above the adaptations list with
//    the counterfactual "if these weights had been live last
//    retrain, how would our expected R + win-rate have shifted?"
//    plus coverage (how much of the training set was touched).
//    A high coverage with near-zero deltas is a tell that
//    adaptations are too broad — surfaced with a warning banner.
const ImpactStrip = ({ impact }) => {
  const g = impact?.global || {};
  const ts = impact?.at ? new Date(impact.at).toLocaleString() : null;
  const modelV = impact?.model_version;
  const broadRuleWarning = (
    g.rows_covered_frac != null && g.rows_covered_frac > 0.8
    && Math.abs(g.delta_mean_r || 0) < 0.005
    && Math.abs(g.delta_win_rate || 0) < 0.005
  );
  return (
    <div
      className="mb-3 p-3 rounded-lg bg-slate-900/50 border border-slate-700/40"
      data-testid="adaptations-impact-strip"
    >
      <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">
            Last retrain · counterfactual impact
          </div>
          {modelV && (
            <span className="text-[10px] font-mono text-slate-500">v{modelV}</span>
          )}
        </div>
        {ts && <span className="text-[10px] text-slate-500 tabular-nums">{ts}</span>}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <ImpactBadge label="ΔR (expectancy)" value={g.delta_mean_r} />
        <ImpactBadge label="Δ win-rate" value={g.delta_win_rate} pct />
        <div className="flex flex-col items-center justify-center rounded-lg border border-slate-600/40 bg-slate-800/40 px-3 py-2">
          <div className="text-[9px] uppercase tracking-wider text-slate-400">baseline R / win</div>
          <div className="text-xs font-mono text-slate-200 tabular-nums">
            {typeof g.baseline_mean_r === 'number' ? g.baseline_mean_r.toFixed(3) : '—'}
            {' · '}
            {typeof g.baseline_win_rate === 'number' ? `${(g.baseline_win_rate * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="flex flex-col items-center justify-center rounded-lg border border-slate-600/40 bg-slate-800/40 px-3 py-2">
          <div className="text-[9px] uppercase tracking-wider text-slate-400">rows covered</div>
          <div className="text-xs font-mono text-slate-200 tabular-nums">
            {typeof g.rows_covered_frac === 'number' ? `${(g.rows_covered_frac * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
      </div>
      <p className="text-[10px] text-slate-500 mt-2 leading-snug italic">
        First-order proxy: re-weighted existing R-multiple outcomes. No second fit — not a guarantee of live improvement.
      </p>
      {broadRuleWarning && (
        <div
          className="mt-2 p-2 rounded bg-amber-500/10 border border-amber-500/30 text-amber-200 text-[10px] leading-snug"
          data-testid="adaptations-impact-broad-warning"
        >
          <strong>Heads up:</strong> adaptation set covers &gt;80% of rows with near-zero Δ. Rules may be too broad — tighten <code className="bg-slate-900/50 px-1 rounded">compute_metric_failure_lift</code> thresholds for more selective down-weighting.
        </div>
      )}
    </div>
  );
};

/**
 * ModelAdaptationsPanel — view + revert the bounded row-weight
 * adjustments that the ML retrain engine applies based on recent
 * toxic-alert patterns.
 *
 * Reads ``enabled`` from the backend — when false, the whole
 * adaptation pipeline is in dry-run mode (detection + narration
 * only, training weights untouched). A banner surfaces the state
 * so admins know why their model isn't changing.
 */
const ModelAdaptationsPanel = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [revertingId, setRevertingId] = useState(null);
  const [killing, setKilling] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(API);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      logger.error('[adaptations] fetch', e);
      setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRevert = useCallback(async (id) => {
    setRevertingId(id);
    try {
      const res = await authFetch(`${API}/${encodeURIComponent(id)}/revert`, { method: 'POST' });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d?.detail || `HTTP ${res.status}`);
      toast.success('Adaptation reverted — next retrain will ignore it.');
      await load();
    } catch (e) {
      toast.error(`Revert failed: ${e.message}`);
    } finally {
      setRevertingId(null);
    }
  }, [load]);

  const handleDisableAll = useCallback(async () => {
    if (!window.confirm('Deactivate ALL active adaptations? The next retrain will use pristine severity+regime weighting only. This cannot be undone (audit trail preserved).')) return;
    setKilling(true);
    try {
      const res = await authFetch(`${API}/disable_all`, { method: 'POST' });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d?.detail || `HTTP ${res.status}`);
      toast.success(`Kill switch engaged — ${d.deactivated} adaptation${d.deactivated === 1 ? '' : 's'} deactivated.`);
      await load();
    } catch (e) {
      toast.error(`Disable-all failed: ${e.message}`);
    } finally {
      setKilling(false);
    }
  }, [load]);

  const items = data?.items || [];
  const enabled = data?.enabled ?? false;

  return (
    <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-5" data-testid="model-adaptations-card">
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-xl bg-purple-500/10 border border-purple-500/30 flex items-center justify-center shrink-0">
          <Brain className="w-6 h-6 text-purple-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <h4 className="text-white text-sm font-semibold">ML Adaptations</h4>
              <Badge className={
                enabled
                  ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30 text-[10px]"
                  : "bg-amber-500/15 text-amber-300 border-amber-500/30 text-[10px]"
              }>
                {enabled ? 'APPLYING' : 'DRY-RUN'}
              </Badge>
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={load}
                disabled={loading}
                className="h-7 px-2 text-slate-300 hover:text-white hover:bg-slate-700/60"
                data-testid="adaptations-refresh"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </Button>
              {items.length > 0 && (
                <Button
                  onClick={handleDisableAll}
                  disabled={killing}
                  className="h-7 px-2.5 text-[10px] bg-rose-500/15 text-rose-200 hover:bg-rose-500/25 border border-rose-500/40"
                  data-testid="adaptations-kill-switch"
                >
                  {killing ? <Loader2 className="w-3 h-3 animate-spin" /> : <><PowerOff className="w-3 h-3 mr-1" /> Disable all</>}
                </Button>
              )}
            </div>
          </div>
          {!enabled && (
            <div className="mb-3 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs flex items-start gap-2" data-testid="adaptations-dryrun-banner">
              <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <div>
                <strong className="text-amber-100">Dry-run mode.</strong>{' '}
                Detection and narration are live in the Agent Activity feed, but training weights are NOT modified. Set <code className="bg-slate-900/50 px-1 rounded text-[10px]">ML_ADAPTATION_ENABLED=true</code> in backend env to activate.
              </div>
            </div>
          )}
          <p className="text-slate-300 text-xs leading-relaxed mb-3">
            Bounded row-weight adjustments applied at retrain time based on recent toxic-alert patterns. Each adaptation is capped at ±30%, expires in 14 days, and is fully reversible.
          </p>

          {error && (
            <div className="mb-3 p-2 rounded-lg bg-rose-900/30 border border-rose-700/40 text-rose-200 text-xs">{error}</div>
          )}

          {!loading && items.length === 0 && !error && (
            <div className="p-4 rounded-lg bg-slate-900/40 border border-slate-700/40 text-slate-400 text-xs" data-testid="adaptations-empty">
              No active adaptations. Retrain will use pristine severity + regime weighting. New adaptations appear here after 3+ toxic alerts share the same failure code.
            </div>
          )}

          {/* Impact strip — surfaces the last retrain's counterfactual
              ΔR and Δwin-rate so admins can see whether adaptations
              are actually moving expected outcome. First-order proxy
              (re-weighted existing R-multiples, no second fit). */}
          {data?.last_impact?.global && (
            <ImpactStrip impact={data.last_impact} />
          )}

          {/* Auto-revert audit trail — shown when the safety rail
              has flipped adaptations recently. Silent when empty
              so operators who never enable it see no noise. */}
          {Array.isArray(data?.recent_auto_reverts) && data.recent_auto_reverts.length > 0 && (
            <AutoRevertStrip items={data.recent_auto_reverts} />
          )}

          <div className="space-y-2" data-testid="adaptations-list">
            {items.map((a) => {
              const pct = Math.round((1 - a.adjustment_factor) * 100);
              const weightDirection = a.adjustment_factor < 1 ? 'down' : 'up';
              // Per-adaptation impact from last retrain (if this
              // adaptation was in that run's adaptations_applied).
              const perAd = (data?.last_impact?.per_adaptation || [])
                .find((p) => p.adaptation_id === a.adaptation_id);
              return (
                <div
                  key={a.adaptation_id}
                  className="rounded-lg bg-slate-900/50 border border-slate-700/40 p-3"
                  data-testid={`adaptation-row-${a.metric}`}
                >
                  <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs font-mono font-bold text-white">{a.metric}</span>
                        {a.direction && a.direction !== 'ANY' && (
                          <Badge className={
                            a.direction === 'LONG'
                              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30 text-[10px]'
                              : 'bg-rose-500/10 text-rose-300 border-rose-500/30 text-[10px]'
                          }>
                            {a.direction}
                          </Badge>
                        )}
                        <Badge className={`text-[10px] ${weightDirection === 'down' ? 'bg-rose-500/15 text-rose-200 border-rose-500/30' : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'}`}>
                          {weightDirection === 'down' ? `−${pct}%` : `+${pct}%`} weight
                        </Badge>
                        <Badge className="bg-slate-700/60 text-slate-200 border-slate-600/40 text-[10px]">
                          {a.evidence_count} toxic event{a.evidence_count === 1 ? '' : 's'}
                        </Badge>
                        {a.contrast != null && (
                          <Badge className="bg-purple-500/15 text-purple-200 border-purple-500/30 text-[10px]" title={`Bucket failure rate ${((a.bucket_rate || 0) * 100).toFixed(1)}% vs global ${((a.global_rate || 0) * 100).toFixed(1)}%`}>
                            {a.contrast.toFixed(2)}× baseline
                          </Badge>
                        )}
                        {typeof a.severity === 'number' && a.severity > 0 && (
                          <Badge className="bg-amber-500/10 text-amber-200 border-amber-500/30 text-[10px]" title="Mean |return_1d| on failing rows in this bucket">
                            {(a.severity * 100).toFixed(1)}% avg miss
                          </Badge>
                        )}
                      </div>
                      <p className="text-[11px] text-slate-400 mt-1 leading-snug">{a.description}</p>
                      {perAd && (
                        <div
                          className="mt-1.5 flex items-center gap-2 flex-wrap"
                          data-testid={`adaptation-impact-${a.metric}`}
                        >
                          <span className="text-[9px] uppercase tracking-wider text-slate-500 font-semibold">
                            last retrain impact:
                          </span>
                          <ImpactBadge label="ΔR" value={perAd.delta_mean_r} compact />
                          <ImpactBadge label="Δwin" value={perAd.delta_win_rate} compact pct />
                        </div>
                      )}
                      <p className="text-[10px] text-slate-500 mt-1 tabular-nums">
                        created {a.created_at?.slice(0, 16).replace('T', ' ')} · expires {String(a.expires_at).slice(0, 10)}
                      </p>
                    </div>
                    <Button
                      onClick={() => handleRevert(a.adaptation_id)}
                      disabled={revertingId === a.adaptation_id}
                      className="h-7 px-2.5 text-[10px] bg-slate-700/60 text-slate-200 hover:bg-slate-600 shrink-0"
                      data-testid={`adaptation-revert-${a.metric}`}
                    >
                      {revertingId === a.adaptation_id ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <><Undo2 className="w-3 h-3 mr-1" /> Revert</>
                      )}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Card>
  );
};

export default ModelAdaptationsPanel;
