import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Brain, Activity, AlertTriangle, RefreshCw, Lock, Layers,
  GitMerge, Telescope, Shield, Clock,
} from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Shelly Diagnostic Tile.
 *
 * Read-only window into the learning core ("Shelly") — the IP
 * learning layer that sits *next to* the decision pipeline, not
 * inside it. Per the Alpha rollout protocol:
 *
 *   1. Diagnostic endpoint                     ← this tile reads
 *   2. Read-only corridor annotation           ← gated on review
 *   3. Shadow confidence delta logging         ← gated on review
 *   4. Review 50–100 cycles                    ← human checkpoint
 *   5. Gated confidence influence              ← gated on review
 *
 * The tile renders Shelly's situational memory + confusion state
 * without granting her any execution authority. Operator can
 * watch what she notices before deciding whether to let her
 * influence anything.
 */

const ROLLOUT_STEP_DESCRIPTIONS = {
  1: 'Diagnostic only — observation, no influence.',
  2: 'Read-only corridor annotation.',
  3: 'Shadow confidence delta logging.',
  4: 'Review window — 50–100 cycles.',
  5: 'Gated confidence influence.',
};

const ShellyDiagnosticTile = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchDiag = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(`${API}/admin/learning-core/diagnostic`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      logger.error('shelly diagnostic fetch failed:', e);
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchDiag(); }, [fetchDiag]);

  // Derived data for clean rendering.
  const cacl = data?.core?.cacl;
  const memory = data?.core?.regime_memory;
  const pretells = data?.core?.pretell_clusters || [];
  const guardrails = data?.core?.guardrails;
  const awaiting = data?.awaiting_rollout || {};

  const totalSeen = useMemo(() => {
    if (!cacl?.n_seen_per_class) return 0;
    return cacl.n_seen_per_class.reduce((a, b) => a + b, 0);
  }, [cacl]);

  // Confusion matrix — render as small inline grid.
  const cmGrid = useMemo(() => {
    if (!cacl?.confusion_matrix) return null;
    const cm = cacl.confusion_matrix;
    const max = Math.max(1, ...cm.flat());
    return cm.map((row, i) => row.map((v, j) => ({
      i, j, v,
      diag: i === j,
      intensity: v / max,
    })));
  }, [cacl]);

  return (
    <div className="p-6 space-y-5" data-testid="shelly-diagnostic-tile">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-cyan-500/30 to-purple-500/30 border border-cyan-400/40 flex items-center justify-center">
            <Brain className="w-5 h-5 text-cyan-300" />
          </div>
          <div>
            <h3 className="text-white text-base font-bold">
              {cacl?.name || 'Shelly'}
              <span className="text-slate-400 text-xs font-normal ml-2">
                Learning Core · Patent M
              </span>
            </h3>
            <p className="text-[11px] text-slate-500">
              Historical situational memory · observes, never steers
            </p>
          </div>
        </div>
        <Button size="sm" variant="outline" onClick={fetchDiag}
          className="text-[10px] h-7 px-2 bg-slate-800 border-slate-400/30 text-white"
          data-testid="shelly-refresh-btn">
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {error && (
        <Card className="bg-rose-900/30 border-rose-500/40 rounded-xl p-3" data-testid="shelly-error">
          <p className="text-xs text-rose-300">{error}</p>
        </Card>
      )}

      {/* Rollout state banner */}
      {data && (
        <Card
          className="bg-slate-800/60 border border-cyan-500/30 rounded-xl p-4"
          data-testid="shelly-rollout-banner"
        >
          <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
            <div className="flex items-center gap-2">
              <Shield className="w-4 h-4 text-cyan-300" />
              <span className="text-white text-sm font-semibold">
                Rollout Step {data.rollout_step} / 5
              </span>
              <Badge className="text-[9px] bg-cyan-500/15 text-cyan-300 border-cyan-500/40">
                {data.rollout_step_label}
              </Badge>
            </div>
            <Badge
              className={`text-[9px] border ${
                data.wired_into_decision_flow
                  ? 'bg-rose-500/15 text-rose-300 border-rose-500/40'
                  : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
              }`}
              data-testid="shelly-wired-badge"
            >
              {data.wired_into_decision_flow
                ? 'WIRED INTO DECISIONS'
                : 'NOT WIRED — observation only'}
            </Badge>
          </div>
          <p className="text-[11px] text-slate-400 leading-relaxed">
            {ROLLOUT_STEP_DESCRIPTIONS[data.rollout_step] || '—'}
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
            {Object.entries(data.env_flags || {}).map(([k, v]) => {
              const isString = typeof v === 'string';
              const isCanonical = k.startsWith('REGIME_MEMORY_');
              const dotClass = isString
                ? 'bg-cyan-400'
                : (v ? 'bg-emerald-400' : 'bg-slate-600');
              const label = isCanonical
                ? k.replace('REGIME_MEMORY_', 'engine.').toLowerCase()
                : k.replace('LEARNING_CORE_', '').replace('_', ' ').toLowerCase();
              const display = isString ? `${label}=${v}` : label;
              return (
                <div
                  key={k}
                  className="flex items-center gap-1.5 text-[10px]"
                  data-testid={`shelly-env-${k}`}
                  title={k}
                >
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotClass}`} />
                  <span className={`font-mono truncate ${
                    isCanonical ? 'text-cyan-300' : 'text-slate-400'
                  }`}>
                    {display}
                  </span>
                </div>
              );
            })}
          </div>
          {/* Canonical engine state callout — most operationally
              important flag, labelled prominently. */}
          {data.env_flags?.REGIME_MEMORY_ENABLED !== undefined && (
            <p className="text-[10px] text-slate-500 mt-3 pt-3 border-t border-slate-700/40 leading-relaxed" data-testid="shelly-canonical-engine-state">
              <span className="font-semibold text-cyan-300">Canonical regime-memory engine:</span>{' '}
              {data.env_flags.REGIME_MEMORY_ENABLED ? (
                <>
                  <span className="text-emerald-300">ENABLED</span> · mode={' '}
                  <span className="font-mono text-slate-300">
                    {data.env_flags.REGIME_MEMORY_MODE}
                  </span>
                  {' '} — Shelly stores memories;{' '}
                  {data.env_flags.REGIME_MEMORY_MODE === 'shadow'
                    ? 'shadow mode is observation-only and cannot influence sizing/risk.'
                    : 'mode is permissive — verify operator intent.'}
                </>
              ) : (
                <span className="text-rose-300">
                  DISABLED — every Patent M flag above is a silent no-op until this is on.
                </span>
              )}
            </p>
          )}
        </Card>
      )}

      {/* Top stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="shelly-memory-depth">
          <div className="flex items-center gap-2 mb-2">
            <Layers className="w-4 h-4 text-cyan-400" />
            <span className="text-slate-300 text-xs">Memory Depth</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : (memory?.total_memories ?? 0).toLocaleString()}
          </p>
          <p className="text-[10px] text-slate-500 mt-1">resolved memories</p>
        </Card>

        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="shelly-regime-clusters">
          <div className="flex items-center gap-2 mb-2">
            <GitMerge className="w-4 h-4 text-purple-400" />
            <span className="text-slate-300 text-xs">Regime Clusters</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : (memory?.total_regime_clusters ?? 0)}
          </p>
          <p className="text-[10px] text-slate-500 mt-1">distinct market states</p>
        </Card>

        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="shelly-pretell-clusters">
          <div className="flex items-center gap-2 mb-2">
            <Telescope className="w-4 h-4 text-amber-300" />
            <span className="text-slate-300 text-xs">Pre-Tell Patterns</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : (memory?.total_pretell_clusters ?? 0)}
          </p>
          <p className="text-[10px] text-slate-500 mt-1">shift forerunners</p>
        </Card>

        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="shelly-batches-seen">
          <div className="flex items-center gap-2 mb-2">
            <Activity className="w-4 h-4 text-emerald-300" />
            <span className="text-slate-300 text-xs">Examples Seen</span>
          </div>
          <p className="text-2xl font-black text-white">
            {loading ? '—' : totalSeen.toLocaleString()}
          </p>
          <p className="text-[10px] text-slate-500 mt-1">across all classes</p>
        </Card>
      </div>

      {/* Active regime clusters */}
      <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="shelly-active-regimes">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <GitMerge className="w-4 h-4 text-purple-400" />
            <span className="text-white text-sm font-semibold">Active Regime Clusters</span>
          </div>
          <Badge className="text-[9px] bg-slate-700 text-slate-300 border-slate-600">
            top 10 by sample count
          </Badge>
        </div>
        {memory?.top_clusters?.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs" data-testid="shelly-regime-table">
              <thead>
                <tr className="text-slate-500 border-b border-slate-600/50">
                  <th className="text-left py-2 font-medium">Cluster</th>
                  <th className="text-right py-2 font-medium">Samples</th>
                  <th className="text-right py-2 font-medium">Win Rate</th>
                  <th className="text-right py-2 font-medium">Avg PnL</th>
                  <th className="text-right py-2 font-medium">Hold (d)</th>
                  <th className="text-right py-2 font-medium">Shifts</th>
                </tr>
              </thead>
              <tbody>
                {memory.top_clusters.map((c) => (
                  <tr key={c.cluster_id} className="border-b border-slate-700/30 hover:bg-slate-700/30" data-testid={`shelly-cluster-${c.cluster_id}`}>
                    <td className="py-2 text-white font-mono truncate max-w-[200px]">{c.cluster_id}</td>
                    <td className="py-2 text-right text-slate-300">{c.count ?? 0}</td>
                    <td className="py-2 text-right">
                      <span className={`font-mono ${
                        (c.win_rate ?? 0) >= 0.55 ? 'text-emerald-300'
                        : (c.win_rate ?? 0) <= 0.40 ? 'text-rose-300'
                        : 'text-slate-300'
                      }`}>
                        {((c.win_rate ?? 0) * 100).toFixed(0)}%
                      </span>
                    </td>
                    <td className="py-2 text-right">
                      <span className={`font-mono ${
                        (c.avg_pnl ?? 0) > 0 ? 'text-emerald-300'
                        : (c.avg_pnl ?? 0) < 0 ? 'text-rose-300'
                        : 'text-slate-300'
                      }`}>
                        {(c.avg_pnl ?? 0) > 0 ? '+' : ''}{(c.avg_pnl ?? 0).toFixed(2)}%
                      </span>
                    </td>
                    <td className="py-2 text-right text-slate-400 font-mono">{(c.avg_holding_days ?? 0).toFixed(1)}</td>
                    <td className="py-2 text-right text-amber-300 font-mono">{c.regime_shifts ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : !loading ? (
          <p className="text-xs text-slate-500 italic py-2">
            No regime clusters yet. Shelly starts learning when resolved trades flow in via <code className="text-cyan-300">add_resolved_memory</code>.
          </p>
        ) : null}
      </Card>

      {/* Pre-tell patterns */}
      <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="shelly-pretells">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Telescope className="w-4 h-4 text-amber-300" />
            <span className="text-white text-sm font-semibold">Pre-Tell Forerunner Patterns</span>
          </div>
          <Badge className="text-[9px] bg-amber-500/10 text-amber-300 border-amber-500/30">
            shift detection
          </Badge>
        </div>
        {pretells.length > 0 ? (
          <div className="space-y-2">
            {pretells.slice(0, 5).map((p) => (
              <div
                key={p.cluster_id}
                className="rounded border border-amber-500/20 bg-amber-500/5 p-3"
                data-testid={`shelly-pretell-${p.cluster_id}`}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-300" />
                    <span className="text-white text-xs font-mono">{p.shift_type}</span>
                  </div>
                  <span className="text-[10px] text-slate-400">
                    {p.sample_count} samples · {p.avg_days_to_shift?.toFixed(1)}d avg lead
                  </span>
                </div>
                <div className="grid grid-cols-3 sm:grid-cols-6 gap-1.5 text-[10px]">
                  {Object.entries(p.centroid || {}).map(([k, v]) => (
                    <div key={k} className="bg-slate-900/40 rounded px-2 py-1 text-center">
                      <div className="text-slate-500 uppercase tracking-wider text-[8px]">{k.replace('_', ' ')}</div>
                      <div className="text-slate-300">{v}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : !loading ? (
          <p className="text-xs text-slate-500 italic py-2">
            No pre-tell patterns observed yet. These appear when memories with <code className="text-cyan-300">regime_shift_detected=True</code> accumulate enough samples ({'>='}3 default).
          </p>
        ) : null}
      </Card>

      {/* Confusion hotspots */}
      <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid="shelly-confusion">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-rose-300" />
            <span className="text-white text-sm font-semibold">Confusion Hotspots</span>
          </div>
          {cacl?.hardest_confusion ? (
            <Badge className="text-[9px] bg-rose-500/10 text-rose-300 border-rose-500/30">
              hardest: class {cacl.hardest_confusion.true_label} → {cacl.hardest_confusion.predicted_label} ({cacl.hardest_confusion.count}×)
            </Badge>
          ) : (
            <Badge className="text-[9px] bg-emerald-500/10 text-emerald-300 border-emerald-500/30">
              no off-diagonal mistakes
            </Badge>
          )}
        </div>
        {cmGrid && (
          <div className="flex items-start gap-4 flex-wrap">
            <div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">
                Confusion matrix (true → predicted)
              </div>
              <div className="inline-grid gap-0.5" style={{
                gridTemplateColumns: `repeat(${cmGrid[0]?.length || 1}, minmax(36px, 1fr))`
              }}>
                {cmGrid.flat().map(({ i, j, v, diag, intensity }) => (
                  <div
                    key={`${i}-${j}`}
                    className={`h-9 flex items-center justify-center text-[10px] font-mono rounded ${
                      diag
                        ? 'bg-emerald-500/20 text-emerald-200 border border-emerald-500/30'
                        : intensity > 0
                          ? 'bg-rose-500/15 text-rose-200 border border-rose-500/20'
                          : 'bg-slate-700/30 text-slate-500 border border-slate-700/40'
                    }`}
                    style={!diag && intensity > 0 ? { opacity: 0.4 + 0.6 * intensity } : {}}
                    data-testid={`shelly-cm-${i}-${j}`}
                  >
                    {v}
                  </div>
                ))}
              </div>
              <div className="text-[10px] text-slate-500 mt-1.5">
                diag = correct · off-diag = mistakes (darker = more frequent)
              </div>
            </div>
            <div className="text-[11px] text-slate-400 max-w-md">
              {cacl?.n_seen_per_class && (
                <>
                  <div className="text-slate-500 uppercase tracking-wider text-[10px] mb-1">
                    Memory bank depth per class
                  </div>
                  <div className="space-y-0.5 font-mono text-[11px]">
                    {cacl.n_seen_per_class.map((n, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <span className="text-slate-500">class {i}:</span>
                        <span className="text-white">{n.toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </Card>

      {/* Awaiting-rollout placeholders */}
      {awaiting && (
        <Card className="bg-slate-800/40 border-slate-700/50 rounded-xl p-4" data-testid="shelly-awaiting">
          <div className="flex items-center gap-2 mb-3">
            <Clock className="w-4 h-4 text-slate-400" />
            <span className="text-slate-300 text-sm font-semibold">Awaiting Rollout</span>
            <Lock className="w-3 h-3 text-slate-500 ml-auto" />
          </div>
          <p className="text-[11px] text-slate-500 mb-3 leading-relaxed">
            These metrics activate when the corresponding rollout step is approved
            and wired in. The endpoint surfaces them as empty so the operator can
            verify the gates are still closed.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {Object.entries(awaiting).map(([key, info]) => (
              <div
                key={key}
                className="rounded border border-slate-700/50 bg-slate-900/40 p-3"
                data-testid={`shelly-awaiting-${key}`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-slate-300 text-xs font-mono truncate">
                    {key.replace(/_/g, ' ')}
                  </span>
                  <Badge className="text-[9px] bg-slate-700/60 text-slate-400 border-slate-600/50 ml-2 shrink-0">
                    step {info?.available_after_step}
                  </Badge>
                </div>
                <p className="text-[10px] text-slate-500 leading-snug">
                  {info?.label || '—'}
                </p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Guardrails footer */}
      {guardrails && (
        <p className="text-center text-[10px] text-slate-500" data-testid="shelly-guardrails-footer">
          Guardrails · max confidence delta ±{guardrails.max_confidence_delta} · {guardrails.feature_dim}-dim features · {guardrails.n_classes} classes
        </p>
      )}
    </div>
  );
};

export default ShellyDiagnosticTile;
