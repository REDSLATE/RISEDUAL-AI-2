import React, { useEffect, useState } from 'react';
import ShadowDecisionDrawer from './admin/ShadowDecisionDrawer';
import TradingModeBanner from './TradingModeBanner';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * Crypto Paper Dashboard
 *
 * Live tile for the isolated crypto bot lane:
 *  - Open / closed trade counts
 *  - Total $ PnL + avg R-multiple + win rate
 *  - Active crypto_model_adaptations
 *  - 10 most recent fills
 *  - "Run Bot" / "Close Due Trades" admin actions
 *
 * Polls /api/crypto/dashboard every 15s. Never imports anything from
 * the equity dashboard, paper_trades, or stock bot UI.
 */
export default function CryptoPaperDashboard() {
  const [data, setData] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [drawerSymbol, setDrawerSymbol] = useState(null);

  async function load() {
    try {
      const res = await fetch(`${API}/api/crypto/dashboard`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    }
  }

  async function postAction(path) {
    setRunning(true);
    try {
      await fetch(`${API}${path}`, {
        method: 'POST',
        credentials: 'include',
      });
      await load();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setRunning(false);
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  if (error) {
    return (
      <div
        className="p-4 rounded-2xl bg-slate-900/40 border border-red-500/30 text-red-300 text-sm"
        data-testid="crypto-dashboard-error"
      >
        Crypto dashboard error: {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div
        className="p-4 text-sm text-slate-400"
        data-testid="crypto-dashboard-loading"
      >
        Loading crypto paper dashboard…
      </div>
    );
  }

  return (
    <div
      className="p-4 rounded-2xl bg-slate-800/40 border border-slate-700/40"
      data-testid="crypto-paper-dashboard"
    >
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-sm font-semibold text-white uppercase tracking-wider">
            Crypto Paper Bots
          </div>
          <div className="text-[11px] text-slate-400 mt-0.5">
            Isolated 24/7 crypto lane
          </div>
        </div>

        <div className="flex items-center gap-3">
          <TradingModeBanner expectedMode="paper" />
          <div className="flex gap-2">
            <button
              onClick={() => postAction('/api/crypto/paper-bot/run')}
              disabled={running}
              className="px-3 py-1.5 rounded-md bg-[#3DE8D9] hover:bg-[#7AEEE0] disabled:opacity-40 text-slate-900 text-xs font-semibold transition-colors"
              data-testid="crypto-run-bot-btn"
            >
              Run Bot
            </button>
            <button
              onClick={() => postAction('/api/crypto/paper-trades/close')}
              disabled={running}
              className="px-3 py-1.5 rounded-md bg-slate-700/60 hover:bg-slate-600/60 border border-slate-600/40 disabled:opacity-40 text-slate-200 text-xs font-medium transition-colors"
              data-testid="crypto-close-trades-btn"
            >
              Close Due Trades
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Stat
          label="Open"
          value={data.open_trades}
          testId="crypto-stat-open"
        />
        <Stat
          label="Closed"
          value={data.closed_trades}
          testId="crypto-stat-closed"
        />
        <Stat
          label="PnL"
          value={`$${Number(data.total_pnl || 0).toFixed(2)}`}
          tone={Number(data.total_pnl || 0) >= 0 ? 'pos' : 'neg'}
          testId="crypto-stat-pnl"
        />
        <Stat
          label="Win Rate"
          value={`${(Number(data.win_rate || 0) * 100).toFixed(1)}%`}
          testId="crypto-stat-winrate"
        />
      </div>

      <div className="mb-4">
        <div className="text-[11px] text-slate-400 mb-2 uppercase tracking-wider">
          Active Crypto Adaptations
        </div>
        {data.active_adaptations?.length ? (
          <div
            className="space-y-2"
            data-testid="crypto-adaptations-list"
          >
            {data.active_adaptations.map((a, i) => (
              <div
                key={`${a.failure_code}-${a.regime}-${i}`}
                className="text-xs p-2 rounded bg-slate-900/50 border border-slate-700/30"
              >
                <span className="text-amber-300">{a.failure_code}</span>{' '}
                <span className="text-slate-500">under</span>{' '}
                <span className="text-[#3DE8D9]">{a.regime}</span>{' '}
                <span className="text-slate-500">×{a.factor}</span>
                {a.evidence_count != null && (
                  <span className="text-slate-500 ml-2">
                    (evidence={a.evidence_count})
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div
            className="text-xs text-slate-500 italic"
            data-testid="crypto-adaptations-empty"
          >
            No active adaptations yet.
          </div>
        )}
      </div>

      <div>
        <div className="text-[11px] text-slate-400 mb-2 uppercase tracking-wider">
          Recent Crypto Paper Trades
        </div>
        <div
          className="space-y-1.5"
          data-testid="crypto-recent-trades-list"
        >
          {data.recent_trades?.map((t) => (
            <button
              key={t.trade_id}
              type="button"
              onClick={() => setDrawerSymbol(drawerSymbol === t.symbol ? null : t.symbol)}
              className={`w-full text-left text-xs p-2 rounded bg-slate-900/40 border ${drawerSymbol === t.symbol ? 'border-[#3DE8D9]/40' : 'border-slate-700/30'} hover:border-[#3DE8D9]/40 flex justify-between items-center transition-colors`}
              data-testid={`crypto-trade-row-${t.symbol}`}
            >
              <div>
                <span className="font-medium text-white">{t.symbol}</span>{' '}
                <span
                  className={
                    t.direction === 'LONG'
                      ? 'text-emerald-400'
                      : 'text-red-400'
                  }
                >
                  {t.direction}
                </span>{' '}
                <span className="text-slate-500">{t.status}</span>
                {t.confidence != null && (
                  <span className="text-slate-500 ml-2">
                    conf={Number(t.confidence).toFixed(2)}
                  </span>
                )}
              </div>
              <div
                className={`font-mono ${
                  Number(t.pnl || 0) >= 0
                    ? 'text-emerald-400'
                    : 'text-red-400'
                }`}
              >
                {t.pnl !== undefined && t.pnl !== null
                  ? `$${Number(t.pnl).toFixed(2)}`
                  : 'open'}
              </div>
            </button>
          ))}
          {!data.recent_trades?.length && (
            <div className="text-xs text-slate-500 italic">
              No fills yet. Hit "Run Bot" to seed the first signals.
            </div>
          )}
        </div>
        {drawerSymbol && (
          <ShadowDecisionDrawer
            symbol={drawerSymbol}
            botId="crypto_fleet"
            onClose={() => setDrawerSymbol(null)}
          />
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, tone, testId }) {
  const valueClass =
    tone === 'pos'
      ? 'text-emerald-400'
      : tone === 'neg'
      ? 'text-red-400'
      : 'text-white';
  return (
    <div
      className="p-3 rounded-lg bg-slate-900/50 border border-slate-700/30"
      data-testid={testId}
    >
      <div className="text-[10px] text-slate-400 uppercase tracking-wider">{label}</div>
      <div className={`text-lg font-semibold font-mono mt-1 ${valueClass}`}>{value}</div>
    </div>
  );
}
