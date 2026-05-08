import React, { useEffect, useState, useCallback } from 'react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { TrendingUp, Bitcoin, Shield, AlertTriangle, CheckCircle2, Clock, RefreshCw } from 'lucide-react';
import { Button } from '../ui/button';

const API = `${getApiBase()}/api`;

// Phase pill colour map.
const PHASE_STYLES = {
  Shadow:    { bg: 'bg-slate-800',  border: 'border-slate-600',  text: 'text-slate-200', dot: 'bg-slate-400' },
  Calibrate: { bg: 'bg-amber-950',  border: 'border-amber-700',  text: 'text-amber-200', dot: 'bg-amber-400' },
  Enforce:   { bg: 'bg-emerald-950', border: 'border-emerald-700', text: 'text-emerald-200', dot: 'bg-emerald-400' },
};

const STATE_STYLES = {
  'Eligible':         { bg: 'bg-emerald-950', text: 'text-emerald-200', border: 'border-emerald-700' },
  'Ready for Review': { bg: 'bg-amber-950',  text: 'text-amber-200',  border: 'border-amber-700' },
  'Blocked':          { bg: 'bg-rose-950',   text: 'text-rose-200',   border: 'border-rose-800' },
};

const STATUS_ICON = {
  pass: <CheckCircle2 className="w-4 h-4 text-emerald-400" />,
  fail: <AlertTriangle className="w-4 h-4 text-rose-400" />,
  'n/a': <Clock className="w-4 h-4 text-slate-400" />,
};

const LANE_ICON = {
  equity: TrendingUp,
  crypto: Bitcoin,
};

const formatNumber = (v) => {
  if (typeof v !== 'number') return String(v);
  return v.toLocaleString();
};

const ChecklistRow = ({ item }) => (
  <div
    data-testid={`kanban-check-${item.key}`}
    className="flex items-start gap-2 py-2 px-3 rounded-md bg-slate-900/50 border border-slate-800/60"
  >
    <span className="mt-0.5">{STATUS_ICON[item.status] || STATUS_ICON['n/a']}</span>
    <div className="flex-1 min-w-0">
      <div className="text-sm text-slate-200 leading-tight">{item.label}</div>
      <div className="text-[11px] text-slate-500 mt-0.5">
        value: <span className="font-mono text-slate-400">{formatNumber(item.value)}</span>
        {item.threshold !== null && item.threshold !== undefined ? (
          <>
            {' '}· target: <span className="font-mono text-slate-400">{formatNumber(item.threshold)}</span>
          </>
        ) : null}
        {item.note ? <span className="ml-2 text-amber-400">· {item.note}</span> : null}
      </div>
    </div>
  </div>
);

const Metric = ({ label, value, testid }) => (
  <div data-testid={testid} className="flex flex-col">
    <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
    <span className="text-base font-mono text-slate-100">{formatNumber(value ?? 0)}</span>
  </div>
);

