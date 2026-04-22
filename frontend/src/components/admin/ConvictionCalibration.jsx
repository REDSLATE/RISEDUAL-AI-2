import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, TrendingUp, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';
import MLHealthStrip from './MLHealthStrip';

const API = `${getApiBase()}/api`;

const WINDOWS = [7, 30, 90];

// Tier-specific accent so the visual matches risk_calculator.py's sizing:
// Strong = full risk (green), Moderate = half (amber), Weak = veto (red).
const TIER_COLOR = {
  strong:   { bar: 'bg-emerald-400',  text: 'text-emerald-300',  border: 'border-emerald-400/30' },
  moderate: { bar: 'bg-amber-400',    text: 'text-amber-300',    border: 'border-amber-400/30' },
  weak:     { bar: 'bg-rose-400',     text: 'text-rose-300',     border: 'border-rose-400/30' },
  default:  { bar: 'bg-slate-400',    text: 'text-slate-300',    border: 'border-slate-400/30' },
};

const fmtPct = (v) => (v == null ? '—' : `${Math.round(v * 1000) / 10}%`);
const fmtRange = ([lo, hi]) => `${lo.toFixed(2)} – ${Math.min(hi, 1).toFixed(2)}`;

// Tiny SVG sparkline for per-bucket 4-week win-rate trend. Accepts an
// array of values in [0,1] or null for gaps. Null points create a
// broken line (segment skipped) so we don't draw misleading zeros for
// weeks with no data. The trailing dot highlights the most recent week.
const Sparkline = ({ series, color = 'currentColor', width = 64, height = 20 }) => {
  if (!Array.isArray(series) || series.length === 0) return null;
  const pad = 2;
  const n = series.length;
  const step = n > 1 ? (width - pad * 2) / (n - 1) : 0;
  const y = (v) => pad + (height - pad * 2) * (1 - Math.max(0, Math.min(1, v)));

  // Build path segments, breaking on nulls so gaps render as gaps.
  const segments = [];
  let current = [];
  series.forEach((v, i) => {
    if (v == null) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    current.push(`${pad + i * step},${y(v).toFixed(1)}`);
  });
  if (current.length) segments.push(current);

  const lastIdx = [...series].reverse().findIndex((v) => v != null);
  const lastAt = lastIdx === -1 ? null : n - 1 - lastIdx;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="overflow-visible"
      aria-hidden="true"
    >
      {/* Reference 50% line — helps eyeball whether bucket beats coin flip */}
      <line
        x1={pad}
        x2={width - pad}
        y1={y(0.5)}
        y2={y(0.5)}
        stroke="rgb(71 85 105)"
        strokeWidth="0.5"
        strokeDasharray="1.5 1.5"
      />
      {segments.map((seg, idx) =>
        seg.length > 1 ? (
          <polyline
            key={idx}
            points={seg.join(' ')}
            fill="none"
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ) : (
          <circle
            key={idx}
            cx={seg[0].split(',')[0]}
            cy={seg[0].split(',')[1]}
            r={1.25}
            fill={color}
          />
        )
      )}
      {lastAt != null && (
        <circle cx={pad + lastAt * step} cy={y(series[lastAt])} r={1.75} fill={color} />
      )}
    </svg>
  );
};

const BucketRow = ({ bucket, trend }) => {
  const color = TIER_COLOR[bucket.tier] || TIER_COLOR.default;
  const pct = bucket.win_rate == null ? 0 : Math.max(0, Math.min(1, bucket.win_rate));
  // Map each bucket tier to its SVG stroke colour. Tailwind classes can't
  // be read off the DOM, so we hardcode the hex for the sparkline only.
  const SPARK_COLOR = {
    strong:   '#34d399',
    moderate: '#fbbf24',
    weak:     '#fb7185',
    default:  '#94a3b8',
  };
  const sparkColor = SPARK_COLOR[bucket.tier] || SPARK_COLOR.default;
  const hasTrend = Array.isArray(trend) && trend.some((v) => v != null);
  return (
    <div
      className={`p-3 rounded-lg bg-slate-800/50 border ${color.border}`}
      data-testid={`conviction-bucket-${bucket.label.toLowerCase()}`}
    >
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className={`text-sm font-semibold ${color.text}`}>{bucket.label}</div>
          <div className="text-[10px] text-slate-500 font-mono">
            {fmtRange(bucket.range)}
          </div>
        </div>
        <div className="text-right">
          <div className="text-lg font-bold text-white tabular-nums">
            {fmtPct(bucket.win_rate)}
          </div>
          <div className="text-[10px] text-slate-400">
            {bucket.correct}/{bucket.total} correct
          </div>
        </div>
      </div>
      <div className="h-1.5 bg-slate-900/60 rounded-full overflow-hidden">
        <div
          className={`h-full ${color.bar} transition-all`}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
      {/* 4-week trend sparkline — shown only when we have at least one
          non-null weekly data point. Keeps the bucket card compact in
          the empty-state case. */}
      {hasTrend && (
        <div
          className="flex items-center justify-between mt-2"
          data-testid={`conviction-trend-${bucket.label.toLowerCase()}`}
        >
          <span className="text-[9px] uppercase tracking-wider text-slate-500">
            4-week trend
          </span>
          <Sparkline series={trend} color={sparkColor} />
        </div>
      )}
    </div>
  );
};

