import React, { useEffect, useState, useCallback } from 'react';
import { Activity, AlertTriangle, Pause } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Live Spread Watch · cross-asset liquidity-stress monitor.
 *
 * Polls /api/admin/spread-watch every 15s. Renders one row per
 * symbol across BOTH lanes (crypto via Kraken, equity via Alpaca)
 * with bid/ask/spread + a "WIDE" pill when spread_bps exceeds the
 * stress threshold (defaults to 25bps server-side).
 *
 * Key design choices:
 * - Wide post-close equity spreads are NOT flagged as stress (the
 *   server uses market_session=rth to gate the equity "stressed"
 *   bool). The chip just shows "AH" beside the spread instead.
 * - Crypto is 24/7 so wide crypto spreads are ALWAYS flagged.
 * - Sudden simultaneous widening on BOTH lanes = the macro alarm
 *   the operator wants. The header card highlights when ANY row
 *   is stressed.
 */
const LiveSpreadWatchTile = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const fetchSnapshot = useCallback(async () => {
    try {
      const r = await authFetch(`${API}/admin/spread-watch`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError(null);
    } catch (e) {
      setError(e.message || 'fetch failed');
    }
  }, []);

  useEffect(() => {
    fetchSnapshot();
    const id = setInterval(fetchSnapshot, 15_000);
    return () => clearInterval(id);
  }, [fetchSnapshot]);

  if (error) {
    return (
      <div
        className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-300"
        data-testid="spread-watch-error"
      >
        Spread Watch error: {error}
      </div>
    );
  }

  if (!data) return null;

  const { rows = [], stressed_count, market_session, stress_threshold_bps } = data;

  // Header status — emerald when calm, amber when any row is stressed.
  const stressed = stressed_count > 0;
  const headerBorder = stressed ? 'border-amber-500/40' : 'border-slate-700/60';
  const headerBg = stressed ? 'bg-amber-500/5' : 'bg-slate-800/30';
  const headerText = stressed ? 'text-amber-300' : 'text-slate-300';
  const HeaderIcon = stressed ? AlertTriangle : Activity;
  const headerLabel = stressed
    ? `${stressed_count} STRESSED`
    : 'CALM';

  const sessionLabel = {
    rth: 'RTH',
    pre: 'PRE',
    post: 'POST',
    closed: 'CLOSED',
  }[market_session] || market_session?.toUpperCase() || '?';

  return (
    <div
      data-testid="spread-watch-tile"
      className={`rounded-xl border ${headerBorder} ${headerBg} p-3`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wider">
        <div className={`flex items-center gap-2 ${headerText}`}>
          {stressed ? <AlertTriangle className="h-3.5 w-3.5" /> : <Activity className="h-3.5 w-3.5" />}
          Live Spread Watch
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="spread-watch-session"
            className="rounded-full border border-slate-700/60 bg-slate-800/60 px-2 py-0.5 text-[10px] font-semibold normal-case text-slate-400"
          >
            equity · {sessionLabel}
          </span>
          <span className={`${headerText} font-semibold`} data-testid="spread-watch-status">
            {headerLabel}
          </span>
          <span className="text-slate-500 text-[10px]">
            · threshold {stress_threshold_bps}bps
          </span>
          <HeaderIcon className={`h-3.5 w-3.5 ${headerText}`} />
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs" data-testid="spread-watch-table">
          <thead className="text-left text-[10px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="py-1 pr-3">Lane</th>
              <th className="py-1 pr-3">Symbol</th>
              <th className="py-1 pr-3 text-right">Bid</th>
              <th className="py-1 pr-3 text-right">Ask</th>
              <th className="py-1 pr-3 text-right">Last</th>
              <th className="py-1 pr-3 text-right">Spread</th>
              <th className="py-1 pr-3">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr data-testid="spread-watch-empty">
                <td colSpan="7" className="py-4 text-center text-slate-500">
                  No quotes available — provider hop failed.
                </td>
              </tr>
            )}
            {rows.map((r) => {
              const spread = r.spread_bps;
              const isStressed = r.stressed;
              const rowBorder = isStressed
                ? 'border-amber-500/30 bg-amber-500/5'
                : 'border-slate-800/60';
              const spreadDisplay = spread === null || spread === undefined
                ? (r.lane === 'equity' && market_session !== 'rth' ? 'AH' : '—')
                : `${spread.toFixed(2)} bps`;
              return (
                <tr
                  key={`${r.lane}:${r.symbol}`}
                  data-testid={`spread-watch-row-${r.symbol}`}
                  className={`border-t ${rowBorder}`}
                >
                  <td className="py-1.5 pr-3">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${r.lane === 'crypto' ? 'bg-cyan-500/10 text-cyan-300' : 'bg-emerald-500/10 text-emerald-300'}`}>
                      {r.lane}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 font-semibold text-slate-100">
                    {r.symbol}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-300">
                    {r.bid != null ? r.bid : '—'}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-300">
                    {r.ask != null ? r.ask : '—'}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-slate-300">
                    {r.last != null ? r.last : '—'}
                  </td>
                  <td className={`py-1.5 pr-3 text-right tabular-nums font-semibold ${isStressed ? 'text-amber-300' : 'text-slate-200'}`}>
                    {spreadDisplay}
                    {isStressed && (
                      <span
                        data-testid={`spread-watch-wide-pill-${r.symbol}`}
                        className="ml-1 rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[9px] font-bold text-amber-300"
                      >
                        WIDE
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 text-[10px] text-slate-500">
                    {r.source}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default LiveSpreadWatchTile;
