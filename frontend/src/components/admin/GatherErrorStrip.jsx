/**
 * Gather Error Strip — compact admin card that surfaces the rolling
 * `log_error` counter exposed at `/api/admin/gather-error-rate`.
 *
 * Answers "which upstream provider is flaking right now?" without
 * requiring log-aggregator access. Each row shows one `context`
 * (e.g. `market_data.ticker`, `war_room`, `fred_fetch`) with its
 * count over the selected window, the top exception type, and a
 * compact heat-stripe whose width is proportional to that context's
 * share of total errors.
 *
 * Visual rhythm matches `MLHealthStrip.jsx` — same `#3DE8D9` accent,
 * same 10px uppercase label, same `border-slate-400/20` card chrome.
 * Tone flips to `warn` when any single context contributes ≥40% of
 * total errors in the window (a single provider dominating is the
 * exact signal this tile exists to catch).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api`;
const WINDOWS = [1, 6, 24];

// Any single context pushing this share of the window's errors flips
// the card tone to warn — matches the "one provider dominating" signal
// this tile was built for.
const DOMINATION_THRESHOLD = 0.4;

const GatherErrorStrip = () => {
  const [data, setData] = useState(null);
  const [hours, setHours] = useState(24);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(
        `${API}/admin/gather-error-rate?hours=${hours}`
      );
      if (res.ok) setData(await res.json());
    } catch (e) {
      logger.error('gather-error-rate load failed', e);
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => {
    load();
  }, [load]);

  const total = data?.total_errors ?? 0;
  const rows = data?.by_context ?? [];
  // Top row's share of the total; used for tone + the row-level width.
  const dominantShare = total > 0 && rows[0] ? rows[0].count / total : 0;
  const isDominated = dominantShare >= DOMINATION_THRESHOLD;
  const isHealthy = total === 0;

  const toneBorder = isHealthy
    ? 'border-emerald-400/30 bg-emerald-900/10'
    : isDominated
    ? 'border-amber-400/40 bg-amber-900/15'
    : 'border-slate-400/20 bg-slate-800/50';

  const StatusBadge = () => {
    if (isHealthy) {
      return (
        <span className="flex items-center gap-1 text-[10px] text-emerald-300">
          <CheckCircle2 className="w-3 h-3" />
          quiet
        </span>
      );
    }
    if (isDominated) {
      return (
        <span className="flex items-center gap-1 text-[10px] text-amber-300">
          <AlertTriangle className="w-3 h-3" />
          one provider dominating
        </span>
      );
    }
    return (
      <span className="flex items-center gap-1 text-[10px] text-slate-300">
        <Activity className="w-3 h-3" />
        mixed
      </span>
    );
  };

  return (
    <div
      className={`p-3 rounded-lg border ${toneBorder}`}
      data-testid="gather-error-strip"
    >
      {/* Header: icon + title + window selector + refresh */}
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Activity className="w-3.5 h-3.5 text-[#3DE8D9]" />
          <span className="text-[10px] uppercase tracking-wide text-slate-300">
            Gather-error rate · last {hours}h
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div
            className="flex items-center bg-slate-800 rounded-lg border border-slate-400/20 overflow-hidden"
            role="group"
          >
            {WINDOWS.map((h) => (
              <button
                key={h}
                onClick={() => setHours(h)}
                data-testid={`gather-error-window-${h}h-btn`}
                className={`px-2.5 py-1 text-[10px] font-medium transition-colors ${
                  hours === h
                    ? 'bg-[#3DE8D9]/20 text-[#3DE8D9]'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {h}h
              </button>
            ))}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={load}
            disabled={loading}
            data-testid="gather-error-refresh-btn"
            className="h-7 px-2 text-slate-400 hover:text-slate-200"
          >
            <RefreshCw
              className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`}
            />
          </Button>
        </div>
      </div>

      {/* Headline count + status */}
      <div className="flex items-baseline justify-between mb-3">
        <div
          className="text-xl font-bold text-white tabular-nums"
          data-testid="gather-error-total"
        >
          {total}
          <span className="text-sm text-slate-400 font-normal"> errors</span>
          {data?.distinct_contexts > 0 && (
            <span className="text-[10px] text-slate-500 font-normal ml-2">
              across {data.distinct_contexts} context
              {data.distinct_contexts === 1 ? '' : 's'}
            </span>
          )}
        </div>
        <StatusBadge />
      </div>

      {/* Per-context rows with proportional heat stripes */}
      {data == null ? (
        <div className="text-xs text-slate-400">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-[10px] text-slate-500 italic">
          no errors recorded in this window — restart clears the buffer
        </div>
      ) : (
        <ul
          className="space-y-1.5"
          data-testid="gather-error-context-list"
        >
          {rows.slice(0, 8).map((row) => {
            const share = total > 0 ? row.count / total : 0;
            const widthPct = Math.max(4, Math.round(share * 100));
            const topType = row.top_types?.[0];
            // Stripe colour: the top context uses accent, everything
            // else stays slate. Keeps the "who's the worst offender"
            // read obvious without a 6-colour legend.
            const isTop = row === rows[0];
            const stripeColor =
              isTop && isDominated
                ? 'bg-amber-400'
                : isTop
                ? 'bg-[#3DE8D9]'
                : 'bg-slate-500';
            return (
              <li
                key={row.context}
                className="text-[11px]"
                data-testid={`gather-error-row-${row.context}`}
              >
                <div className="flex items-center justify-between gap-2 mb-0.5">
                  <span className="text-slate-200 font-mono truncate">
                    {row.context}
                  </span>
                  <span className="text-slate-400 tabular-nums shrink-0">
                    {row.count}
                    {topType && (
                      <span className="text-slate-600 ml-1.5">
                        · {topType.type}
                      </span>
                    )}
                  </span>
                </div>
                <div className="h-1 bg-slate-900/60 rounded-full overflow-hidden">
                  <div
                    className={`h-full ${stripeColor} transition-all`}
                    style={{ width: `${widthPct}%` }}
                  />
                </div>
              </li>
            );
          })}
          {rows.length > 8 && (
            <li className="text-[10px] text-slate-500 italic pt-1">
              +{rows.length - 8} more context
              {rows.length - 8 === 1 ? '' : 's'} hidden
            </li>
          )}
        </ul>
      )}
    </div>
  );
};

export default GatherErrorStrip;
