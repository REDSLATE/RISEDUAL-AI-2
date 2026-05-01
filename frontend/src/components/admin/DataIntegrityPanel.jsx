import React, { useState, useEffect, useCallback } from 'react';
import { Shield, CheckCircle2, AlertTriangle, RefreshCw, History, Activity } from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api`;

/**
 * Compact inline SVG sparkline — no external dep. 14 daily buckets
 * rendered as a scaled polyline with min/max baseline. Designed to
 * fit inside a Stat card without chrome.
 */
const Sparkline = ({ points = [], tone = 'default', testid }) => {
  if (!points || points.length === 0) return null;
  const w = 110;
  const h = 22;
  const pad = 1.5;
  const max = Math.max(1, ...points);
  const step = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
  const coords = points
    .map((v, i) => {
      const x = pad + i * step;
      const y = h - pad - ((v / max) * (h - pad * 2));
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const color =
    tone === 'good' ? '#34d399' :
    tone === 'warn' ? '#fbbf24' :
    tone === 'bad' ? '#f87171' :
    '#94a3b8';
  const lastVal = points[points.length - 1];
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="mt-1"
      data-testid={testid}
    >
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={coords}
      />
      {/* Emphasise the final point — this is today's reading. */}
      <circle
        cx={pad + (points.length - 1) * step}
        cy={h - pad - ((lastVal / max) * (h - pad * 2))}
        r="1.8"
        fill={color}
      />
    </svg>
  );
};

/**
 * DataIntegrityPanel — operator-visible surface for the 2026-05-01
 * direction-token cleanup and its ongoing tripwires.
 *
 * Surfaces:
 *   - Unknown-direction-token count (24h / 7d) + 14-day sparkline.
 *   - Toxic lessons created (7d) vs. superseded (ever).
 *   - Grade backfills + LE trade repairs counts + 14-day sparkline.
 *   - Brute-force lockouts + top offenders + 14-day sparkline.
 *   - Latest nightly audit result (check-by-check pass/fail).
 *   - "Run audit now" button for manual verification after a cleanup.
 */
const DataIntegrityPanel = () => {
  const [data, setData] = useState(null);
  const [series, setSeries] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [summary, ts] = await Promise.all([
        authFetch(`${API}/admin/data-integrity/summary`).then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))),
        authFetch(`${API}/admin/data-integrity/timeseries?days=14`)
          .then((r) => r.ok ? r.json() : null) // timeseries is nice-to-have
          .catch(() => null),
      ]);
      setData(summary);
      setSeries(ts);
    } catch (e) {
      logger.error('DataIntegrityPanel load error:', e);
      setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const runAudit = async () => {
    setRunning(true);
    try {
      const res = await authFetch(`${API}/admin/data-integrity/run-audit`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch (e) {
      logger.error('runAudit error:', e);
      setError(e.message || 'Audit failed');
    } finally {
      setRunning(false);
    }
  };

  if (loading) {
    return (
      <Card className="bg-slate-800/80 border-slate-700/50 p-5" data-testid="data-integrity-panel">
        <div className="flex items-center gap-2 text-slate-300 text-sm">
          <RefreshCw className="w-4 h-4 animate-spin" /> Loading data-integrity snapshot…
        </div>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card className="bg-slate-800/80 border-orange-500/40 p-5" data-testid="data-integrity-panel">
        <div className="flex items-center gap-2 text-orange-300 text-sm">
          <AlertTriangle className="w-4 h-4" /> Data-integrity snapshot unavailable
          {error && <span className="text-slate-400 ml-2">· {error}</span>}
        </div>
        <Button onClick={load} className="mt-3 h-8 text-xs" data-testid="retry-data-integrity-btn">
          Retry
        </Button>
      </Card>
    );
  }

  const {
    unknown_direction_tokens: udt = {},
    backfills = {},
    toxic_lessons = {},
    brute_force = {},
    latest_nightly_audit: audit,
  } = data;

  const udt24h = udt.last_24h ?? 0;
  const udt7d = udt.last_7d ?? 0;
  const overallOk = udt24h === 0 && (audit?.overall_passed ?? true);

  const Stat = ({ label, value, tone = 'default', testid, spark, sparkTone }) => {
    const toneCls =
      tone === 'good' ? 'text-emerald-300' :
      tone === 'warn' ? 'text-amber-300' :
      tone === 'bad' ? 'text-red-300' :
      'text-slate-100';
    return (
      <div className="bg-slate-900/50 rounded-lg px-3 py-2 border border-slate-700/40" data-testid={testid}>
        <div className="text-[10px] uppercase tracking-wider text-slate-400">{label}</div>
        <div className={`text-lg font-bold tabular-nums ${toneCls}`}>{value}</div>
        {spark && spark.length > 0 && (
          <Sparkline points={spark} tone={sparkTone ?? tone} testid={`${testid}-spark`} />
        )}
      </div>
    );
  };

  return (
    <Card className="bg-slate-800/80 border-slate-700/50 p-5 space-y-4" data-testid="data-integrity-panel">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <div className={`w-9 h-9 rounded-lg border flex items-center justify-center ${
            overallOk
              ? 'bg-emerald-500/10 border-emerald-500/40'
              : 'bg-orange-500/10 border-orange-500/40'
          }`}>
            {overallOk
              ? <Shield className="w-5 h-5 text-emerald-300" />
              : <AlertTriangle className="w-5 h-5 text-orange-300" />}
          </div>
          <div>
            <h3 className="text-white text-base font-bold">Data Integrity</h3>
            <p className="text-[11px] text-slate-400">
              Direction-token tripwire · nightly audit · backfill timeline
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {overallOk ? (
            <Badge className="bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-[10px]">
              <CheckCircle2 className="w-3 h-3 mr-1" /> All invariants passing
            </Badge>
          ) : (
            <Badge className="bg-orange-500/15 text-orange-300 border border-orange-500/30 text-[10px]">
              <AlertTriangle className="w-3 h-3 mr-1" /> Action needed
            </Badge>
          )}
          <Button
            size="sm"
            onClick={runAudit}
            disabled={running}
            className="h-8 text-xs bg-slate-700 hover:bg-slate-600 text-white"
            data-testid="run-integrity-audit-btn"
          >
            {running ? <><RefreshCw className="w-3 h-3 mr-1 animate-spin" /> Running…</> : <><Activity className="w-3 h-3 mr-1" /> Run audit now</>}
          </Button>
        </div>
      </div>

      {/* Core metric grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat
          label="Unknown tokens · 24h"
          value={udt24h}
          tone={udt24h === 0 ? 'good' : 'bad'}
          spark={series?.unknown_direction_tokens}
          sparkTone={udt24h === 0 && (series?.unknown_direction_tokens || []).every((n) => n === 0) ? 'good' : 'bad'}
          testid="udt-24h"
        />
        <Stat
          label="Unknown tokens · 7d"
          value={udt7d}
          tone={udt7d === 0 ? 'good' : 'warn'}
          testid="udt-7d"
        />
        <Stat
          label="Grade backfills · 7d"
          value={backfills.grade_last_7d ?? 0}
          spark={series?.grade_backfills}
          testid="backfill-7d"
        />
        <Stat
          label="LE repairs · 7d"
          value={backfills.le_trade_last_7d ?? 0}
          testid="le-repairs-7d"
        />
        <Stat
          label="Toxic lessons · 7d"
          value={toxic_lessons.created_last_7d ?? '—'}
          testid="toxic-created-7d"
        />
        <Stat
          label="Superseded · total"
          value={toxic_lessons.superseded_total ?? '—'}
          testid="toxic-superseded"
        />
        <Stat
          label="Lockouts · 24h"
          value={brute_force.lockouts_last_24h ?? 0}
          tone={(brute_force.lockouts_last_24h ?? 0) > 0 ? 'warn' : 'default'}
          spark={series?.brute_force_lockouts}
          sparkTone={(brute_force.lockouts_last_7d ?? 0) > 0 ? 'warn' : 'default'}
          testid="bf-24h"
        />
        <Stat
          label="Lockouts · 7d"
          value={brute_force.lockouts_last_7d ?? 0}
          tone={(brute_force.lockouts_last_7d ?? 0) > 0 ? 'warn' : 'default'}
          testid="bf-7d"
        />
      </div>

      {/* Unknown-direction contexts — only show when there are any */}
      {udt.top_contexts_7d?.length > 0 && (
        <div className="bg-orange-500/5 border border-orange-500/30 rounded-lg p-3">
          <div className="text-xs font-semibold text-orange-300 mb-2">
            Unknown-direction token emitters (7d)
          </div>
          <div className="flex flex-wrap gap-2" data-testid="udt-contexts-list">
            {udt.top_contexts_7d.map((c) => (
              <Badge
                key={c.context}
                className="bg-orange-500/15 text-orange-200 border border-orange-500/30 text-[10px] font-mono"
              >
                {c.context} · {c.count}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Brute-force offenders */}
      {brute_force.top_offenders_7d?.length > 0 && (
        <div className="bg-slate-900/40 border border-slate-700/40 rounded-lg p-3">
          <div className="text-xs font-semibold text-slate-200 mb-2">
            Lockout offenders (7d)
          </div>
          <div className="space-y-1" data-testid="bf-offenders-list">
            {brute_force.top_offenders_7d.map((o) => (
              <div
                key={o.identifier}
                className="flex justify-between items-center text-[11px] text-slate-300 font-mono"
              >
                <span className="truncate">{o.identifier}</span>
                <span className="tabular-nums text-amber-300 ml-2">{o.count}×</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Latest audit breakdown */}
      {audit && (
        <div className="bg-slate-900/40 border border-slate-700/40 rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
              <History className="w-3.5 h-3.5" />
              Latest nightly audit
            </div>
            <span className="text-[10px] text-slate-500 font-mono">
              {new Date(audit.run_id).toLocaleString()}
            </span>
          </div>
          <div className="space-y-1" data-testid="audit-checks-list">
            {(audit.checks || []).map((c) => (
              <div
                key={c.name}
                className="flex items-center justify-between gap-2 text-[11px]"
              >
                <span className="font-mono text-slate-300 truncate">{c.name}</span>
                <span className="flex items-center gap-1 shrink-0">
                  {c.violation_count > 0 && (
                    <span className="text-amber-300 tabular-nums">
                      {c.violation_count} violation{c.violation_count === 1 ? '' : 's'}
                    </span>
                  )}
                  {c.passed ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  ) : (
                    <AlertTriangle className="w-3.5 h-3.5 text-orange-400" />
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
};

export default DataIntegrityPanel;