const LaneCard = ({ card }) => {
  if (!card) return null;
  const lane = card.lane;
  const Icon = LANE_ICON[lane] || Shield;
  const phaseStyle = PHASE_STYLES[card.phase] || PHASE_STYLES.Shadow;
  const stateStyle = STATE_STYLES[card.promotion_state] || STATE_STYLES.Blocked;
  const m = card.metrics || {};
  const r = m.receipts || {};
  const rg = m.roadguard || {};

  return (
    <div
      data-testid={`kanban-card-${lane}`}
      className="bg-slate-950 border border-slate-800 rounded-xl overflow-hidden flex flex-col"
    >
      <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-center">
            <Icon className="w-5 h-5 text-slate-300" />
          </div>
          <div>
            <div className="text-base font-semibold text-slate-100 capitalize" data-testid={`kanban-lane-${lane}`}>
              {lane}
            </div>
            <div className="text-[11px] text-slate-500 font-mono">
              {card.rg_collection}
            </div>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <div
            className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] uppercase tracking-wide border ${phaseStyle.bg} ${phaseStyle.border} ${phaseStyle.text}`}
            data-testid={`kanban-phase-${lane}`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${phaseStyle.dot}`} />
            {card.phase}
          </div>
          <div
            className={`px-2.5 py-0.5 rounded-md text-[11px] font-medium border ${stateStyle.bg} ${stateStyle.text} ${stateStyle.border}`}
            data-testid={`kanban-state-${lane}`}
          >
            {card.promotion_state}
          </div>
        </div>
      </div>

      <div className="p-5 grid grid-cols-3 gap-4 border-b border-slate-800/60">
        <Metric label="Receipts" value={r.total} testid={`kanban-${lane}-receipts-total`} />
        <Metric label="Approved" value={r.approved} testid={`kanban-${lane}-receipts-approved`} />
        <Metric label="No-Trade" value={r.no_trade} testid={`kanban-${lane}-receipts-no-trade`} />
        <Metric label="RG Total" value={rg.total} testid={`kanban-${lane}-rg-total`} />
        <Metric label="RG Pass" value={rg.by_decision?.PASS} testid={`kanban-${lane}-rg-pass`} />
        <Metric label="RG Reduce" value={rg.by_decision?.REDUCE} testid={`kanban-${lane}-rg-reduce`} />
        <Metric label="RG Block" value={rg.by_decision?.BLOCK} testid={`kanban-${lane}-rg-block`} />
        <Metric label="Lane Mismatch" value={rg.lane_mismatch} testid={`kanban-${lane}-lane-mismatch`} />
        <Metric label="Broker-Health Blocks" value={rg.broker_health_blocks} testid={`kanban-${lane}-broker-blocks`} />
        <Metric label="Exposure-Cap Blocks" value={rg.exposure_cap_blocks} testid={`kanban-${lane}-exposure-blocks`} />
        <Metric label="Dup-Symbol Blocks" value={rg.duplicate_symbol_blocks} testid={`kanban-${lane}-dup-blocks`} />
        <Metric label="False Blocks" value={r.false_blocks} testid={`kanban-${lane}-false-blocks`} />
      </div>

      <div className="p-5 flex-1">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs uppercase tracking-wider text-slate-500">Promotion checklist</span>
          <span className="text-[10px] text-slate-600 font-mono">
            sample window: {card.sample_window?.days}d
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          {(card.checklist || []).map((item) => (
            <ChecklistRow key={item.key} item={item} />
          ))}
        </div>
      </div>

      <div className="px-5 py-3 border-t border-slate-800 bg-slate-900/40">
        <Button
          disabled
          aria-disabled="true"
          data-testid={`kanban-promote-btn-${lane}`}
          title="Read-only — no enforcement changes from this tile"
          className={`w-full justify-center ${stateStyle.bg} ${stateStyle.text} ${stateStyle.border} border opacity-100 hover:opacity-100 cursor-not-allowed`}
        >
          {card.promotion_state}
        </Button>
        <p className="text-[10px] text-slate-500 mt-2 text-center">
          Read-only · this button does not flip enforcement
        </p>
      </div>
    </div>
  );
};

const CalibrationKanban = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await authFetch(`${API}/admin/ml/v2/calibration/kanban`);
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
      }
      const payload = await resp.json();
      setData(payload);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-4" data-testid="calibration-kanban-tile">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h3 className="text-lg font-semibold text-slate-100">Calibration Kanban</h3>
          <p className="text-sm text-slate-400 mt-1 max-w-2xl">
            Per-lane promotion-readiness journey: <span className="text-slate-300">Shadow → Calibrate → Enforce</span>.
            Read-only tile — buttons display state but never flip enforcement, never call a broker.
          </p>
        </div>
        <Button
          onClick={load}
          variant="outline"
          size="sm"
          disabled={loading}
          data-testid="kanban-refresh-btn"
          className="border-slate-700 text-slate-300 hover:bg-slate-800"
        >
          <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {error ? (
        <div
          className="bg-rose-950/40 border border-rose-900 text-rose-200 rounded-lg p-4 text-sm"
          data-testid="kanban-error"
        >
          Failed to load kanban: {error}
        </div>
      ) : null}

      {loading && !data ? (
        <div className="text-sm text-slate-400 px-4 py-8 text-center" data-testid="kanban-loading">
          Loading…
        </div>
      ) : null}

      {data ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4" data-testid="kanban-grid">
          <LaneCard card={data.lanes?.equity} />
          <LaneCard card={data.lanes?.crypto} />
        </div>
      ) : null}

      {data ? (
        <div className="text-[10px] text-slate-600 font-mono text-right" data-testid="kanban-generated-at">
          Generated: {data.generated_at} · Threshold: {data.promotion_thresholds?.min_receipts_per_lane}+ receipts
        </div>
      ) : null}
    </div>
  );
};

export default CalibrationKanban;
