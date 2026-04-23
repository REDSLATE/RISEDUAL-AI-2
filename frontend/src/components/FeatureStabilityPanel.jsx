/**
 * FeatureStabilityPanel — rollup of which features dominate the
 * agent's top-3 reasoning over a trailing window.
 *
 * Surfaces three signals the raw event feed can't:
 *   - Dominance: how often a feature lands in the top-3
 *   - Drift: signed `avg_impact` — regime-shift signal if a
 *     feature's average contribution flips sign or trends away
 *     from its long-run mean
 *   - Balance: `bullish_frac` near 0.5 = feature is noisy /
 *     non-directional in the current regime
 *
 * Rendered as a compact, at-a-glance visualization — horizontal
 * bar length = average |impact| (importance magnitude), bar color
 * encodes bullish-dominance fraction, count chip on the right.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { BarChart3, RefreshCcw } from 'lucide-react';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';

const API = `${getApiBase()}/api/agent/feature-stability`;

// Map bullish_frac → bar color.
// 0.0 → deep red, 0.5 → slate (mixed / noisy), 1.0 → teal-green.
// Chosen to match the "bullish = teal, bearish = red" convention
// already in use on the Agent Activity feed.
function barColor(bullishFrac) {
  if (bullishFrac == null) return 'bg-slate-500';
  if (bullishFrac >= 0.75) return 'bg-emerald-500';
  if (bullishFrac >= 0.55) return 'bg-emerald-500/70';
  if (bullishFrac >= 0.45) return 'bg-slate-500';
  if (bullishFrac >= 0.25) return 'bg-red-500/70';
  return 'bg-red-500';
}

function directionLabel(bullishFrac) {
  if (bullishFrac == null) return '—';
  if (bullishFrac >= 0.75) return 'bullish';
  if (bullishFrac >= 0.55) return 'mostly bullish';
  if (bullishFrac >= 0.45) return 'mixed';
  if (bullishFrac >= 0.25) return 'mostly bearish';
  return 'bearish';
}

const FeatureRow = ({ row, maxAbsImpact }) => {
  const barWidth =
    maxAbsImpact > 0
      ? Math.max(4, Math.round((row.avg_abs_impact / maxAbsImpact) * 100))
      : 0;
  return (
    <div
      className="group grid grid-cols-[1fr_auto] items-center gap-3 py-1.5 px-2 hover:bg-slate-800/40 rounded"
      data-testid={`feature-stability-row-${row.feature}`}
    >
      <div className="min-w-0">
        <div className="flex items-baseline justify-between gap-2 mb-0.5">
          <span
            className="text-xs font-mono text-white truncate"
            title={row.feature}
          >
            {row.feature}
          </span>
          <span className="text-[9px] text-slate-400 shrink-0 tabular-nums">
            {directionLabel(row.bullish_frac)}
          </span>
        </div>
        <div
          className="h-1.5 bg-slate-900/60 rounded-full overflow-hidden"
          title={`avg |impact|: ${row.avg_abs_impact.toFixed(3)}`}
        >
          <div
            className={`h-full ${barColor(row.bullish_frac)} transition-all`}
            style={{ width: `${barWidth}%` }}
          />
        </div>
      </div>
      <div className="text-right shrink-0 w-14">
        <div
          className="text-xs font-bold text-white tabular-nums"
          data-testid="feature-stability-appearances"
        >
          {row.appearances}×
        </div>
        <div
          className={`text-[9px] tabular-nums ${
            row.avg_impact >= 0 ? 'text-emerald-400' : 'text-red-400'
          }`}
        >
          {row.avg_impact >= 0 ? '+' : ''}
          {row.avg_impact.toFixed(3)}
        </div>
      </div>
    </div>
  );
};

const FeatureStabilityPanel = ({ days = 7 }) => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [windowDays, setWindowDays] = useState(days);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(
        `${API}?days=${windowDays}&min_appearances=1&top_k=15`,
      );
      if (!res.ok) return;
      const data = await res.json();
      setRows(data.features || []);
    } catch (e) {
      logger.warn('feature stability load failed', e);
    } finally {
      setLoading(false);
    }
  }, [windowDays]);

  useEffect(() => {
    load();
    // Polling every 2 minutes — this aggregate moves slowly, no
    // value in faster refresh than that.
    const id = setInterval(load, 120_000);
    return () => clearInterval(id);
  }, [load]);

  const maxAbsImpact = rows.reduce(
    (m, r) => Math.max(m, r.avg_abs_impact || 0),
    0,
  );

  return (
    <div
      className="bg-slate-900/70 border border-slate-700/60 rounded-xl overflow-hidden"
      data-testid="feature-stability-panel"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/60">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-[#3DE8D9]" />
          <h3 className="text-sm font-bold text-white">Model's Mind</h3>
        </div>
        <div className="flex items-center gap-1">
          {[1, 7, 30].map((d) => (
            <button
              key={d}
              onClick={() => setWindowDays(d)}
              className={`text-[10px] px-2 py-0.5 rounded ${
                windowDays === d
                  ? 'bg-[#3DE8D9]/20 text-[#3DE8D9] border border-[#3DE8D9]/40'
                  : 'text-slate-400 hover:text-white border border-transparent'
              }`}
              data-testid={`feature-stability-window-${d}`}
            >
              {d}d
            </button>
          ))}
          <button
            onClick={load}
            disabled={loading}
            className="ml-1 text-slate-400 hover:text-white disabled:opacity-40"
            aria-label="Refresh"
            data-testid="feature-stability-refresh"
          >
            <RefreshCcw
              className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`}
            />
          </button>
        </div>
      </div>
      <div className="px-2 py-2">
        {rows.length === 0 ? (
          <div
            className="px-3 py-6 text-center"
            data-testid="feature-stability-empty"
          >
            <BarChart3 className="w-5 h-5 text-slate-600 mx-auto mb-2" />
            <p className="text-xs text-slate-400">
              Not enough decisions yet.
            </p>
            <p className="text-[10px] text-slate-500 mt-0.5">
              Features the agent leans on will appear here.
            </p>
          </div>
        ) : (
          <>
            <div className="space-y-0">
              {rows.map((r) => (
                <FeatureRow
                  key={r.feature}
                  row={r}
                  maxAbsImpact={maxAbsImpact}
                />
              ))}
            </div>
            <p className="text-[9px] text-slate-500 mt-2 px-2 italic">
              Bar = average |impact|. Color = bullish balance. Count = top-3 appearances ({windowDays}d).
            </p>
          </>
        )}
      </div>
    </div>
  );
};

export default FeatureStabilityPanel;
