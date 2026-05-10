import React from 'react';
import {
  Lock, Unlock, AlertTriangle, RefreshCw, History, ShieldCheck,
  Activity, ToggleLeft, ToggleRight, AlertOctagon,
  TrendingUp, TrendingDown, BarChart3,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * TradingGate — RISEDUAL's single trade-authorization rule.
 *
 *   "There is only one rule, no trades until I say so."
 *      — operator order, 2026-05-10
 *
 * When OFF (default): every paper-trade and broker-order path
 * short-circuits and writes a synthetic "would-have-traded" event
 * to the Alpha Decision Log. MLs keep learning; nothing reaches
 * Mongo trade collections or the broker.
 *
 * When ON: existing trade logic resumes.
 */

export default function TradingGate() {
  const [status, setStatus] = React.useState(null);
  const [history, setHistory] = React.useState([]);
  const [synthetics, setSynthetics] = React.useState([]);
  const [pnl, setPnl] = React.useState(null);
  const [pnlDays, setPnlDays] = React.useState(1);
  const [busy, setBusy] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [confirm, setConfirm] = React.useState(null); // {target_state}

  const fetchAll = React.useCallback(async () => {
    setError(null);
    try {
      const [s, h, sy, p] = await Promise.all([
        authFetch(`${API}/admin/trading-gate/status`),
        authFetch(`${API}/admin/trading-gate/history?limit=10`),
        authFetch(`${API}/admin/trading-gate/synthetic-summary?limit=10`),
        authFetch(`${API}/admin/trading-gate/counterfactual-pnl?days=${pnlDays}`),
      ]);
      const sJ = await s.json();
      const hJ = await h.json();
      const syJ = await sy.json();
      const pJ = await p.json();
      if (!s.ok) throw new Error(sJ.detail || 'status');
      setStatus(sJ);
      setHistory(hJ.history || []);
      setSynthetics(syJ.synthetic_receipts || []);
      if (p.ok) setPnl(pJ);
    } catch (e) {
      setError(e.message);
    }
  }, [pnlDays]);

  React.useEffect(() => { fetchAll(); }, [fetchAll]);

  const requestToggle = (target) => {
    setConfirm({ target });
  };

  const doToggle = async (note) => {
    if (!confirm) return;
    setBusy('toggle');
    setError(null);
    try {
      const r = await authFetch(`${API}/admin/trading-gate/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: confirm.target, note: note || '' }),
      });
      const json = await r.json();
      if (!r.ok || !json.ok) {
        throw new Error(json.detail || json.error || 'toggle failed');
      }
      setConfirm(null);
      await fetchAll();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const enabled = !!status?.enabled;

  return (
    <div data-testid="trading-gate" className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-100 mb-1 flex items-center gap-2">
            <ShieldCheck className="text-cyan-400" size={22} />
            Operator Trading Gate
          </h2>
          <p className="text-sm text-slate-400 max-w-2xl">
            The <strong className="text-cyan-400">single rule</strong>: no
            trades happen until you say so. Affects paper AND live.
            When OFF, every blocked trade writes a synthetic ADL
            receipt so MLs keep learning from the counterfactual stream.
          </p>
        </div>
        <button
          type="button"
          data-testid="trading-gate-refresh-btn"
          onClick={fetchAll}
          className="px-3 py-1.5 rounded-md text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 flex items-center gap-1.5"
        >
          <RefreshCw size={12} />
          Refresh
        </button>
      </div>

      {error && (
        <div
          className="bg-rose-500/10 border border-rose-500/30 rounded-lg px-4 py-3 text-sm text-rose-300 flex items-start gap-2"
          data-testid="trading-gate-error"
        >
          <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
          <div>{error}</div>
        </div>
      )}

      {/* The big visual gate */}
      {status && (
        <div
          data-testid="trading-gate-card"
          className={`border-2 rounded-xl p-6 transition-colors ${
            enabled
              ? 'bg-emerald-500/10 border-emerald-500/40'
              : 'bg-rose-500/5 border-rose-500/30'
          }`}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              {enabled ? (
                <Unlock className="text-emerald-400 flex-shrink-0 mt-1" size={32} />
              ) : (
                <Lock className="text-rose-400 flex-shrink-0 mt-1" size={32} />
              )}
              <div>
                <div
                  className={`text-2xl font-bold ${enabled ? 'text-emerald-400' : 'text-rose-400'}`}
                  data-testid="trading-gate-state"
                >
                  {enabled ? 'TRADING AUTHORIZED' : 'TRADING PAUSED'}
                </div>
                <div className="text-sm text-slate-400 mt-1">
                  {enabled
                    ? 'Paper + live execution paths are open. Existing trade logic active.'
                    : 'All paper + live trade inserts are blocked. Synthetic ADL receipts are being written.'}
                </div>
                {status.note && (
                  <div className="text-xs text-slate-500 mt-2 font-mono italic">
                    note: {status.note} · by {status.by_operator || 'unknown'}
                  </div>
                )}
              </div>
            </div>
            <button
              type="button"
              data-testid="trading-gate-toggle-btn"
              disabled={busy === 'toggle'}
              onClick={() => requestToggle(!enabled)}
              className={`px-5 py-3 rounded-lg text-sm font-bold transition-colors flex items-center gap-2 ${
                enabled
                  ? 'bg-rose-500 hover:bg-rose-400 text-slate-950'
                  : 'bg-emerald-500 hover:bg-emerald-400 text-slate-950'
              } ${busy === 'toggle' ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              {enabled ? (
                <>
                  <ToggleRight size={18} />
                  PAUSE TRADING
                </>
              ) : (
                <>
                  <ToggleLeft size={18} />
                  AUTHORIZE TRADING
                </>
              )}
            </button>
          </div>
        </div>
      )}

      {/* Confirmation modal */}
      {confirm && (
        <div
          className="bg-amber-500/10 border-2 border-amber-500/40 rounded-lg p-4 space-y-3"
          data-testid="trading-gate-confirm"
        >
          <div className="flex items-start gap-2">
            <AlertOctagon size={18} className="text-amber-400 flex-shrink-0 mt-0.5" />
            <div>
              <div className="text-amber-300 font-semibold text-sm">
                Confirm: {confirm.target ? 'AUTHORIZE TRADING' : 'PAUSE TRADING'}
              </div>
              <div className="text-xs text-amber-200/80 mt-1">
                {confirm.target
                  ? 'Trades will be persisted to Mongo and (if other gates open) routed to the live broker.'
                  : 'All trade inserts will be blocked. MLs continue learning from synthetic receipts.'}
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <input
              id="trading-gate-confirm-note"
              type="text"
              data-testid="trading-gate-confirm-note"
              placeholder="Optional note for the audit log…"
              maxLength={500}
              className="flex-1 bg-slate-950 border border-amber-500/40 rounded px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-amber-400"
            />
            <button
              type="button"
              data-testid="trading-gate-confirm-yes"
              disabled={busy === 'toggle'}
              onClick={() => {
                const note = document.getElementById('trading-gate-confirm-note').value || '';
                doToggle(note);
              }}
              className={`px-4 py-2 rounded text-xs font-bold ${
                confirm.target
                  ? 'bg-emerald-500 hover:bg-emerald-400 text-slate-950'
                  : 'bg-rose-500 hover:bg-rose-400 text-slate-950'
              } ${busy === 'toggle' ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              Confirm
            </button>
            <button
              type="button"
              data-testid="trading-gate-confirm-cancel"
              onClick={() => setConfirm(null)}
              className="px-4 py-2 rounded text-xs bg-slate-800 hover:bg-slate-700 text-slate-300"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* History */}
      {history.length > 0 && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-3">
            <History size={14} /> Toggle history
          </div>
          <ul className="space-y-2" data-testid="trading-gate-history">
            {history.map((h, i) => (
              <li
                key={i}
                className="flex items-start justify-between gap-3 text-xs border-b border-slate-800/40 pb-2 last:border-b-0"
              >
                <div className="flex items-center gap-2">
                  {h.enabled ? (
                    <Unlock size={12} className="text-emerald-400" />
                  ) : (
                    <Lock size={12} className="text-rose-400" />
                  )}
                  <span className="font-mono text-slate-300">
                    {h.enabled ? 'authorized' : 'paused'}
                  </span>
                  <span className="text-slate-500">·</span>
                  <span className="text-slate-400">{h.change}</span>
                  {h.note && (
                    <>
                      <span className="text-slate-500">·</span>
                      <span className="text-slate-500 italic">{h.note}</span>
                    </>
                  )}
                </div>
                <div className="text-slate-600 font-mono text-[10px]">
                  {h.by_operator} · {h.at?.replace('T', ' ').substring(0, 19)}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Synthetic receipts */}
      {synthetics.length > 0 && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-3">
            <Activity size={14} /> Recent synthetic ADL receipts
            <span className="text-[10px] text-slate-600 font-mono">
              ({synthetics.length} would-have-traded events)
            </span>
          </div>
          <ul className="space-y-2" data-testid="trading-gate-synthetics">
            {synthetics.map((s, i) => (
              <li
                key={i}
                className="text-xs border-b border-slate-800/40 pb-2 last:border-b-0 font-mono"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-300">{s.symbol}</span>
                    <span className="text-slate-500">·</span>
                    <span className="text-amber-400">{s.lane}</span>
                    <span className="text-slate-500">·</span>
                    <span className="text-cyan-400">
                      {s.extras?.intended_action || s.decision}
                    </span>
                  </div>
                  <div className="text-slate-600 text-[10px]">
                    conf {(s.confidence ?? 0).toFixed(2)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Counterfactual P&L */}
      {pnl && (
        <div
          data-testid="trading-gate-pnl"
          className="bg-slate-900/40 border border-slate-800 rounded-lg p-4"
        >
          <div className="flex items-center justify-between gap-3 mb-3">
            <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500">
              <BarChart3 size={14} /> What would have traded
              <span className="text-[10px] text-slate-600 font-mono">
                ({pnl.window_label || `last ${pnlDays}d`})
              </span>
            </div>
            <div className="flex gap-1">
              {[1, 7, 30].map((d) => (
                <button
                  key={d}
                  type="button"
                  data-testid={`trading-gate-pnl-window-${d}`}
                  onClick={() => setPnlDays(d)}
                  className={`px-2 py-1 rounded text-[10px] font-mono ${
                    pnlDays === d
                      ? 'bg-cyan-500 text-slate-950'
                      : 'bg-slate-800 hover:bg-slate-700 text-slate-400'
                  }`}
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-baseline gap-3 mb-4">
            <div
              className={`text-3xl font-bold flex items-center gap-2 ${
                pnl.simulated_pnl_usd > 0
                  ? 'text-emerald-400'
                  : pnl.simulated_pnl_usd < 0
                    ? 'text-rose-400'
                    : 'text-slate-400'
              }`}
              data-testid="trading-gate-pnl-total"
            >
              {pnl.simulated_pnl_usd > 0 && <TrendingUp size={24} />}
              {pnl.simulated_pnl_usd < 0 && <TrendingDown size={24} />}
              {pnl.simulated_pnl_usd >= 0 ? '+' : ''}
              ${pnl.simulated_pnl_usd.toLocaleString()}
            </div>
            <div className="text-xs text-slate-500 font-mono">
              simulated · {pnl.scored_receipts}/{pnl.total_receipts} receipts scored
              {pnl.unscored_receipts > 0 && ` · ${pnl.unscored_receipts} unscoreable`}
            </div>
          </div>

          {pnl.by_symbol?.length > 0 && (
            <div className="border-t border-slate-800/50 pt-3">
              <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-2 font-mono">
                Top symbols (counterfactual)
              </div>
              <ul className="space-y-1">
                {pnl.by_symbol.slice(0, 6).map((b) => (
                  <li
                    key={b.symbol}
                    className="flex items-center justify-between text-xs font-mono py-1"
                    data-testid={`trading-gate-pnl-sym-${b.symbol}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-slate-300 w-16">{b.symbol}</span>
                      <span className="text-[10px] text-slate-600">
                        {b.n_long}L · {b.n_short}S
                      </span>
                    </div>
                    <span
                      className={
                        b.simulated_pnl_usd > 0
                          ? 'text-emerald-400'
                          : b.simulated_pnl_usd < 0
                            ? 'text-rose-400'
                            : 'text-slate-500'
                      }
                    >
                      {b.simulated_pnl_usd >= 0 ? '+' : ''}
                      ${b.simulated_pnl_usd.toLocaleString()}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {pnl.scored_receipts === 0 && (
            <div className="text-xs text-slate-500 italic mt-2">
              No scoreable receipts in this window. The MLs haven't
              produced any directional intent the gate blocked yet.
            </div>
          )}
        </div>
      )}

      <div className="text-xs text-slate-600 pt-2 border-t border-slate-800/40">
        Doctrine: <code className="text-slate-400 font-mono">
          OPERATOR_TRADING_AUTHORIZATION_ENABLED
        </code> · single source of truth for trade auth · default OFF on bootstrap.
      </div>
    </div>
  );
}
