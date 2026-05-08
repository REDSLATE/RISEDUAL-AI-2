import React, { useState, useEffect, useCallback } from 'react';
import { Trophy, RefreshCw, TrendingUp, TrendingDown, Loader2 } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api/admin/strategies/leaderboard`;

/**
 * StrategyLeaderboardPanel — rolls up every autonomous agent's PnL
 * so the owner can see which strategy is actually printing money
 * vs. which one is just printing trades. Sorts by total_pnl desc;
 * pending-only strategies fall to the bottom by design (no PnL
 * yet = no leadership claim).
 *
 * Owner-only endpoint — this tile is gated by the admin section.
 */
const StrategyLeaderboardPanel = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(30);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(`${API}?days=${days}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      logger.error('[strategy-leaderboard] fetch error', e);
      setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  const items = data?.items || [];
  const resolvedTotal = data?.resolved_total ?? 0;
  const pendingTotal = data?.pending_total ?? 0;

  // Rank: show #1 trophy only when at least one row has resolved data
  // AND there's a meaningful leader (non-zero PnL or non-zero avg_r).
  const leaderIndex = (() => {
    if (items.length === 0 || resolvedTotal === 0) return -1;
    const leader = items.find((i) => i.total_pnl > 0 || (i.avg_r != null && i.avg_r > 0));
    return leader ? items.indexOf(leader) : -1;
  })();

  return (
    <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-5" data-testid="strategy-leaderboard-card">
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-xl bg-[#3DE8D9]/10 border border-[#3DE8D9]/20 flex items-center justify-center shrink-0">
          <Trophy className="w-6 h-6 text-[#3DE8D9]" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            <h4 className="text-white text-sm font-semibold">Which strategy is best?</h4>
            <div className="flex items-center gap-2">
              <select
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                className="bg-slate-900/60 border border-slate-700/60 rounded-md text-[10px] text-slate-200 px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#3DE8D9]/50"
                data-testid="strategy-leaderboard-window"
              >
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
                <option value={365}>1 year</option>
              </select>
              <Button
                variant="ghost"
                size="sm"
                onClick={load}
                disabled={loading}
                className="h-7 px-2 text-slate-300 hover:text-white hover:bg-slate-700/60"
                data-testid="strategy-leaderboard-refresh"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </Button>
            </div>
          </div>
          <p className="text-slate-300 text-xs leading-relaxed mb-3">
            Autonomous-agent PnL roll-up by strategy. {resolvedTotal} resolved · {pendingTotal} pending in the last {data?.window_days || days} days. Sorted by realized PnL.
          </p>

          {error && (
            <div className="mb-3 p-2 rounded-lg bg-rose-900/30 border border-rose-700/40 text-rose-200 text-xs">{error}</div>
          )}

          {loading && !data && (
            <div className="flex items-center gap-2 text-slate-400 text-xs">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading…
            </div>
          )}

          {!loading && items.length === 0 && !error && (
            <div className="p-4 rounded-lg bg-slate-900/40 border border-slate-700/40 text-slate-400 text-xs">
              No strategy activity in this window yet.
            </div>
          )}

          {items.length > 0 && resolvedTotal === 0 && (
            <div className="mb-3 p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs" data-testid="strategy-leaderboard-warming">
              <strong className="text-amber-100">Data warming up:</strong>{' '}
              all {pendingTotal} trades in this window are still pending resolution. Leaderboard ranking activates once trades resolve.
            </div>
          )}

          <div className="space-y-1.5" data-testid="strategy-leaderboard-rows">
            {items.map((row, idx) => {
              const isLeader = idx === leaderIndex;
              const winRatePct = row.win_rate != null ? (row.win_rate * 100) : null;
              const avgR = row.avg_r;
              return (
                <div
                  key={row.strategy}
                  className={`rounded-lg border px-3 py-2.5 transition-colors ${
                    isLeader
                      ? 'bg-gradient-to-r from-[#3DE8D9]/10 to-transparent border-[#3DE8D9]/40'
                      : 'bg-slate-900/40 border-slate-700/40'
                  }`}
                  data-testid={`strategy-leaderboard-row-${row.strategy}`}
                >
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className={`text-[10px] font-bold tabular-nums ${isLeader ? 'text-[#3DE8D9]' : 'text-slate-500'}`}>
                      {isLeader ? '🏆 #1' : `#${idx + 1}`}
                    </span>
                    <span className="text-xs font-mono font-semibold text-white">{row.strategy}</span>
                    <Badge className="bg-slate-700/60 text-slate-200 border-slate-600/40 text-[10px]">
                      {row.trades} trade{row.trades === 1 ? '' : 's'}
                    </Badge>
                    {row.pending > 0 && (
                      <Badge className="bg-amber-500/10 text-amber-300 border-amber-500/30 text-[10px]">
                        {row.pending} pending
                      </Badge>
                    )}
                    {winRatePct != null && (
                      <span className={`text-[10px] font-semibold tabular-nums ${winRatePct >= 50 ? 'text-emerald-300' : 'text-rose-300'}`}>
                        {winRatePct.toFixed(0)}% win rate
                      </span>
                    )}
                    {avgR != null && (
                      <span className={`inline-flex items-center gap-0.5 text-[10px] font-semibold tabular-nums ${avgR >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
                        {avgR >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                        {avgR >= 0 ? '+' : ''}{avgR.toFixed(2)}R avg
                      </span>
                    )}
                    {row.total_pnl !== 0 && (
                      <span className={`ml-auto text-xs font-bold tabular-nums ${row.total_pnl >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
                        ${row.total_pnl >= 0 ? '+' : ''}{row.total_pnl.toFixed(2)}
                      </span>
                    )}
                  </div>
                  {(row.wins > 0 || row.losses > 0) && (
                    <div className="mt-1 text-[10px] text-slate-400 tabular-nums">
                      <span className="text-emerald-400">{row.wins}W</span>
                      {' · '}
                      <span className="text-rose-400">{row.losses}L</span>
                      {' · '}
                      <span className="text-slate-500">{row.pending} pending</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Card>
  );
};

export default StrategyLeaderboardPanel;