const MonotonicBadge = ({ status, label }) => {
  if (status === null || status === undefined) {
    return (
      <span className="text-[11px] text-slate-500">
        {label}: insufficient data
      </span>
    );
  }
  const Icon = status ? CheckCircle2 : AlertTriangle;
  const color = status ? 'text-emerald-400' : 'text-amber-400';
  const msg = status
    ? 'healthy (win-rate rises with score)'
    : 'miscalibrated (retrain weights)';
  return (
    <span className={`flex items-center gap-1 text-[11px] ${color}`}>
      <Icon className="w-3 h-3" />
      {label}: {msg}
    </span>
  );
};

const ConvictionCalibration = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [windowDays, setWindowDays] = useState(30);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(
        `${API}/admin/conviction/calibration?days=${windowDays}`
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (e) {
      logger.error('conviction calibration load failed', e);
      setError(e.message || 'Failed to load calibration data');
    } finally {
      setLoading(false);
    }
  }, [windowDays]);

  useEffect(() => {
    load();
  }, [load]);

  const hasConvictionData = useMemo(
    () => (data?.total_with_conviction || 0) > 0,
    [data]
  );

  return (
    <div className="p-4 sm:p-6 space-y-5" data-testid="conviction-calibration-panel">
      {/* ML health strip — Tier 3 progress + clamp canary. Sits above
          the calibration buckets so admins see signal integrity at a
          glance before drilling into the per-bucket sparklines. */}
      <MLHealthStrip />

      {/* Header + window selector */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 className="text-white font-semibold flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-[#3DE8D9]" />
            Conviction Calibration
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Win-rate bucketed by conviction score. Healthy = monotonically rising.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div
            className="flex items-center bg-slate-800 rounded-lg border border-slate-400/20 overflow-hidden"
            role="group"
          >
            {WINDOWS.map((d) => (
              <button
                key={d}
                onClick={() => setWindowDays(d)}
                data-testid={`conviction-window-${d}d-btn`}
                className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                  windowDays === d
                    ? 'bg-[#3DE8D9] text-slate-900'
                    : 'text-slate-300 hover:bg-slate-700'
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={load}
            disabled={loading}
            className="bg-slate-800 border-slate-400/30 text-white"
            data-testid="conviction-refresh-btn"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div
          className="p-3 rounded-lg bg-rose-900/30 border border-rose-500/40 text-rose-200 text-sm"
          data-testid="conviction-error"
        >
          {error}
        </div>
      )}

      {/* Loading placeholder */}
      {loading && !data && (
        <div className="text-slate-400 text-sm" data-testid="conviction-loading">
          Loading calibration data…
        </div>
      )}

      {/* Summary stats + conviction buckets */}
      {data && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div className="p-3 rounded-lg bg-slate-800/50 border border-slate-400/20">
              <div className="text-[10px] uppercase tracking-wide text-slate-400">
                Verified predictions
              </div>
              <div
                className="text-white text-lg font-semibold tabular-nums"
                data-testid="conviction-total-verified"
              >
                {data.total_verified.toLocaleString()}
              </div>
            </div>
            <div className="p-3 rounded-lg bg-slate-800/50 border border-slate-400/20">
              <div className="text-[10px] uppercase tracking-wide text-slate-400">
                With conviction tag
              </div>
              <div
                className="text-white text-lg font-semibold tabular-nums"
                data-testid="conviction-total-tagged"
              >
                {data.total_with_conviction.toLocaleString()}
              </div>
            </div>
            <div className="p-3 rounded-lg bg-slate-800/50 border border-slate-400/20 col-span-2 sm:col-span-1">
              <div className="text-[10px] uppercase tracking-wide text-slate-400">
                Window
              </div>
              <div className="text-white text-lg font-semibold tabular-nums">
                {data.window_days}d
              </div>
            </div>
          </div>

          {/* Conviction buckets */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
                By conviction score
              </h4>
              <MonotonicBadge
                status={data.monotonic?.conviction}
                label="conviction"
              />
            </div>
            {hasConvictionData ? (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {data.by_conviction.map((b) => (
                  <BucketRow
                    key={b.label}
                    bucket={b}
                    trend={data.trend?.by_conviction?.[b.label]}
                  />
                ))}
              </div>
            ) : (
              <div
                className="p-3 rounded-lg bg-slate-800/40 border border-slate-400/20 text-slate-400 text-sm"
                data-testid="conviction-empty-state"
              >
                No conviction-tagged predictions yet in this window. The
                calibration endpoint starts populating once the risk layer
                stamps <code className="text-slate-200">conviction</code> on
                verified predictions.
              </div>
            )}
          </div>

          {/* Confidence fallback buckets */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
                By raw confidence (fallback)
              </h4>
              <MonotonicBadge
                status={data.monotonic?.confidence}
                label="confidence"
              />
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {data.by_confidence.map((b) => (
                <BucketRow
                  key={b.label}
                  bucket={b}
                  trend={data.trend?.by_confidence?.[b.label]}
                />
              ))}
            </div>
          </div>

          <p className="text-[10px] text-slate-500">
            Generated {new Date(data.generated_at).toLocaleString()}
          </p>
        </>
      )}
    </div>
  );
};

export default ConvictionCalibration;
