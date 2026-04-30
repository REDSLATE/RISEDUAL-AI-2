import React, { useCallback, useEffect, useState } from 'react';
import { TrendingUp, TrendingDown, Minus, AlertTriangle } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api/admin/conviction/quality-kpis`;

/**
 * QualityKPIsStrip — surfaces the two operator-facing risk-quality
 * metrics from the cleaned-up `predictions` collection:
 *
 *   1. Unique-failure-pattern count over time (post-dedup count of
 *      distinct ``(symbol, failure_code)`` tuples per week).
 *   2. Calibration gap rolling chart (avg_confidence − empirical
 *      accuracy per week).
 *
 * Designed as a strip — sits above the conviction-bucket grid in the
 * Conviction admin tab. Both KPIs are intentionally simple: the
 * complexity (ECE, monotonicity badges, per-tier sparklines) lives
 * downstream in `ConvictionCalibration`. This strip's job is to
 * answer one question at a glance: *is risk quality improving or
 * degrading week-over-week?*
 */
const QualityKPIsStrip = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(`${API}?weeks=8`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setError(e.message || 'load_failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !data) {
    return (
      <div
        className="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40 text-xs text-slate-500"
        data-testid="quality-kpis-loading"
      >
        Loading risk-quality KPIs…
      </div>
    );
  }
  if (error) {
    return (
      <div
        className="p-3 rounded-lg bg-slate-800/40 border border-red-500/30 text-xs text-red-300"
        data-testid="quality-kpis-error"
      >
        Risk-quality KPIs error: {error}
      </div>
    );
  }
  if (!data) return null;

  const failures = data.unique_failure_patterns || [];
  const gaps = data.calibration_gap || [];
  const summary = data.summary || {};

  return (
    <div
      className="grid grid-cols-1 md:grid-cols-2 gap-3"
      data-testid="quality-kpis-strip"
    >
      <UniqueFailuresCard failures={failures} summary={summary} />
      <CalibrationGapCard gaps={gaps} summary={summary} />
    </div>
  );
};

// ── Unique failure patterns card ──────────────────────────────────────

const UniqueFailuresCard = ({ failures, summary }) => {
  const maxCount = Math.max(1, ...failures.map((f) => f.count));
  const latest = failures[failures.length - 1];
  const latestPatterns = latest?.patterns || [];

  return (
    <div
      className="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40"
      data-testid="quality-kpis-failures-card"
    >
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <div className="text-[10px] text-slate-400 uppercase tracking-wider">
            Unique Failure Patterns
          </div>
          <div className="text-[10px] text-slate-500">
            distinct (symbol, failure_code) per week
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold font-mono text-amber-300 tabular-nums">
            {summary.current_unique_failures ?? '—'}
          </div>
          <div className="text-[9px] text-slate-500">latest</div>
        </div>
      </div>
      {/* Bar chart — one column per week, height = count / max */}
      <div
        className="h-12 flex items-end gap-1"
        data-testid="quality-kpis-failures-chart"
      >
        {failures.map((f) => {
          const height = (f.count / maxCount) * 100;
          return (
            <div
              key={f.week_start}
              className="flex-1 flex flex-col justify-end"
              title={`${f.week_start}: ${f.count} unique`}
            >
              <div
                className={`w-full rounded-t-sm ${
                  f.count === 0
                    ? 'bg-slate-700/40'
                    : f.count >= 5
                    ? 'bg-red-400'
                    : 'bg-amber-400'
                } transition-all`}
                style={{ height: f.count === 0 ? '2px' : `${Math.max(height, 8)}%` }}
              />
            </div>
          );
        })}
      </div>
      {latestPatterns.length > 0 && (
        <div className="mt-3 space-y-0.5">
          <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">
            This week's patterns
          </div>
          {latestPatterns.slice(0, 4).map((p) => (
            <div
              key={`${p.symbol}-${p.failure_code}`}
              className="text-[11px] flex items-center justify-between"
            >
              <span className="text-slate-300 font-mono">{p.symbol}</span>
              <span className="text-slate-500">{p.failure_code}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── Calibration gap card ──────────────────────────────────────────────

const CalibrationGapCard = ({ gaps, summary }) => {
  const populated = gaps.filter((g) => g.gap !== null);
  // Plot range — symmetric around 0 so over- and under-confidence are
  // visually distinguishable. Use max abs gap or 0.10, whichever is
  // larger, so small gaps don't get visually exaggerated.
  const yScale = Math.max(0.1, ...populated.map((g) => Math.abs(g.gap)));
  const TrendIcon =
    summary.trend === 'improving'
      ? TrendingDown
      : summary.trend === 'degrading'
      ? TrendingUp
      : Minus;
  const trendColor =
    summary.trend === 'improving'
      ? 'text-emerald-400'
      : summary.trend === 'degrading'
      ? 'text-red-400'
      : 'text-slate-400';

  return (
    <div
      className="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40"
      data-testid="quality-kpis-gap-card"
    >
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <div className="text-[10px] text-slate-400 uppercase tracking-wider">
            Calibration Gap
          </div>
          <div className="text-[10px] text-slate-500">
            avg_conf − accuracy · 8w rolling
          </div>
        </div>
        <div className="text-right">
          <div
            className={`text-2xl font-semibold font-mono tabular-nums ${
              Math.abs(summary.current_gap || 0) >= 0.18
                ? 'text-red-400'
                : Math.abs(summary.current_gap || 0) >= 0.1
                ? 'text-amber-300'
                : 'text-emerald-400'
            }`}
          >
            {summary.current_gap == null
              ? '—'
              : `${summary.current_gap > 0 ? '+' : ''}${summary.current_gap.toFixed(3)}`}
          </div>
          <div
            className={`text-[9px] flex items-center justify-end gap-1 ${trendColor}`}
            data-testid="quality-kpis-gap-trend"
          >
            <TrendIcon className="w-3 h-3" />
            {summary.trend || 'unknown'}
          </div>
        </div>
      </div>
      {/* SVG line chart with zero reference */}
      <svg
        viewBox="0 0 100 40"
        className="w-full h-12"
        preserveAspectRatio="none"
        data-testid="quality-kpis-gap-chart"
      >
        <line
          x1="0"
          x2="100"
          y1="20"
          y2="20"
          stroke="rgb(71 85 105)"
          strokeWidth="0.5"
          strokeDasharray="1.5 1.5"
        />
        {/* Build polyline from populated points (skip nulls so gaps show) */}
        {(() => {
          const pts = gaps
            .map((g, i) => {
              if (g.gap == null) return null;
              const x = (i / Math.max(1, gaps.length - 1)) * 100;
              const y = 20 - (g.gap / yScale) * 18;
              return `${x.toFixed(1)},${y.toFixed(1)}`;
            })
            .filter(Boolean)
            .join(' ');
          return (
            <polyline
              points={pts}
              fill="none"
              stroke="#3DE8D9"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          );
        })()}
        {/* Dot at each populated point */}
        {gaps.map((g, i) => {
          if (g.gap == null) return null;
          const x = (i / Math.max(1, gaps.length - 1)) * 100;
          const y = 20 - (g.gap / yScale) * 18;
          return (
            <circle
              key={g.week_start}
              cx={x}
              cy={y}
              r="1.2"
              fill="#3DE8D9"
            />
          );
        })}
      </svg>
      {summary.trend === 'degrading' && (
        <div
          className="mt-2 text-[10px] text-red-300 flex items-center gap-1"
          data-testid="quality-kpis-gap-warn"
        >
          <AlertTriangle className="w-3 h-3" />
          Gap widening — consider retraining the conviction weights.
        </div>
      )}
    </div>
  );
};

export default QualityKPIsStrip;
