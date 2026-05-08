/**
 * MLPromotionChecklistTile — read-only Phase 6 readiness aggregator.
 *
 * GET /api/admin/ml/promotion-checklist → traffic-light list.
 *
 * Hard contract:
 *   * NO promote button
 *   * NO set-active button
 *   * NO env editing
 *   * MAY say "Ready for review"
 *   * MUST NOT say "Promote now"
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import {
  ListChecks, RefreshCw, CheckCircle2, AlertTriangle, AlertCircle,
} from 'lucide-react';

const API = `${getApiBase()}/api`;
const REFRESH_INTERVAL_MS = 60_000;

const STATUS_STYLES = {
  GREEN: {
    icon: CheckCircle2,
    iconClass: 'text-emerald-400',
    rowClass: 'border-emerald-900/50 bg-emerald-950/20',
    pillClass: 'bg-emerald-950/60 border-emerald-800 text-emerald-200',
  },
  YELLOW: {
    icon: AlertTriangle,
    iconClass: 'text-amber-400',
    rowClass: 'border-amber-900/50 bg-amber-950/20',
    pillClass: 'bg-amber-950/60 border-amber-800 text-amber-200',
  },
  RED: {
    icon: AlertCircle,
    iconClass: 'text-rose-400',
    rowClass: 'border-rose-900/50 bg-rose-950/20',
    pillClass: 'bg-rose-950/60 border-rose-800 text-rose-200',
  },
};

const styleFor = (s) => STATUS_STYLES[s] || STATUS_STYLES.YELLOW;

const ChecklistRow = ({ check }) => {
  const style = styleFor(check.status);
  const Icon = style.icon;
  return (
    <li
      data-testid={`checklist-row-${check.id}`}
      data-status={check.status}
      className={`flex items-start gap-3 px-3 py-2.5 rounded-md border ${style.rowClass}`}
    >
      <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${style.iconClass}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            data-testid={`checklist-label-${check.id}`}
            className="text-sm text-slate-100"
          >
            {check.label}
          </span>
          <span
            data-testid={`checklist-status-${check.id}`}
            className={`px-1.5 py-0.5 rounded-full text-[9px] uppercase tracking-wider border ${style.pillClass}`}
          >
            {check.status}
          </span>
        </div>
        <div
          data-testid={`checklist-detail-${check.id}`}
          className="text-[11px] text-slate-400 font-mono mt-1 break-words"
        >
          {check.detail}
        </div>
      </div>
    </li>
  );
};

const SummaryBanner = ({ data }) => {
  if (!data) return null;
  const style = styleFor(data.overall_status);
  const Icon = style.icon;
  return (
    <div
      data-testid={`checklist-banner-${data.overall_status}`}
      className={`flex items-center gap-2.5 px-3 py-2 rounded-md border ${style.rowClass}`}
    >
      <Icon className={`w-4 h-4 ${style.iconClass}`} />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold text-slate-100">
          {data.message}
        </div>
        <div className="text-[10px] text-slate-500 font-mono">
          overall = {data.overall_status} · ready_for_review = {String(data.ready_for_review)}
        </div>
      </div>
    </div>
  );
};

const MLPromotionChecklistTile = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const intervalRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const resp = await authFetch(`${API}/admin/ml/promotion-checklist`);
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 160)}`);
      }
      setData(await resp.json());
      setError(null);
      setLastRefreshed(new Date());
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    intervalRef.current = setInterval(load, REFRESH_INTERVAL_MS);
    return () => intervalRef.current && clearInterval(intervalRef.current);
  }, [load]);

  return (
    <div
      className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden"
      data-testid="ml-promotion-checklist-tile"
    >
      <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-md bg-slate-900 border border-slate-800 flex items-center justify-center">
            <ListChecks
              className={`w-4 h-4 ${
                data?.overall_status === 'RED'
                  ? 'text-rose-400'
                  : data?.overall_status === 'YELLOW'
                    ? 'text-amber-400'
                    : 'text-emerald-400'
              }`}
            />
          </div>
          <div>
            <h3 className="text-base font-semibold text-slate-100 leading-tight">
              Promotion Checklist
            </h3>
            <p className="text-[11px] text-slate-500 leading-tight mt-0.5">
              Read-only · GET /api/admin/ml/promotion-checklist · refresh 60s
            </p>
          </div>
        </div>
        <button
          onClick={load}
          data-testid="checklist-refresh-btn"
          className="p-1.5 rounded-md bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 transition-colors"
          title="Refresh now"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="p-5 space-y-3">
        {error && (
          <div
            className="bg-rose-950/40 border border-rose-900 text-rose-200 rounded-md p-3 text-xs"
            data-testid="checklist-error"
          >
            Failed to load checklist: {error}
          </div>
        )}

        {loading && !data && (
          <div className="text-sm text-slate-400 px-4 py-6 text-center" data-testid="checklist-loading">
            Loading…
          </div>
        )}

        {data && (
          <>
            <SummaryBanner data={data} />

            <ul className="space-y-2" data-testid="checklist-items">
              {data.checks.map((c) => (
                <ChecklistRow key={c.id} check={c} />
              ))}
            </ul>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2 border-t border-slate-800/60">
              <div className="text-[10px] text-slate-600 font-mono space-y-0.5">
                <div className="text-slate-500 uppercase tracking-wider">Active artifacts</div>
                <div data-testid="checklist-active-strategist">
                  strategist: {data.active_artifacts?.strategist || '—'}
                </div>
                <div data-testid="checklist-active-auditor">
                  auditor: {data.active_artifacts?.auditor || '—'}
                </div>
              </div>
              <div className="text-[10px] text-slate-600 font-mono space-y-0.5">
                <div className="text-slate-500 uppercase tracking-wider">Latest artifacts</div>
                <div data-testid="checklist-latest-strategist">
                  strategist: {data.latest_artifacts?.strategist || '—'}
                </div>
                <div data-testid="checklist-latest-auditor">
                  auditor: {data.latest_artifacts?.auditor || '—'}
                </div>
              </div>
            </div>

            <div className="flex items-center justify-between text-[10px] text-slate-600 font-mono">
              <span data-testid="checklist-as-of">as_of: {data.as_of}</span>
              <span>{lastRefreshed ? `refreshed ${lastRefreshed.toLocaleTimeString()}` : ''}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default MLPromotionChecklistTile;
