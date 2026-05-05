import React, { useEffect, useState, useCallback } from 'react';
import { DollarSign, TrendingDown } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Slippage Attribution · realised spread cost vs strategy P&L.
 *
 * Polls /api/admin/slippage-attribution every 60s. Renders a
 * compact summary of how much $ the bot has paid in spread cost
 * over the last N days, broken down by lane and fill-method.
 *
 * Operator's mental model:
 *   "What did the strategy earn vs what the spread took?"
 *
 * Drag % = total slippage drag / |total P&L|. Useful for spotting
 * regimes where the strategy is barely beating the spread.
 */
const SlippageAttributionPanel = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const fetchSummary = useCallback(async () => {
    try {
      const r = await authFetch(`${API}/admin/slippage-attribution?lookback_days=30`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError(null);
    } catch (e) {
      setError(e.message || 'fetch failed');
    }
  }, []);

  useEffect(() => {
    fetchSummary();
    const id = setInterval(fetchSummary, 60_000);
    return () => clearInterval(id);
  }, [fetchSummary]);

  if (error) {
    return (
      <div
        className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-300"
        data-testid="slippage-attribution-error"
      >
        Slippage attribution error: {error}
      </div>
    );
  }
  if (!data) return null;

  const { totals = {}, by_lane = {}, by_method = {}, lookback_days } = data;
  const dragUsd = totals.total_slippage_drag_usd || 0;
  const pnlUsd = totals.total_pnl_usd || 0;
  const dragPct = totals.drag_pct_of_abs_pnl || 0;
  const tradesStamped = totals.trades_with_slippage_stamp || 0;
  const totalTrades = totals.trades || 0;

  // Color the drag pill based on severity:
  //   < 5%   = green (cheap entry, strategy dominates)
  //   5-15%  = amber (noticeable, watch)
  //   > 15%  = rose  (spread is eating returns)
  let dragColor = 'text-emerald-300';
  let dragBorder = 'border-emerald-500/40';
  let dragBg = 'bg-emerald-500/5';
  if (dragPct >= 15) {
    dragColor = 'text-rose-300';
    dragBorder = 'border-rose-500/40';
    dragBg = 'bg-rose-500/5';
  } else if (dragPct >= 5) {
    dragColor = 'text-amber-300';
    dragBorder = 'border-amber-500/40';
    dragBg = 'bg-amber-500/5';
  }

  return (
    <div
      data-testid="slippage-attribution-tile"
      className={`rounded-xl border ${dragBorder} ${dragBg} p-3`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wider">
        <div className={`flex items-center gap-2 ${dragColor}`}>
          <DollarSign className="h-3.5 w-3.5" /> Slippage Attribution
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="slippage-attribution-drag-pill"
            className={`flex items-center gap-1 rounded-full border ${dragBorder} bg-slate-900/40 px-2 py-0.5 text-[10px] font-semibold normal-case ${dragColor}`}
          >
            <TrendingDown className="h-3 w-3" />
            {dragPct.toFixed(1)}% drag · ${dragUsd.toFixed(2)} cost
          </span>
          <span className="text-slate-500 text-[10px]">
            · last {lookback_days}d
          </span>
        </div>
      </div>

      <div className="mb-2 grid grid-cols-2 gap-3 md:grid-cols-4">
        <div data-testid="slippage-attribution-total-pnl">
          <div className={`text-2xl font-semibold tabular-nums ${pnlUsd >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
            ${pnlUsd.toFixed(2)}
          </div>
          <div className="text-xs text-slate-500">strategy P&L</div>
        </div>
        <div data-testid="slippage-attribution-total-drag">
          <div className="text-2xl font-semibold tabular-nums text-slate-200">
            ${dragUsd.toFixed(2)}
          </div>
          <div className="text-xs text-slate-500">spread paid (entry+exit)</div>
        </div>
        <div data-testid="slippage-attribution-avg-bps">
          <div className="text-2xl font-semibold tabular-nums text-slate-200">
            {(totals.avg_bps_per_trade || 0).toFixed(1)}
          </div>
          <div className="text-xs text-slate-500">avg bps / trade</div>
        </div>
        <div data-testid="slippage-attribution-trades">
          <div className="text-2xl font-semibold tabular-nums text-slate-200">
            {tradesStamped} / {totalTrades}
          </div>
          <div className="text-xs text-slate-500">stamped / closed</div>
        </div>
      </div>

      {Object.keys(by_lane).length > 0 && (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div>
            <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
              By lane
            </div>
            <div className="space-y-1">
              {Object.entries(by_lane).map(([lane, v]) => (
                <div
                  key={lane}
                  data-testid={`slippage-attribution-lane-${lane}`}
                  className="flex items-center justify-between rounded-md border border-slate-800/60 bg-slate-900/30 px-2 py-1 text-xs"
                >
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${lane === 'crypto' ? 'bg-cyan-500/10 text-cyan-300' : 'bg-emerald-500/10 text-emerald-300'}`}>
                    {lane}
                  </span>
                  <span className="text-slate-400">
                    {v.count} trades · ${v.dollar_cost.toFixed(2)} drag · {v.avg_bps.toFixed(1)} bps
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
              By fill method
            </div>
            <div className="space-y-1">
              {Object.entries(by_method).map(([method, v]) => (
                <div
                  key={method}
                  data-testid={`slippage-attribution-method-${method}`}
                  className="flex items-center justify-between rounded-md border border-slate-800/60 bg-slate-900/30 px-2 py-1 text-xs"
                >
                  <span className="font-mono text-[10px] text-slate-300">
                    {method}
                  </span>
                  <span className="text-slate-400">
                    {v.count} · ${v.dollar_cost.toFixed(2)} · {v.avg_bps.toFixed(1)} bps
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SlippageAttributionPanel;
